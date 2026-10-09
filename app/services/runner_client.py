"""HTTP adapter for lease-fenced Runner commands; Mission Control owns state."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from app.services.artifact_store_service import PublishedArtifact
from app.services.runner_protocols import RunnerControlError

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

    async def claim_work_unit(
        self,
        mission_id: str,
        *,
        runner_id: str,
        agent_id: str,
        adapter_type: str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        del runner_id
        return await self._request(
            "POST",
            f"/api/v1/missions/{mission_id}/work-unit-claims",
            json={
                "agentId": agent_id,
                "adapterType": adapter_type,
                "leaseSeconds": lease_seconds,
            },
        )

    async def claim_ready_work_unit(
        self,
        workspace_id: str,
        *,
        runner_id: str,
        agent_id: str,
        adapter_type: str,
        supported_work_unit_kinds: tuple[str, ...],
        lease_seconds: int,
        supported_capabilities: tuple[str, ...] = (),
        resume_mission_id: str | None = None,
    ) -> dict[str, Any]:
        del runner_id
        payload: dict[str, Any] = {
            "workspaceId": workspace_id,
            "agentId": agent_id,
            "adapterType": adapter_type,
            "supportedWorkUnitKinds": list(supported_work_unit_kinds),
            "leaseSeconds": lease_seconds,
        }
        if supported_capabilities:
            payload["supportedCapabilities"] = list(supported_capabilities)
        if resume_mission_id is not None:
            payload["resumeMissionId"] = resume_mission_id
        return await self._request(
            "POST", "/api/v1/missions/work-unit-claims", json=payload,
        )

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

    async def get_execution_context(
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
            (
                f"/api/v1/missions/{mission_id}/work-units/"
                f"{work_unit_id}/execution-context"
            ),
            json={"leaseId": lease_id},
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

    async def heartbeat_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        del runner_id
        return await self._request(
            "POST",
            f"/api/v1/missions/{mission_id}/work-units/{work_unit_id}/heartbeat",
            json={
                "leaseId": lease_id,
                "leaseSeconds": lease_seconds,
            },
        )

    async def record_execution_checkpoint(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        checkpoint_id: str,
        sequence: int,
        phase: str,
        iteration: int,
        tool_calls: int,
        prompt_tokens: int,
        completion_tokens: int,
        model_cost: float,
        terminal: bool,
        failure_reason: str | None,
        tool_name: str | None = None,
        tool_success: bool | None = None,
        resume_protocol_version: int | None = None,
        next_action: dict[str, object] | None = None,
        idempotency_key: str | None = None,
        workspace_revision: str | None = None,
        context_manifest_digest: str | None = None,
    ) -> dict[str, Any]:
        del runner_id
        payload: dict[str, Any] = {
            "id": checkpoint_id,
            "leaseId": lease_id,
            "sequence": sequence,
            "phase": phase,
            "iteration": iteration,
            "toolCalls": tool_calls,
            "promptTokens": prompt_tokens,
            "completionTokens": completion_tokens,
            "modelCost": model_cost,
            "terminal": terminal,
        }
        if failure_reason is not None:
            payload["failureReason"] = failure_reason
        if tool_name is not None:
            payload["toolName"] = tool_name
        if tool_success is not None:
            payload["toolSuccess"] = tool_success
        if resume_protocol_version is not None:
            payload["resumeProtocolVersion"] = resume_protocol_version
        if next_action is not None:
            payload["nextAction"] = next_action
        if idempotency_key is not None:
            payload["idempotencyKey"] = idempotency_key
        if workspace_revision is not None:
            payload["workspaceRevision"] = workspace_revision
        if context_manifest_digest is not None:
            payload["contextManifestDigest"] = context_manifest_digest
        return await self._request(
            "POST",
            f"/api/v1/missions/{mission_id}/work-units/{work_unit_id}/checkpoints",
            json=payload,
        )

    async def publish_streaming_event(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        event_id: str,
        event_type: str,
        text: str,
        attempt: int,
        tool_name: str = "",
    ) -> dict[str, Any]:
        """Publish one bounded assistant/tool stream event for a leased run."""
        del runner_id
        payload: dict[str, Any] = {
            "eventId": event_id,
            "leaseId": lease_id,
            "eventType": event_type,
            "text": text,
            "attempt": attempt,
        }
        if tool_name:
            payload["toolName"] = tool_name
        return await self._request(
            "POST",
            f"/api/v1/missions/{mission_id}/work-units/{work_unit_id}/stream-events",
            json=payload,
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
