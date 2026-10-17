"""Opt-in repository Harness wrapper; not registered by default entry points."""
from __future__ import annotations

import asyncio
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

from app.services.harness_types import HarnessError, HarnessPort, HarnessRequest, HarnessResult
from app.services.tools.sandbox_executor import SandboxResult
from .policy import TrustedTestParameters, policy_configuration_digest
from .producer import ProductionContext, TrustedLocalTestProducer
from .profile import FrozenTestProfile, validate_profile
from .snapshot import FrozenSnapshot, _outside, capture_candidate, materialize_candidate, verify_snapshot


class TrustedRepositoryHarness:
    """Execute a candidate copy and independently run frozen operator tests.

    The operator owns snapshot/profile lifetimes and injects an explicit Contract
    policy. Resume is unsupported; callers must not register this as the default
    Chat or CLI executor. Local same-user execution is not a security sandbox.
    """
    def __init__(self, *, snapshot: FrozenSnapshot, profile: FrozenTestProfile,
                 producer: TrustedLocalTestProducer, scratch_root: Path,
                 harness_factory: Callable[[Path], HarnessPort], policy: TrustedTestParameters,
                 contract_id: str, contract_version: int, configuration_digest: str,
                 work_unit_kinds: tuple[str, ...] = ('desktop.task',)):
        self.snapshot, self.profile, self.producer = snapshot, profile, producer
        self.scratch_root = scratch_root.resolve(strict=True)
        self.harness_factory, self.policy = harness_factory, policy
        self.contract_id, self.contract_version = contract_id, contract_version
        self.configuration_digest = configuration_digest
        self.work_unit_kinds = work_unit_kinds
        self._validate()

    def _validate(self) -> None:
        verify_snapshot(self.snapshot)
        validate_profile(self.profile)
        policy = self.policy
        observed = (self.snapshot.digest, self.profile.digest, self.producer.producer_id, self.producer.key_id, self.profile.execution_mode)
        expected = (policy.expectedInputSnapshotDigest, policy.testProfileDigest, policy.producerId, policy.keyId, policy.executionMode)
        if observed != expected:
            raise HarnessError('trusted test policy does not match operator snapshot/profile/producer')
        configuration = {'evaluator': 'trusted-test-run.v1', 'workUnitKinds': list(self.work_unit_kinds),
                         **policy.model_dump(mode='json')}
        if policy_configuration_digest(configuration) != self.configuration_digest:
            raise HarnessError('trusted test Contract configuration digest does not match')

    async def execute(self, request: HarnessRequest) -> HarnessResult:
        if request.resume is not None or request.execution is None:
            raise HarnessError('trusted repository execution requires an attempt and does not support resume')
        if request.timeout <= 0:
            raise HarnessError('trusted repository execution deadline must be positive')
        self._validate()
        started = time.monotonic()
        async with asyncio.timeout(request.timeout):
            with tempfile.TemporaryDirectory(prefix='candidate-', dir=self.scratch_root) as directory:
                stage = Path(directory)
                _outside(self.snapshot.root, stage)
                _outside(self.profile.suite.root, stage)
                if request.cwd is not None:
                    _outside(request.cwd.resolve(), stage)
                candidate = materialize_candidate(self.snapshot, stage / 'workspace')
                native = self.harness_factory(candidate)
                result = await native.execute(replace(request, cwd=candidate, timeout=self._remaining(request, started)))
                if not result.sandbox.success:
                    return result
                patch = capture_candidate(self.snapshot, candidate)
                execution = request.execution
                context = ProductionContext(execution.mission_id, execution.work_unit_id, execution.attempt,
                                            self.snapshot, patch, self.profile, self.contract_id,
                                            self.contract_version, self.configuration_digest)
                self._remaining(request, started)
                bundle = await self.producer.produce(context)
                self._remaining(request, started)
                sandbox = SandboxResult(success=True, stdout=f'Candidate test bundle prepared; tests: {bundle.conclusion}. Independent verification owns acceptance.',
                                        stderr='', exit_code=0, duration_ms=int((time.monotonic() - started) * 1000), mode='trusted-local')
                return replace(result, sandbox=sandbox, artifacts=bundle.artifacts)

    @staticmethod
    def _remaining(request: HarnessRequest, started: float) -> float:
        remaining = request.timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError('trusted repository deadline expired')
        return remaining
