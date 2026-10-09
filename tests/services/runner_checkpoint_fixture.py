"""Checkpoint acknowledgements for non-persistent Runner test controls."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app.domain import ActorRef, ExecutionCheckpoint


def checkpoint_acknowledgement(
    mission_id: str,
    work_unit_id: str,
    command: Mapping[str, Any],
    *,
    attempt: int,
) -> dict[str, Any]:
    """Echo admitted counters and recovery metadata through the actual DTO.

    These test controls do not persist checkpoints or establish recovery truth.
    Their fixed state digest is a fixture value; integration tests use the real
    Mission Control repository and journal admission instead.
    """
    required = (
        "sequence", "phase", "iteration", "tool_calls", "prompt_tokens",
        "completion_tokens", "model_cost", "terminal",
    )
    optional = (
        "failure_reason", "resume_protocol_version", "next_action",
        "idempotency_key", "workspace_revision", "context_manifest_digest",
    )
    checkpoint = ExecutionCheckpoint(
        id=command["checkpoint_id"],
        mission_id=mission_id,
        work_unit_id=work_unit_id,
        attempt=attempt,
        **{name: command[name] for name in required},
        **{name: command.get(name) for name in optional},
        state_digest="sha256:" + "a" * 64,
        created_by=ActorRef(type="service", id=command["runner_id"]),
        created_at=datetime.now(timezone.utc),
    )
    return checkpoint.model_dump(mode="json", by_alias=True)
