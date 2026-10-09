"""Strict bounded Runner-private resume images; no provider credentials."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.harness_checkpoint import HarnessCheckpoint
from app.services.guidance_recovery import GuidanceResumeState
from app.services.harness_types import HarnessRequest, HarnessResumeInput
from app.services.model_contract import ModelUsage, ToolCall, ToolResult

MAX_RESUME_IMAGE_BYTES = 2 * 1024 * 1024


class ResumeImageError(ValueError):
    """Recovery cannot establish a complete, fenced execution state."""


class _StrictImage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SavedCall(_StrictImage):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]
    arguments_complete: bool


class SavedResult(_StrictImage):
    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    success: bool
    content: str


class SavedUsage(_StrictImage):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    cost: float = Field(ge=0, allow_inf_nan=False)


class ResumeImage(_StrictImage):
    version: int = Field(default=2, ge=2, le=2)
    checkpoint_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    mission_id: str = Field(min_length=1)
    work_unit_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    phase: str
    code: str = Field(repr=False)
    language: str
    cwd: str
    timeout: float = Field(gt=0, allow_inf_nan=False)
    workspace_revision: str = Field(min_length=1)
    context_manifest_digest: str = Field(min_length=1)
    context_material: dict[str, Any] = Field(default_factory=dict, repr=False)
    guidance_state: GuidanceResumeState | None = Field(default=None, repr=False)
    iteration: int = Field(ge=0)
    next_iteration: int = Field(ge=1)
    tool_calls: int = Field(ge=0)
    usage: SavedUsage
    tool_results: list[SavedResult] = Field(repr=False)
    pending_tool_calls: list[SavedCall] = Field(repr=False)
    response_content: str | None = Field(repr=False)
    reserved_call_id: str | None
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)
    deadline_epoch: float = Field(gt=0, allow_inf_nan=False)
    terminal: bool
    failure_reason: str | None

    def encode(self) -> tuple[str, str]:
        body = json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=True,
                          allow_nan=False, separators=(",", ":"))
        raw = body.encode("utf-8")
        if len(raw) > MAX_RESUME_IMAGE_BYTES:
            raise ResumeImageError("resume image exceeds 2 MiB")
        return body, "sha256:" + hashlib.sha256(raw).hexdigest()

    def resume_input(self) -> HarnessResumeInput:
        return HarnessResumeInput(
            checkpoint_id=self.checkpoint_id, attempt=self.attempt,
            recovered_tool_results=tuple(ToolResult(**value.model_dump()) for value in self.tool_results),
            start_iteration=self.iteration, tool_calls=self.tool_calls,
            usage=ModelUsage(**self.usage.model_dump()),
            pending_tool_calls=tuple(ToolCall(**value.model_dump()) for value in self.pending_tool_calls),
            response_content=self.response_content, reserved_call_id=self.reserved_call_id,
            checkpoint_sequence=self.sequence, phase=self.phase,
            elapsed_seconds=self.elapsed_seconds,
            next_iteration=self.next_iteration,
            deadline_epoch=self.deadline_epoch,
        )


def capture_image(request: HarnessRequest, checkpoint: HarnessCheckpoint, *,
                  checkpoint_id: str, sequence: int, workspace_revision: str,
                  context_manifest_digest: str, context_material: dict[str, Any] | None = None,
                  guidance_state: GuidanceResumeState | None = None) -> ResumeImage:
    execution = checkpoint.execution
    if execution is None or request.cwd is None:
        raise ResumeImageError("durable recovery requires execution identity and workspace")
    return ResumeImage(
        checkpoint_id=checkpoint_id, sequence=sequence,
        mission_id=execution.mission_id, work_unit_id=execution.work_unit_id,
        attempt=execution.attempt, phase=checkpoint.phase.value,
        code=request.code, language=request.language, cwd=str(request.cwd.resolve()),
        timeout=request.timeout, workspace_revision=workspace_revision,
        context_manifest_digest=context_manifest_digest,
        context_material=context_material or {},
        guidance_state=guidance_state,
        iteration=checkpoint.iteration, tool_calls=checkpoint.tool_calls,
        next_iteration=checkpoint.next_iteration,
        usage=SavedUsage(**asdict(checkpoint.usage)),
        tool_results=[SavedResult(**asdict(value)) for value in checkpoint.tool_results],
        pending_tool_calls=[SavedCall(**asdict(value)) for value in checkpoint.pending_tool_calls],
        response_content=checkpoint.response_content, reserved_call_id=checkpoint.reserved_call_id,
        elapsed_seconds=checkpoint.elapsed_seconds, terminal=checkpoint.terminal,
        deadline_epoch=checkpoint.deadline_epoch,
        failure_reason=checkpoint.failure_reason,
    )
