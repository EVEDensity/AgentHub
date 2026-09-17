from __future__ import annotations

import pytest

from app.services.harness_service import HarnessResumeInput
from app.services.model_contract import ToolResult


def test_resume_input_accepts_recovered_tool_result() -> None:
    item = HarnessResumeInput(
        checkpoint_id="chk-1",
        attempt=2,
        next_action={"toolName": "file_write", "callId": "call-1"},
        recovered_tool_results=(
            ToolResult(call_id="call-1", name="file_write", success=True, content="recovered"),
        ),
        start_iteration=3,
    )
    assert item.attempt == 2
    assert item.recovered_tool_results[0].call_id == "call-1"


def test_resume_input_rejects_missing_checkpoint_or_invalid_attempt() -> None:
    with pytest.raises(ValueError):
        HarnessResumeInput(checkpoint_id="", attempt=1)
    with pytest.raises(ValueError):
        HarnessResumeInput(checkpoint_id="chk-1", attempt=0)
