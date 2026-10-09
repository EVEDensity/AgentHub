import sqlite3
import time
from pathlib import Path

import pytest

from app.services.harness_checkpoint import (
    HarnessCheckpoint,
    HarnessEventType,
    HarnessExecutionContext,
)
from app.services.harness_types import HarnessRequest
from app.services.model_contract import ModelUsage, ToolCall, ToolResult
from app.services.recovery_image import ResumeImageError, capture_image
from app.services.recovery_store import ResumeImageStore, runner_state_directory


def image(root: Path, sequence: int = 1):
    execution = HarnessExecutionContext("mission", "unit", 1)
    request = HarnessRequest("secret prompt", "python", 60.0, cwd=root, execution=execution)
    checkpoint = HarnessCheckpoint(sequence, HarnessEventType.MODEL_COMPLETED, execution,
        1, 2, ModelUsage(123, 45, .5), (ToolResult("done", "read", True, "private output"),),
        pending_tool_calls=(ToolCall("next", "write", {"text": "private args"}),),
        response_content="", elapsed_seconds=3.0, deadline_epoch=time.time() + 60)
    return capture_image(request, checkpoint, checkpoint_id=f"checkpoint-{sequence}",
                         sequence=sequence, workspace_revision="sha256:workspace",
                         context_manifest_digest="sha256:context")


def test_round_trip_preserves_complete_budgets_and_private_context(tmp_path):
    value = image(tmp_path)
    store = ResumeImageStore(tmp_path / "private.sqlite3")
    digest = store.save(value)
    loaded = ResumeImageStore(store.path).load(value.checkpoint_id, digest)
    resume = loaded.resume_input()
    assert loaded.code == "secret prompt"
    assert resume.usage == ModelUsage(123, 45, .5)
    assert resume.tool_calls == 2 and resume.elapsed_seconds == 3
    assert resume.pending_tool_calls[0].arguments == {"text": "private args"}
    assert resume.recovered_tool_results[0].content == "private output"
    assert "secret prompt" not in repr(value) and "private output" not in repr(value)


def test_unadmitted_candidate_never_replaces_requested_anchor(tmp_path):
    store = ResumeImageStore(tmp_path / "private.sqlite3")
    anchor = image(tmp_path)
    digest = store.save(anchor)
    store.save(image(tmp_path, 2))
    assert store.load(anchor.checkpoint_id, digest).sequence == 1
    with pytest.raises(ResumeImageError, match="overwritten"):
        store.save(anchor.model_copy(update={"tool_calls": 20}))
    store.prune_before(image(tmp_path, 2))
    with pytest.raises(ResumeImageError, match="missing"):
        store.load(anchor.checkpoint_id, digest)


def test_corrupt_missing_oversized_and_wrong_digest_refuse(tmp_path):
    store = ResumeImageStore(tmp_path / "private.sqlite3")
    value = image(tmp_path)
    digest = store.save(value)
    with pytest.raises(ResumeImageError, match="digest"):
        store.load(value.checkpoint_id, "sha256:wrong")
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE resume_images SET body='{}'")
    with pytest.raises(ResumeImageError, match="digest"):
        store.load(value.checkpoint_id, digest)
    with pytest.raises(ResumeImageError, match="2 MiB"):
        store.save(value.model_copy(update={"code": "x" * (2 * 1024 * 1024)}))


def test_private_state_cannot_live_inside_tool_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ResumeImageError, match="outside"):
        runner_state_directory(workspace, workspace / "state")
    assert runner_state_directory(workspace, tmp_path / "private").is_dir()
