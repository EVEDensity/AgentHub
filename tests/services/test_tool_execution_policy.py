from pathlib import Path

import pytest

from app.services.tools.policy import (
    ToolExecutionPolicy,
    ToolPermissionMode,
    resolve_tool_execution_policy,
)


def test_policy_modes_have_one_canonical_capability_mapping(tmp_path: Path) -> None:
    suggest = ToolExecutionPolicy.for_mode("suggest", tmp_path)
    edit = ToolExecutionPolicy.for_mode("edit", tmp_path)
    auto = ToolExecutionPolicy.for_mode("auto", tmp_path)

    assert suggest.mode is ToolPermissionMode.SUGGEST
    assert not suggest.allow_code_execute and not suggest.allow_shell
    assert edit.allows_workspace_write
    assert not edit.allow_code_execute and not edit.allow_shell
    assert auto.allow_code_execute and auto.allow_shell


def test_policy_resolver_prefers_explicit_mode(tmp_path: Path) -> None:
    policy = resolve_tool_execution_policy(
        tmp_path, mode="suggest", environment_value="auto"
    )
    assert policy.mode is ToolPermissionMode.SUGGEST
    assert policy.workspace_root == tmp_path.resolve()


def test_policy_rejects_unknown_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ToolExecutionPolicy.for_mode("bypass", tmp_path)
