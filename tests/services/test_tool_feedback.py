from __future__ import annotations

from dataclasses import asdict

import pytest

from app.services.model_contract import ToolResult
from app.services.tool_executor import ToolExecutor
from app.services.tool_feedback import ToolFeedbackPolicy, apply_tool_feedback
from app.services.tools.result_storage import ContentBudgetConfig, ResultStorage


def test_harness_scope_preserves_gateway_services_without_global_result_state() -> None:
    executor = ToolExecutor()
    services = ("permission_manager", "hook_manager", "receipt_store", "code_index")
    for name in services:
        setattr(executor, name, object())
    executor.result_storage = ResultStorage()
    executor.result_storage._used_budget = 30_001
    scoped = executor.for_harness()
    assert scoped is not executor and scoped.result_storage is None
    assert all(getattr(scoped, name) is getattr(executor, name) for name in services)
    assert executor.result_storage.used_budget == 30_001


def test_feedback_policy_snapshots_real_default_config_without_processor_counter() -> None:
    storage = ResultStorage()
    policy = ToolFeedbackPolicy.from_config(storage.config)
    storage.config.max_total_results_chars = 1
    assert asdict(policy) == {
        "max_result_chars": 10_000, "max_total_results_chars": 30_000,
        "truncation_marker": "\n... [结果已截断，超出上下文预算]",
    }
    assert ToolFeedbackPolicy.from_config(None) == policy


def test_feedback_limit_is_rebuilt_from_saved_processed_prefix_including_errors() -> None:
    policy = ToolFeedbackPolicy(10, 12, "~")
    first = apply_tool_feedback(ToolResult("1", "read", True, "abcdefgh"), (), policy)
    second = apply_tool_feedback(ToolResult("2", "read", False, "failure text"), (first,), policy)
    assert first.content == "abcdefgh" and second.content == "fail~"
    restored_prefix = tuple(ToolResult(**asdict(value)) for value in (first, second))
    third = apply_tool_feedback(ToolResult("3", "read", True, "actual result"), restored_prefix, policy)
    assert third.content == "~"
    assert second.success is False and third.success is True


def test_success_text_matches_existing_processor_semantics_at_cumulative_boundary() -> None:
    config = ContentBudgetConfig(max_result_chars=10, max_total_results_chars=25, truncation_marker="~")
    storage = ResultStorage(config)
    policy = ToolFeedbackPolicy.from_config(config)
    saved = []
    for index, content in enumerate(("a" * 20, "b" * 20, "c" * 20, "d")):
        expected = storage.process({"success": True, "result": content})["result"]
        feedback = apply_tool_feedback(ToolResult(str(index), "read", True, content), saved, policy)
        assert feedback.content == expected
        saved.append(feedback)


def test_structured_feedback_serializes_sorted_keys_before_limiting() -> None:
    policy = ToolFeedbackPolicy(12, 30, "~")
    first = ToolResult("same", "read", True, {"z": "large text", "a": 1})
    second = ToolResult("same", "read", True, {"a": 1, "z": "large text"})
    assert apply_tool_feedback(first, (), policy) == apply_tool_feedback(second, (), policy)
    assert apply_tool_feedback(first, (), policy).content == '{"a": 1, "z"~'


@pytest.mark.asyncio
async def test_harness_executor_clone_keeps_side_effect_approval_and_raw_results() -> None:
    executor = ToolExecutor()
    executor.configure(result_storage=ResultStorage(ContentBudgetConfig(max_result_chars=1)))
    scoped = executor.for_harness()
    calls = []

    async def handler(arguments):
        calls.append(arguments)
        return {"success": True, "result": "real unmodified result"}

    denied = await scoped.execute_gateway("file_write", {}, handler, idempotency_key="denied")
    assert denied["success"] is False and not calls
    granted = await scoped.execute_gateway(
        "file_write", {}, handler, idempotency_key="granted", approval_callback=lambda *_: True,
    )
    assert granted["result"] == "real unmodified result" and calls == [{}]
    assert executor.result_storage.used_budget == 0
