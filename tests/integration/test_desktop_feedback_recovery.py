"""Real Desktop tools, private SQLite image/receipt and identical visible feedback."""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from app.services.harness_checkpoint import (
    HarnessEventType,
    HarnessExecutionContext,
    InMemoryHarnessCheckpointPort,
)
from app.services.harness_types import HarnessRequest
from app.services.model_contract import ModelResponse, ToolCall
from app.services.recovery_image import ResumeImageError
from app.services.recovery_journal import restore_resume
from app.services.recovery_store import ResumeImageStore
from app.services.runner.model import DesktopTaskHarnessFactory
from app.services.desktop_runner_tools import build_desktop_runner_tools
from app.services.tool_executor import tool_executor
from app.services.tools.policy import ToolExecutionPolicy
from app.services.tools.result_storage import ResultStorage
from tests.services.test_desktop_local_runner import desktop_claim_payload


class _Model:
    def __init__(self):
        self.feedback = ()

    async def complete(self, request, tool_results):
        if not tool_results:
            return ModelResponse(tool_calls=tuple(
                ToolCall(f"call-{index}", "file_read", {"path": f"read-{index}.txt"})
                for index in range(8)
            ))
        self.feedback = tool_results
        return ModelResponse(content="complete")


class _ModelFactory:
    recovery_manifest = {"provider": "local-feedback-test", "model": "stable-v1"}

    def __init__(self):
        self.model = _Model()

    def build(self, tools):
        return self.model


def _public_anchor(image, checkpoint):
    return {
        "id": image.checkpoint_id, "sequence": image.sequence,
        "missionId": image.mission_id, "workUnitId": image.work_unit_id,
        "attempt": image.attempt, "phase": image.phase,
        "iteration": image.iteration, "toolCalls": image.tool_calls,
        "promptTokens": image.usage.prompt_tokens,
        "completionTokens": image.usage.completion_tokens,
        "modelCost": image.usage.cost, "terminal": image.terminal,
        "workspaceRevision": image.workspace_revision,
        "contextManifestDigest": image.context_manifest_digest,
        "resumeProtocolVersion": checkpoint.resume_protocol_version,
        "nextAction": checkpoint.next_action, "idempotencyKey": checkpoint.idempotency_key,
    }


class _GapCapture(InMemoryHarnessCheckpointPort):
    def __init__(self, gap_store):
        super().__init__()
        self.gap_store = gap_store
        self.source_store = None
        self.anchor = None

    async def record(self, checkpoint, event):
        await super().record(checkpoint, event)
        if checkpoint.phase == HarnessEventType.TOOL_STARTED and checkpoint.tool_calls == 8:
            digest = checkpoint.next_action["resumeImageDigest"]
            # Copy this precise admitted anchor before normal completion prunes
            # it. The receipt is committed later by the real execution path.
            with closing(self.source_store._connect()) as connection:
                row = connection.execute("SELECT checkpoint_id FROM resume_images WHERE digest=?", (digest,)).fetchone()
            image = self.source_store.load(row["checkpoint_id"], digest)
            assert self.gap_store.save(image) == digest
            self.anchor = _public_anchor(image, checkpoint)


class _CheckpointFactory:
    def __init__(self, port):
        self.port = port

    def build(self, execution, *, lease_id):
        return self.port


async def _run_case(tmp_path, monkeypatch, structured):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for index in range(8):
        (workspace / f"read-{index}.txt").write_text("r" * 8000, encoding="utf-8")
    global_storage = ResultStorage()
    global_storage._used_budget = 40_000
    monkeypatch.setattr(tool_executor, "result_storage", global_storage)
    real_read = next(tool for tool in build_desktop_runner_tools(workspace) if tool.name == "file_read")
    calls = []

    async def read(arguments):
        calls.append(arguments["path"])
        if structured:
            return {"success": True, "result": {"z": "r" * 4000, "a": arguments["path"]}}
        return await real_read.handler(arguments)

    tool = replace(real_read, handler=read)
    model_factory = _ModelFactory()
    gap_store = ResumeImageStore(tmp_path / "gap-images.sqlite3")
    port = _GapCapture(gap_store)
    _, context = desktop_claim_payload()
    factory = DesktopTaskHarnessFactory(
        model_factory, tools=[tool], workspace_root=workspace,
        recovery_state_root=tmp_path / "private", checkpoint_factory=_CheckpointFactory(port),
        tool_policy=ToolExecutionPolicy.for_mode("edit", workspace),
    )
    harness = factory.build(context)
    port.source_store = harness._recovery.store
    execution = HarnessExecutionContext("mis-desktop-1", "wu-desktop-1", 1)
    result = await harness.execute(HarnessRequest("objective", "text", 60.0, cwd=workspace, execution=execution))
    assert result.sandbox.success and port.anchor is not None
    assert harness._tool_executor is not tool_executor
    assert harness._tool_executor.result_storage is None and global_storage.used_budget == 40_000
    return harness, model_factory.model.feedback, port, calls, workspace


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [False, True])
async def test_default_desktop_normal_and_success_receipt_gap_have_identical_visible_results(tmp_path: Path, monkeypatch, structured):
    harness, normal, port, calls, workspace = await _run_case(tmp_path, monkeypatch, structured)
    binding = harness._recovery
    restored = restore_resume(
        port.gap_store, port.anchor, code="objective", workspace=workspace,
        context_material=binding.material, receipt_store=binding.receipts,
        tools=binding.tools, feedback_policy=binding.feedback_policy,
    )
    assert tuple(restored.recovered_tool_results) == tuple(normal)
    assert len(normal[-1].content) < len(normal[0].content)
    if structured:
        assert normal[0].content.startswith('{"a":')
    assert len(calls) == 8
    raw = binding.receipts.recover_result(port.anchor["idempotencyKey"]).result["result"]
    assert len(str(raw)) > len(normal[-1].content)
    assert binding.material["toolFeedbackPolicy"] == asdict(binding.feedback_policy)
    assert restored.tool_calls == 8 and not restored.pending_tool_calls


@pytest.mark.asyncio
async def test_changed_feedback_policy_refuses_the_saved_image(tmp_path: Path, monkeypatch):
    harness, _, port, _, workspace = await _run_case(tmp_path, monkeypatch, False)
    binding = harness._recovery
    changed = {**binding.material, "toolFeedbackPolicy": {
        **binding.material["toolFeedbackPolicy"], "max_total_results_chars": 40_000,
    }}
    with pytest.raises(ResumeImageError, match="model, tools or compiled context changed"):
        restore_resume(port.gap_store, port.anchor, code="objective", workspace=workspace,
            context_material=changed, receipt_store=binding.receipts, tools=binding.tools,
            feedback_policy=binding.feedback_policy)
