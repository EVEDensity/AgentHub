"""Publish executor bytes under the current Runner lease, then complete once."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.services.execution_artifacts import HarnessArtifact, validate_bundle
from app.services.runner_protocols import RunnerExecutionError, RunnerRunResult
from app.services.runner_supervision import (
    AttemptFence,
    RunnerTerminalAckUncertainError,
)


class ArtifactReportingError(RunnerExecutionError):
    pass


@dataclass
class AttemptProgress:
    reporting: bool = False
    flushed: bool = False


async def report_result(control, publisher, result, *, mission_id, work_unit_id,
                        runner_id, lease, artifact_kind, media_type, fence: AttemptFence) -> RunnerRunResult:
    candidates = result.artifacts
    if not candidates:
        content = result.sandbox.stdout.encode()
        digest = hashlib.sha256(content).hexdigest()
        candidates = (HarnessArtifact(f"artifact-{work_unit_id}-{lease.attempt}-{digest[:32]}",
                                      artifact_kind, media_type, content),)
    try:
        fence.ensure_active()
        validate_bundle(candidates)
        published = []
        refs = []
        for candidate in candidates:
            fence.ensure_active()
            artifact = await publisher.publish_bytes(candidate.content)
            if artifact.digest.lower() != candidate.digest or artifact.size_bytes != len(candidate.content):
                raise ValueError("published Artifact facts differ from candidate bytes")
            fence.ensure_active()
            await control.register_artifact(
                mission_id, work_unit_id, runner_id=runner_id, lease_id=lease.lease_id,
                artifact=artifact, artifact_id=candidate.id, kind=candidate.kind, media_type=candidate.media_type,
            )
            published.append(artifact)
            refs.append({"id": candidate.id, "digest": artifact.digest})
        fence.dispatch_terminal("complete")
        try:
            completed = await control.complete_work_unit(
                mission_id, work_unit_id, runner_id=runner_id, lease_id=lease.lease_id, artifact_refs=refs,
            )
        except Exception as exc:
            raise RunnerTerminalAckUncertainError(
                "completion has no confirmed ACK; Mission Control state is unknown"
            ) from exc
        return RunnerRunResult(True, completed, published[-1], artifacts=tuple(published))
    except RunnerTerminalAckUncertainError:
        raise
    except Exception as exc:
        raise ArtifactReportingError(f"artifact reporting failed for WorkUnit {work_unit_id}") from exc
