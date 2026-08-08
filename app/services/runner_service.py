from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.services.artifact_store_service import (
    ArtifactPublisher,
    PublishedArtifact,
)
from app.services.tools.sandbox_executor import SandboxExecutor, SandboxResult


class RunnerError(RuntimeError):
    """Base error for a Runner execution attempt."""


class RunnerControlError(RunnerError):
    """Raised when Mission Control rejects or cannot complete a command."""


class RunnerExecutionError(RunnerError):
    """Raised when execution or Artifact publication cannot finish honestly."""


class MissionControlRunnerPort(Protocol):
    async def lease_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_seconds: int,
    ) -> dict[str, Any]: ...

    async def start_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
    ) -> dict[str, Any]: ...

    async def register_artifact(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        artifact: PublishedArtifact,
        artifact_id: str,
        kind: str,
        media_type: str,
    ) -> dict[str, Any]: ...

    async def complete_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        artifact_refs: list[dict[str, str]],
    ) -> dict[str, Any]: ...

    async def fail_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        reason: str,
    ) -> dict[str, Any]: ...


class SandboxPort(Protocol):
    async def execute(
        self,
        code: str,
        language: str = "python",
        timeout: float = 30.0,
        cwd: str | None = None,
    ) -> SandboxResult: ...


@dataclass(frozen=True)
class RunnerRunResult:
    success: bool
    work_unit: dict[str, Any]
    artifact: PublishedArtifact | None
    failure_reason: str | None = None


@dataclass(frozen=True)
class _LeaseContext:
    lease_id: str
    attempt: int


class MissionControlRunnerClient:
    """HTTP adapter for Runner-owned Mission Control commands."""

    def __init__(
        self,
        base_url: str,
        *,
        access_token: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._http_client = http_client

    async def lease_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        del runner_id
        return await self._request(
            "POST",
            f"/api/v1/missions/{mission_id}/work-units/{work_unit_id}/lease",
            json={"leaseSeconds": lease_seconds},
        )

    async def start_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
    ) -> dict[str, Any]:
        del runner_id
        return await self._request(
            "POST",
            f"/api/v1/missions/{mission_id}/work-units/{work_unit_id}/start",
            json={"leaseId": lease_id},
        )

    async def register_artifact(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        artifact: PublishedArtifact,
        artifact_id: str,
        kind: str,
        media_type: str,
    ) -> dict[str, Any]:
        del runner_id
        return await self._request(
            "POST",
            f"/api/v1/missions/{mission_id}/work-units/{work_unit_id}/artifacts",
            json={
                "id": artifact_id,
                "leaseId": lease_id,
                "kind": kind,
                "digest": artifact.digest,
                "contentAddress": artifact.content_address,
                "mediaType": media_type,
                "sizeBytes": artifact.size_bytes,
            },
        )

    async def complete_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        artifact_refs: list[dict[str, str]],
    ) -> dict[str, Any]:
        del runner_id
        return await self._request(
            "POST",
            f"/api/v1/missions/{mission_id}/work-units/{work_unit_id}/complete",
            json={"leaseId": lease_id, "artifactRefs": artifact_refs},
        )

    async def fail_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        reason: str,
    ) -> dict[str, Any]:
        del runner_id
        return await self._request(
            "POST",
            f"/api/v1/missions/{mission_id}/work-units/{work_unit_id}/fail",
            json={"leaseId": lease_id, "reason": reason},
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any],
    ) -> dict[str, Any]:
        headers = (
            {"Authorization": f"Bearer {self._access_token}"}
            if self._access_token
            else {}
        )
        try:
            if self._http_client is not None:
                response = await self._http_client.request(
                    method,
                    self._base_url + path,
                    headers=headers,
                    json=json,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.request(
                        method,
                        self._base_url + path,
                        headers=headers,
                        json=json,
                    )
        except httpx.HTTPError as exc:
            raise RunnerControlError(
                f"Mission Control request failed: {method} {path}"
            ) from exc
        if response.is_error:
            detail: object = response.text[:500]
            try:
                payload = response.json()
                if isinstance(payload, dict) and "detail" in payload:
                    detail = payload["detail"]
            except ValueError:
                pass
            raise RunnerControlError(
                f"Mission Control rejected {method} {path}: {detail}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RunnerControlError(
                f"Mission Control returned invalid JSON: {method} {path}"
            ) from exc
        if not isinstance(payload, dict):
            raise RunnerControlError(
                f"Mission Control returned an invalid response: {method} {path}"
            )
        return payload


class WorkUnitRunner:
    """Runs one bounded command and reports only through Mission Control."""

    def __init__(
        self,
        control: MissionControlRunnerPort,
        *,
        publisher: ArtifactPublisher,
        sandbox: SandboxPort | None = None,
        runner_id: str,
    ) -> None:
        self._control = control
        self._publisher = publisher
        self._sandbox = sandbox or SandboxExecutor()
        self._runner_id = runner_id

    async def run(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        code: str,
        language: str = "python",
        timeout: float = 30.0,
        cwd: Path | None = None,
        lease_seconds: int = 300,
        artifact_kind: str = "test-result",
        media_type: str = "text/plain",
    ) -> RunnerRunResult:
        leased = await self._control.lease_work_unit(
            mission_id,
            work_unit_id,
            runner_id=self._runner_id,
            lease_seconds=lease_seconds,
        )
        lease = _lease_context(leased)
        started = await self._control.start_work_unit(
            mission_id,
            work_unit_id,
            runner_id=self._runner_id,
            lease_id=lease.lease_id,
        )
        _assert_lease_context(started, lease)

        try:
            result = await self._sandbox.execute(
                code,
                language=language,
                timeout=timeout,
                cwd=str(cwd) if cwd is not None else None,
            )
        except Exception as exc:
            await self._fail(
                mission_id,
                work_unit_id,
                lease,
                f"sandbox execution raised: {exc}",
            )
            raise RunnerExecutionError(
                f"sandbox execution failed for WorkUnit {work_unit_id}"
            ) from exc

        if not result.success:
            reason = _execution_failure_reason(result)
            failed = await self._fail(mission_id, work_unit_id, lease, reason)
            return RunnerRunResult(
                success=False,
                work_unit=failed,
                artifact=None,
                failure_reason=reason,
            )

        try:
            published = await self._publisher.publish_bytes(result.stdout.encode())
            artifact_id = _artifact_id(work_unit_id, lease.attempt, published.digest)
            await self._control.register_artifact(
                mission_id,
                work_unit_id,
                runner_id=self._runner_id,
                lease_id=lease.lease_id,
                artifact=published,
                artifact_id=artifact_id,
                kind=artifact_kind,
                media_type=media_type,
            )
            completed = await self._control.complete_work_unit(
                mission_id,
                work_unit_id,
                runner_id=self._runner_id,
                lease_id=lease.lease_id,
                artifact_refs=[
                    {"id": artifact_id, "digest": published.digest},
                ],
            )
        except Exception as exc:
            await self._fail(
                mission_id,
                work_unit_id,
                lease,
                f"artifact reporting failed: {exc}",
            )
            raise RunnerExecutionError(
                f"artifact reporting failed for WorkUnit {work_unit_id}"
            ) from exc

        return RunnerRunResult(
            success=True,
            work_unit=completed,
            artifact=published,
        )

    async def _fail(
        self,
        mission_id: str,
        work_unit_id: str,
        lease: _LeaseContext,
        reason: str,
    ) -> dict[str, Any]:
        try:
            return await self._control.fail_work_unit(
                mission_id,
                work_unit_id,
                runner_id=self._runner_id,
                lease_id=lease.lease_id,
                reason=reason[:2000],
            )
        except Exception as exc:
            raise RunnerControlError(
                f"Mission Control could not record WorkUnit failure: {work_unit_id}"
            ) from exc


def _lease_context(payload: Mapping[str, Any]) -> _LeaseContext:
    lease = payload.get("lease")
    if not isinstance(lease, Mapping):
        raise RunnerControlError("Mission Control lease response has no lease")
    lease_id = lease.get("id")
    attempt = payload.get("attempt")
    if not isinstance(lease_id, str) or not lease_id:
        raise RunnerControlError("Mission Control lease response has no lease id")
    if not isinstance(attempt, int) or attempt < 1:
        raise RunnerControlError("Mission Control lease response has no attempt")
    return _LeaseContext(lease_id=lease_id, attempt=attempt)


def _assert_lease_context(payload: Mapping[str, Any], expected: _LeaseContext) -> None:
    actual = _lease_context(payload)
    if actual != expected:
        raise RunnerControlError("Mission Control changed the WorkUnit lease")


def _execution_failure_reason(result: SandboxResult) -> str:
    if result.error:
        return result.error
    if result.stderr:
        return result.stderr
    return f"sandbox exited with code {result.exit_code}"


def _artifact_id(work_unit_id: str, attempt: int, digest: str) -> str:
    digest_hex = digest.removeprefix("sha256:")
    return f"artifact-{work_unit_id}-{attempt}-{digest_hex[:32]}"


__all__ = [
    "MissionControlRunnerClient",
    "MissionControlRunnerPort",
    "RunnerControlError",
    "RunnerError",
    "RunnerExecutionError",
    "RunnerRunResult",
    "SandboxPort",
    "WorkUnitRunner",
]
