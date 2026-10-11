"""Publish only a digest of private state, and restore only its admitted anchor."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from app.services.harness_checkpoint import (
    HarnessCheckpoint,
    HarnessEvent,
    HarnessEventType,
    build_tool_idempotency_key,
)
from app.services.harness_types import HarnessRequest, HarnessResumeInput
from app.services.model_contract import ToolResult
from app.services.recovery_image import ResumeImage, ResumeImageError, capture_image
from app.services.recovery_store import ResumeImageStore
from app.services.runner_checkpoint import _checkpoint_id
from app.services.workspace_fingerprint import workspace_revision


def recovery_context_digest(code: str, material: Mapping[str, Any]) -> str:
    raw = json.dumps({"code": code, "context": dict(material)}, ensure_ascii=True,
                     sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class RecoveryCheckpointJournal:
    """Save candidate first, publish digest second, prune only after admission."""

    def __init__(self, delegate: Any, store: ResumeImageStore, *, workspace: Path,
                 context_material: dict[str, Any], base_sequence: int = 0,
                 tools: Mapping[str, Any] | None = None, guidance_model: Any = None) -> None:
        self.delegate = delegate
        self.store = store
        self.workspace = workspace.resolve()
        self.material = context_material
        self.tools = tools or {}
        self.guidance_model = guidance_model
        self.sequence = base_sequence
        self.request: HarnessRequest | None = None
        # Every phase is meaningful for recovery, including an in-flight model.
        delegate._slim_identical = False
        delegate._uploaded_count = base_sequence

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def bind_request(self, request: HarnessRequest) -> None:
        if request.cwd is None or request.cwd.resolve() != self.workspace:
            raise ResumeImageError("execution workspace differs from recovery binding")
        self.request = request

    async def record(self, checkpoint: HarnessCheckpoint, event: HarnessEvent) -> None:
        if self.request is None or checkpoint.execution is None:
            raise ResumeImageError("private journal request is not bound")
        sequence = self.sequence + 1
        image = capture_image(self.request, checkpoint,
            checkpoint_id=_checkpoint_id(checkpoint.execution, sequence), sequence=sequence,
            workspace_revision=await asyncio.to_thread(workspace_revision, self.workspace),
            context_manifest_digest=recovery_context_digest(self.request.code, self.material),
            context_material=self.material,
            guidance_state=self.guidance_model.snapshot_guidance() if self.guidance_model is not None else None)
        digest = self.store.save(image)
        action = dict(checkpoint.next_action or {})
        action["resumeImageDigest"] = digest
        key = checkpoint.idempotency_key
        if checkpoint.reserved_call_id is not None:
            call = checkpoint.pending_tool_calls[0]
            tool = self.tools.get(call.name)
            if tool is None:
                raise ResumeImageError("pending tool has no recovery binding")
            arguments = tool.validate_arguments(call.arguments)
            key = build_tool_idempotency_key(checkpoint.execution, call.name, arguments)
            action["argumentsDigest"] = _arguments_digest(arguments)
        public = replace(checkpoint, resume_protocol_version=2, next_action=action,
                         idempotency_key=key,
                         workspace_revision=image.workspace_revision,
                         context_manifest_digest=image.context_manifest_digest)
        await self.delegate.record(public, event)
        self.sequence = sequence
        self.store.prune_before(image)


def _arguments_digest(arguments: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(arguments), sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":")).encode()).hexdigest()


def _receipt_duration(result: Mapping[str, Any]) -> float:
    duration = result.get("duration_ms")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
        raise ResumeImageError("completed receipt has no trustworthy duration")
    return duration / 1000


def _recovered_feedback(result: Mapping[str, Any], call: Any, image: ResumeImage, policy: Any) -> ToolResult:
    if result.get("success") is not True:
        raise ResumeImageError("completed receipt has no explicit successful gateway outcome")
    content = result.get("result", result.get("content", ""))
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
    feedback = ToolResult(call.id, call.name, True, content)
    if policy is not None:
        from app.services.tool_feedback import apply_tool_feedback
        prior_results = [ToolResult(**value.model_dump()) for value in image.tool_results]
        feedback = apply_tool_feedback(feedback, prior_results, policy)
    return feedback


def _check_anchor(image: ResumeImage, anchor: Mapping[str, Any], *,
                  code: str, workspace: Path, material: Mapping[str, Any]) -> None:
    pairs = {"id": image.checkpoint_id, "sequence": image.sequence,
             "missionId": image.mission_id, "workUnitId": image.work_unit_id,
             "attempt": image.attempt, "phase": image.phase,
             "iteration": image.iteration, "toolCalls": image.tool_calls,
             "promptTokens": image.usage.prompt_tokens, "completionTokens": image.usage.completion_tokens,
             "modelCost": image.usage.cost, "terminal": image.terminal,
             "workspaceRevision": image.workspace_revision,
             "contextManifestDigest": image.context_manifest_digest}
    if any(anchor.get(key) != value for key, value in pairs.items()):
        raise ResumeImageError("private image and admitted checkpoint do not agree")
    if image.code != code or image.cwd != str(workspace.resolve()):
        raise ResumeImageError("compiled request or workspace differs from checkpoint")
    if image.context_manifest_digest != recovery_context_digest(code, material):
        raise ResumeImageError("model, tools or compiled context changed since checkpoint")
    if image.phase in {HarnessEventType.MODEL_STARTED.value,
                       HarnessEventType.EXECUTION_FAILED.value,
                       HarnessEventType.BUDGET_EXHAUSTED.value}:
        raise ResumeImageError("checkpoint has an indeterminate model call or terminal failure")


def _reconcile_started(image: ResumeImage, anchor: Mapping[str, Any], receipt_store: Any,
                       tools: Mapping[str, Any], current_revision: str, feedback_policy: Any = None) -> ResumeImage:
    if not image.pending_tool_calls or image.reserved_call_id != image.pending_tool_calls[0].id:
        raise ResumeImageError("started tool checkpoint lacks its complete pending call")
    call = image.pending_tool_calls[0]
    if not call.arguments_complete:
        raise ResumeImageError("incomplete pending call cannot inherit a successful receipt")
    tool = tools.get(call.name)
    if tool is None:
        raise ResumeImageError("pending tool is no longer granted")
    arguments = tool.validate_arguments(call.arguments)
    from app.services.harness_checkpoint import HarnessExecutionContext
    execution = HarnessExecutionContext(image.mission_id, image.work_unit_id, image.attempt)
    key = build_tool_idempotency_key(execution, call.name, arguments)
    action = anchor.get("nextAction")
    if not isinstance(action, Mapping) or any(action.get(field) != value for field, value in {
        "toolName": call.name, "callId": call.id, "argumentsDigest": _arguments_digest(arguments),
    }.items()) or anchor.get("idempotencyKey") != key:
        raise ResumeImageError("pending tool action or receipt key differs from admitted checkpoint")
    decision = receipt_store.replay_decision(key, strict=True)
    if decision == "execute":
        if image.workspace_revision != current_revision:
            raise ResumeImageError("workspace changed before pending tool execution")
        return image
    if decision != "already_succeeded":
        raise ResumeImageError("ambiguous or failed tool receipt forbids replay")
    recovered = receipt_store.recover_result(key, tool_name=call.name)
    if recovered.post_workspace_revision != current_revision:
        raise ResumeImageError("workspace changed after completed tool receipt")
    result = recovered.result
    tool_result = _recovered_feedback(result, call, image, feedback_policy)
    from app.services.recovery_image import SavedResult
    image = image.model_copy(update={"tool_results": [*image.tool_results, SavedResult(**tool_result.__dict__)],
                                     "pending_tool_calls": image.pending_tool_calls[1:],
                                     "elapsed_seconds": image.elapsed_seconds + _receipt_duration(result),
                                     "reserved_call_id": None, "workspace_revision": current_revision})
    return image


def restore_resume(store: ResumeImageStore, anchor: Mapping[str, Any], *, code: str,
                   workspace: Path, context_material: Mapping[str, Any],
                   receipt_store: Any, tools: Mapping[str, Any],
                   timeout: float | None = None, language: str | None = None,
                   feedback_policy: Any = None, guidance_model: Any = None) -> HarnessResumeInput:
    action = anchor.get("nextAction")
    if anchor.get("resumeProtocolVersion") != 2 or not isinstance(action, Mapping):
        raise ResumeImageError("legacy checkpoint has no complete strict resume image")
    digest = action.get("resumeImageDigest")
    if not isinstance(digest, str):
        raise ResumeImageError("checkpoint has no private image digest")
    image = store.load(str(anchor.get("id", "")), digest)
    if (timeout is not None and image.timeout != timeout) or (language is not None and image.language != language):
        raise ResumeImageError("execution timeout or language changed since checkpoint")
    _check_anchor(image, anchor, code=code, workspace=workspace, material=context_material)
    revision = workspace_revision(workspace)
    if image.phase == HarnessEventType.TOOL_STARTED.value:
        image = _reconcile_started(image, anchor, receipt_store, tools, revision, feedback_policy)
    elif image.workspace_revision != revision:
        raise ResumeImageError("workspace revision changed since checkpoint")
    # A completed tool round needs the next model turn, not an empty terminal answer.
    if image.phase in {HarnessEventType.TOOL_STARTED.value, HarnessEventType.TOOL_COMPLETED.value} and not image.pending_tool_calls:
        image = image.model_copy(update={"response_content": None})
    if guidance_model is not None:
        guidance_model.restore_guidance(image.guidance_state)
    elif image.guidance_state is not None:
        raise ResumeImageError("saved guidance has no bound recovery model")
    return image.resume_input()
