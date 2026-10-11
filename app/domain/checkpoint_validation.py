"""Checkpoint resume and terminal invariants shared by durable domain values."""
from __future__ import annotations

import json
import re
from typing import Any


def validate_resume_fields(checkpoint: Any) -> None:
    action = checkpoint.next_action
    if action is not None:
        if len(json.dumps(action, ensure_ascii=True, separators=(",", ":"))) > 8192:
            raise ValueError("checkpoint next_action exceeds 8 KiB")
        image_digest = action.get("resumeImageDigest")
        if image_digest is not None:
            _validate_image_digest(image_digest, checkpoint.resume_protocol_version)
        elif not any(key in action for key in {"toolName", "tool_name", "callId", "call_id"}):
            raise ValueError("checkpoint next_action must identify a tool call")
        if checkpoint.resume_protocol_version is None:
            raise ValueError("next_action requires resume_protocol_version")
    if checkpoint.idempotency_key is not None and "/" not in checkpoint.idempotency_key:
        raise ValueError("idempotency_key must be execution scoped")


def _validate_image_digest(value: Any, version: int | None) -> None:
    if version != 2 or not isinstance(value, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise ValueError("resume image requires protocol v2 and a SHA-256 digest")


def validate_terminal_state(checkpoint: Any) -> None:
    terminal_phases = {"harness.execution.completed", "harness.execution.failed"}
    if checkpoint.terminal != (checkpoint.phase in terminal_phases):
        raise ValueError("checkpoint terminal flag must match its phase")
    if checkpoint.phase == "harness.execution.failed":
        if checkpoint.failure_reason is None:
            raise ValueError("failed checkpoint requires a failure reason")
    elif checkpoint.failure_reason is not None:
        raise ValueError("only a failed checkpoint can carry a failure reason")
    validate_resume_fields(checkpoint)
