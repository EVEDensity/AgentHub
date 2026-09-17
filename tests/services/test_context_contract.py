from __future__ import annotations

from pathlib import Path

from app.services.context_compiler import ContextBudget, ContextCompiler
from app.services.context_store import ContextStore


def test_context_store_preserves_provenance_for_compiler(tmp_path: Path) -> None:
    store = ContextStore(tmp_path)
    store.append(
        "user",
        "continue the fix",
        source="mission",
        source_id="msg-1",
        mission_id="mis-1",
        event_id="evt-1",
    )

    manifest = ContextCompiler(tmp_path, store=store).compile(current="run tests")

    assert manifest.covered_missions == ("mis-1",)
    assert [message.role for message in manifest.messages] == ["user", "system"]
    assert manifest.messages[0].source_id == "current"
    assert "continue the fix" in manifest.render()
    record = store.records()[0]
    assert record.source_id == "msg-1"
    assert record.event_id == "evt-1"
    assert record.created_at


def test_context_compiler_has_deterministic_priority_and_omission_manifest(
    tmp_path: Path,
) -> None:
    compiler = ContextCompiler(
        tmp_path,
        budget=ContextBudget(total_chars=1100, reserved_output_chars=0),
    )
    manifest = compiler.compile(
        current="C" * 900,
        conversation="S" * 300,
        mission="M" * 50,
        project="P" * 50,
    )

    assert [source.kind for source in manifest.sources] == ["current", "conversation"]
    assert manifest.sources[1].reason == "budget truncated"
    assert manifest.omitted == (
        "conversation",
        "mission",
        "project-instructions",
    )
    assert manifest.estimated_chars == 1100

