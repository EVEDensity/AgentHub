from pathlib import Path

import pytest

from app.services.runner.tool_approval import desktop_tool_approval
from app.services.tool_executor import ToolExecutor
from app.services.tools.policy import ToolExecutionPolicy


@pytest.mark.parametrize("mode,allowed", [("suggest", False), ("edit", True), ("auto", True)])
@pytest.mark.asyncio
async def test_gateway_honors_explicit_workspace_grant(tmp_path: Path, mode: str, allowed: bool):
    destination = tmp_path / "result.txt"

    async def write(arguments):
        destination.write_text(arguments["content"], encoding="utf-8")
        return {"success": True}

    result = await ToolExecutor().execute_gateway(
        "file_write", {"content": "verified bytes"}, handler=write, idempotency_key="",
        approval_callback=desktop_tool_approval(ToolExecutionPolicy.for_mode(mode, tmp_path)),
    )
    assert result["success"] is allowed
    assert destination.exists() is allowed


@pytest.mark.parametrize("name", ["command_execute", "network_request", "http_request", "mcp.remote.write"])
def test_auto_does_not_grant_unknown_or_remote_side_effects(tmp_path: Path, name: str):
    assert not desktop_tool_approval(ToolExecutionPolicy.for_mode("auto", tmp_path))(name, {})


def test_code_execution_requires_auto(tmp_path: Path):
    assert not desktop_tool_approval(ToolExecutionPolicy.for_mode("edit", tmp_path))("code_execute", {})
    assert desktop_tool_approval(ToolExecutionPolicy.for_mode("auto", tmp_path))("code_execute", {})


@pytest.mark.parametrize("mode,allowed", [("suggest", False), ("edit", True)])
@pytest.mark.asyncio
async def test_child_harness_inherits_parent_permission(tmp_path: Path, mode: str, allowed: bool):
    from app.services.desktop_runner_tools import build_desktop_runner_tools
    from app.services.harness_service import FunctionCall, ModelResponse

    class Model:
        async def complete(self, request, tool_results):
            if not tool_results:
                return ModelResponse(tool_calls=(FunctionCall(
                    id="child-write", name="file_write",
                    arguments={"path": "child.txt", "content": "child output", "expected_sha256": ""},
                ),))
            return ModelResponse(content=tool_results[0].content)

    class Factory:
        def build(self, tools):
            return Model()

    tools = build_desktop_runner_tools(tmp_path, model_factory=Factory(), permission_mode=mode)
    delegate = next(tool for tool in tools if tool.name == "delegate_subtask")
    await delegate.handler({"objective": "write child file"})
    assert (tmp_path / "child.txt").exists() is allowed
