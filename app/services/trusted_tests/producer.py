"""Signed real pytest observations for an explicit trusted-local operator profile.

The signer remains in this parent process and is never serialized, mounted or
passed to tests. Same-user hostile processes are outside this experiment's threat
model: separate OS identities and backend isolation remain production gates.
"""
from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.execution_artifacts import HarnessArtifact, validate_bundle
from .junit import JUnitError, build_test_report, junit_cases
from .models import ExecutionManifest, artifact_id, canonical_bytes, content_digest
from .process import TestProcessBackend, TrustedLocalProcessBackend
from .profile import FrozenTestProfile, test_environment, validate_profile
from .signing import sign_receipt
from .snapshot import (CandidatePatch, FrozenSnapshot, SnapshotError, _outside, _read_file,
                       materialize_candidate, reconstruct_candidate, tree_digest, verify_snapshot)

# Import pytest before adding candidate to sys.path: candidate pytest.py cannot
# replace the test driver. -I ignores cwd/PYTHONPATH/sitecustomize startup hooks.
_PYTEST_DRIVER = "import sys,pytest;sys.path.insert(0,sys.argv[1]);raise SystemExit(pytest.main(sys.argv[2:]))"


@dataclass(frozen=True)
class ProductionContext:
    mission_id: str
    work_unit_id: str
    attempt: int
    snapshot: FrozenSnapshot
    candidate: CandidatePatch
    profile: FrozenTestProfile
    contract_id: str
    contract_version: int
    configuration_digest: str


@dataclass(frozen=True)
class ProducedBundle:
    artifacts: tuple[HarnessArtifact, ...]
    conclusion: str


def _metadata(context: ProductionContext) -> dict:
    return {'schemaVersion': 1, 'contractId': context.contract_id, 'contractVersion': context.contract_version,
            'configurationDigest': context.configuration_digest, 'missionId': context.mission_id, 'workUnitId': context.work_unit_id, 'attempt': context.attempt,
            'inputSnapshotDigest': context.snapshot.digest,
            'candidateSnapshotDigest': context.candidate.candidate_snapshot_digest,
            'testProfileDigest': context.profile.digest, 'executionMode': 'trusted-local'}


def _read_junit(path: Path, maximum: int) -> tuple[bytes, list[dict[str, str]], str]:
    try:
        content, _ = _read_file(path, maximum)
        return content, junit_cases(content, max_bytes=maximum), ''
    except (OSError, SnapshotError, JUnitError) as exc:
        return b'', [], str(exc)


def _artifact(context: ProductionContext, role: str, kind: str, media_type: str, content: bytes) -> HarnessArtifact:
    return HarnessArtifact(id=artifact_id(context.work_unit_id, context.attempt, role, content_digest(content)),
                           kind=kind, media_type=media_type, content=content)


def _make_bundle(context, report, receipt, log_bytes) -> ProducedBundle:
    artifacts = (
        _artifact(context, 'patch', 'diff', 'text/x-diff', context.candidate.patch_bytes),
        _artifact(context, 'report', 'test-result', 'application/json', canonical_bytes(report)),
        _artifact(context, 'log', 'log', 'text/plain', log_bytes),
        _artifact(context, 'receipt', 'report', 'application/json', canonical_bytes(receipt)),
    )
    links = {role: {'id': artifact.id, 'digest': content_digest(artifact.content)}
             for role, artifact in zip(('patch', 'report', 'log', 'receipt'), artifacts)}
    manifest = ExecutionManifest(manifestType='trusted-test-execution', **_metadata(context), **links)
    complete = (*artifacts, _artifact(context, 'manifest', 'report', 'application/json', canonical_bytes(manifest)))
    validate_bundle(complete)
    return ProducedBundle(complete, report.conclusion)


class TrustedLocalTestProducer:
    def __init__(self, *, producer_id: str, key_id: str, private_key: Ed25519PrivateKey,
                 scratch_root: Path, process_backend: TestProcessBackend | None = None):
        self.producer_id, self.key_id = producer_id, key_id
        self._private_key = private_key
        self.scratch_root = scratch_root.resolve(strict=True)
        self.backend = process_backend or TrustedLocalProcessBackend()

    async def produce(self, context: ProductionContext, cancel_event: asyncio.Event | None = None) -> ProducedBundle:
        validate_profile(context.profile)
        verify_snapshot(context.snapshot)
        _outside(context.snapshot.root, context.profile.suite.root)
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError
        with tempfile.TemporaryDirectory(prefix='test-', dir=self.scratch_root) as directory:
            _outside(context.snapshot.root, Path(directory))
            _outside(context.profile.suite.root, Path(directory))
            return await self._produce_in(context, Path(directory), cancel_event)

    async def _produce_in(self, context, stage, cancel_event):
        profile = context.profile
        candidate = reconstruct_candidate(context.snapshot, context.candidate, stage / 'candidate')
        acceptance = materialize_candidate(profile.suite, stage / 'acceptance')
        reports = stage / 'reports'
        reports.mkdir()
        xml_path = reports / 'pytest.xml'
        config_path = reports / 'pytest.ini'
        config_path.write_text('[pytest]\n', encoding='utf-8')
        argv = (profile.argv[0], '-I', '-c', _PYTEST_DRIVER, str(candidate), *profile.argv[3:],
                '-c', str(config_path),
                '--noconftest', '-p', 'no:cacheprovider', '--junitxml=' + str(xml_path), str(acceptance))
        started = datetime.now(UTC)
        result = await self.backend.run(tuple(argv), cwd=candidate, environment=test_environment(stage),
                                        timeout_seconds=profile.timeout_seconds, max_output_bytes=profile.max_output_bytes,
                                        monitored_root=stage, max_workdir_bytes=profile.max_workdir_bytes,
                                        cancel_event=cancel_event)
        if result.cancelled:
            raise asyncio.CancelledError
        xml, cases, error = _read_junit(xml_path, profile.max_junit_bytes)
        changed = tree_digest(candidate, context.snapshot.limits) != context.candidate.candidate_snapshot_digest
        changed = changed or tree_digest(acceptance, profile.suite.limits) != profile.suite.tree_digest
        interrupted = result.timed_out or result.limit_exceeded or bool(error) or changed
        report = build_test_report(mission_id=context.mission_id, work_unit_id=context.work_unit_id,
                                   attempt=context.attempt, cases=cases, exit_code=result.exit_code, interrupted=interrupted)
        diagnostic = {'junitDigest': content_digest(xml), 'junitError': error, 'treeChanged': changed,
                      'timedOut': result.timed_out, 'limitExceeded': result.limit_exceeded,
                      'elapsedSeconds': round(result.elapsed_seconds, 6)}
        log = canonical_bytes(diagnostic) + b'\n' + result.output
        payload = {**_metadata(context), 'receiptId': artifact_id(context.work_unit_id, context.attempt, 'receipt', content_digest(log)),
                   'producerId': self.producer_id, 'keyId': self.key_id,
                   'patchDigest': content_digest(context.candidate.patch_bytes), 'reportDigest': content_digest(canonical_bytes(report)),
                   'logDigest': content_digest(log), 'exitCode': result.exit_code, 'timedOut': result.timed_out,
                   'startedAt': started.isoformat(), 'finishedAt': datetime.now(UTC).isoformat()}
        # Verify operator-controlled sources again before signing any observation.
        validate_profile(profile)
        verify_snapshot(context.snapshot)
        return _make_bundle(context, report, sign_receipt(payload, self._private_key), log)
