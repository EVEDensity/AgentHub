from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from pydantic import ValidationError

from app.domain import ExecutionCheckpoint
from app.services.harness_checkpoint import (
    HarnessCheckpoint,
    HarnessCheckpointPort,
    HarnessError,
    HarnessEvent,
    HarnessExecutionContext,
)


@dataclass(frozen=True)
class ResumeProtocol:
    """Content-minimized execution resume contract.

    ``next_action`` contains only the tool identity and call metadata; callers
    must obtain arguments from the authoritative Harness journal.  This keeps
    durable Mission records free of prompts and tool output while still
    allowing a Runner to fence a resumed action.
    """

    next_action: Mapping[str, object] | None
    idempotency_key: str | None
    workspace_revision: str | None
    context_manifest_digest: str | None


class ResumeValidationError(ValueError):
    """Raised when a checkpoint cannot be safely resumed."""


def validate_resume_protocol(
    protocol: ResumeProtocol,
    *,
    workspace_revision: str,
    expected_idempotency_prefix: str | None = None,
) -> None:
    """Fail closed unless all execution fencing fields are present and match."""
    if not workspace_revision.strip():
        raise ResumeValidationError("current workspace revision is missing")
    if not protocol.workspace_revision:
        raise ResumeValidationError("checkpoint has no workspace revision")
    if protocol.workspace_revision != workspace_revision:
        raise ResumeValidationError("workspace revision changed since checkpoint")
    if protocol.next_action is not None:
        name = str(protocol.next_action.get("toolName") or protocol.next_action.get("tool_name") or "").strip()
        call_id = str(protocol.next_action.get("callId") or protocol.next_action.get("call_id") or "").strip()
        if not name or not call_id:
            raise ResumeValidationError("pending action requires tool name and call id")
        if not protocol.idempotency_key:
            raise ResumeValidationError("pending action requires idempotency key")
    if expected_idempotency_prefix and protocol.idempotency_key and not protocol.idempotency_key.startswith(expected_idempotency_prefix):
        raise ResumeValidationError("idempotency key does not match execution scope")



class ExecutionCheckpointControlPort(Protocol):
    """Mission Control command used by a leased Runner attempt."""

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
        next_action: Mapping[str, object] | None = None,
        idempotency_key: str | None = None,
        workspace_revision: str | None = None,
        context_manifest_digest: str | None = None,
    ) -> dict[str, Any]: ...

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
    ) -> dict[str, Any]: ...


def checkpoint_state_digest(
    checkpoint: HarnessCheckpoint,
    event: HarnessEvent,
) -> str:
    """Digest the uploaded execution state, excluding identity/sequence/phase.

    Mission Control's durable ``stateDigest`` folds in the sequence, so it
    can never repeat. This runner-side digest covers the state fields the
    port uploads minus the loop-position metadata (``phase``): two harness
    events with the same iteration/usage/tool state carry the same durable
    content, so the second one is skipped before upload (P3-4b).
    """
    material = {
        "completion_tokens": checkpoint.usage.completion_tokens,
        "failure_reason": checkpoint.failure_reason,
        "iteration": checkpoint.iteration,
        "model_cost": checkpoint.usage.cost,
        "prompt_tokens": checkpoint.usage.prompt_tokens,
        "terminal": checkpoint.terminal,
        "tool_calls": checkpoint.tool_calls,
        "tool_name": event.tool_name,
        "tool_success": event.tool_success,
    }
    return hashlib.sha256(
        json.dumps(
            material,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


class MissionControlHarnessCheckpointPort(HarnessCheckpointPort):
    """Map one Harness attempt to content-minimized durable checkpoints.

    Checkpoint slimming (P3-4b): consecutive events whose uploaded state
    digest equals the previous upload are skipped — Mission Control only
    receives state changes plus the mandatory terminal checkpoint, and the
    server-side contiguous sequence is consumed by uploads only.
    """

    def __init__(
        self,
        control: ExecutionCheckpointControlPort,
        *,
        execution: HarnessExecutionContext,
        runner_id: str,
        lease_id: str,
        slim_identical: bool = False,
    ) -> None:
        if not runner_id.strip() or not lease_id.strip():
            raise ValueError("Runner and lease ids must be non-empty")
        self._control = control
        self._execution = execution
        self._runner_id = runner_id
        self._lease_id = lease_id
        # Keep event-complete audit trails by default in the Runner factory;
        # callers that need lower write volume can explicitly enable slimming.
        self._slim_identical = slim_identical
        self._last_state_digest: str | None = None
        self._uploaded_count = 0

    def _resume_fields(self, checkpoint: HarnessCheckpoint) -> dict[str, object]:
        return {
            "resume_protocol_version": checkpoint.resume_protocol_version,
            "next_action": checkpoint.next_action,
            "idempotency_key": checkpoint.idempotency_key,
            "workspace_revision": checkpoint.workspace_revision,
            "context_manifest_digest": checkpoint.context_manifest_digest,
        }

    async def record(
        self,
        checkpoint: HarnessCheckpoint,
        event: HarnessEvent,
    ) -> None:
        if (
            checkpoint.execution != self._execution
            or event.execution != self._execution
        ):
            raise HarnessError("Harness checkpoint execution context drifted")
        if (
            checkpoint.sequence != event.sequence
            or checkpoint.phase != event.event_type
        ):
            raise HarnessError("Harness checkpoint and event identity must match")
        if (
            checkpoint.failure_reason is not None
            and len(checkpoint.failure_reason) > 2000
        ):
            raise HarnessError(
                "Harness checkpoint failure reason exceeds durable limit"
            )

        state_digest = checkpoint_state_digest(checkpoint, event)
        if (
            self._slim_identical
            and not checkpoint.terminal
            and state_digest == self._last_state_digest
        ):
            return

        self._uploaded_count += 1
        sequence = self._uploaded_count
        checkpoint_id = _checkpoint_id(self._execution, sequence)
        payload = await self._control.record_execution_checkpoint(
            self._execution.mission_id,
            self._execution.work_unit_id,
            runner_id=self._runner_id,
            lease_id=self._lease_id,
            checkpoint_id=checkpoint_id,
            sequence=sequence,
            phase=checkpoint.phase.value,
            iteration=checkpoint.iteration,
            tool_calls=checkpoint.tool_calls,
            prompt_tokens=checkpoint.usage.prompt_tokens,
            completion_tokens=checkpoint.usage.completion_tokens,
            model_cost=checkpoint.usage.cost,
            terminal=checkpoint.terminal,
            failure_reason=checkpoint.failure_reason,
            tool_name=event.tool_name,
            tool_success=event.tool_success,
            resume_protocol_version=checkpoint.resume_protocol_version,
            next_action=checkpoint.next_action,
            idempotency_key=checkpoint.idempotency_key,
            workspace_revision=checkpoint.workspace_revision,
            context_manifest_digest=checkpoint.context_manifest_digest,
        )
        try:
            durable = ExecutionCheckpoint.model_validate(payload)
        except (TypeError, ValidationError) as exc:
            raise HarnessError(
                "Mission Control returned an invalid checkpoint"
            ) from exc

        expected = {
            "id": checkpoint_id,
            "mission_id": self._execution.mission_id,
            "work_unit_id": self._execution.work_unit_id,
            "attempt": self._execution.attempt,
            "sequence": sequence,
            "phase": checkpoint.phase.value,
            "iteration": checkpoint.iteration,
            "tool_calls": checkpoint.tool_calls,
            "prompt_tokens": checkpoint.usage.prompt_tokens,
            "completion_tokens": checkpoint.usage.completion_tokens,
            "model_cost": checkpoint.usage.cost,
            "terminal": checkpoint.terminal,
            "failure_reason": checkpoint.failure_reason,
        }
        if any(getattr(durable, field) != value for field, value in expected.items()):
            raise HarnessError("Mission Control checkpoint identity drifted")
        self._last_state_digest = state_digest

    async def publish_text_delta(self, event_id: str, text: str, *, completed: bool = False) -> dict[str, Any]:
        """Publish model text without adding it to the durable checkpoint."""
        publish = getattr(self._control, "publish_streaming_event", None)
        if not callable(publish):
            return {}
        return await publish(
            self._execution.mission_id,
            self._execution.work_unit_id,
            runner_id=self._runner_id,
            lease_id=self._lease_id,
            event_id=event_id,
            event_type="harness.assistant.completed" if completed else "harness.assistant.delta",
            text=text,
            attempt=self._execution.attempt,
        )

    async def publish_tool_event(
        self, event_id: str, event_type: str, tool_name: str, text: str = ""
    ) -> dict[str, Any]:
        publish = getattr(self._control, "publish_streaming_event", None)
        if not callable(publish):
            return {}
        return await publish(
            self._execution.mission_id,
            self._execution.work_unit_id,
            runner_id=self._runner_id,
            lease_id=self._lease_id,
            event_id=event_id,
            event_type="harness.tool." + event_type,
            text=text[:4000],
            tool_name=tool_name,
            attempt=self._execution.attempt,
        )


class MissionControlHarnessCheckpointFactory:
    """Bind the control command and Runner identity to one claimed lease."""

    def __init__(
        self,
        control: ExecutionCheckpointControlPort,
        *,
        runner_id: str,
        slim_identical: bool = True,
    ) -> None:
        if not runner_id.strip():
            raise ValueError("runner_id must be non-empty")
        self._control = control
        self._runner_id = runner_id
        self._slim_identical = slim_identical

    def build(
        self,
        execution: HarnessExecutionContext,
        *,
        lease_id: str,
    ) -> HarnessCheckpointPort:
        return MissionControlHarnessCheckpointPort(
            self._control,
            execution=execution,
            runner_id=self._runner_id,
            lease_id=lease_id,
            slim_identical=self._slim_identical,
        )


def _checkpoint_id(execution: HarnessExecutionContext, sequence: int) -> str:
    material = (
        f"{execution.mission_id}\0{execution.work_unit_id}\0"
        f"{execution.attempt}\0{sequence}"
    )
    return "chk-" + hashlib.sha256(material.encode("utf-8")).hexdigest()


__all__ = [
    "ResumeProtocol",
    "ResumeValidationError",
    "validate_resume_protocol",
    "ExecutionCheckpointControlPort",
    "MissionControlHarnessCheckpointFactory",
    "MissionControlHarnessCheckpointPort",
    "checkpoint_state_digest",
]
