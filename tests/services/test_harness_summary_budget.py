import pytest

from app.services.harness_service import FunctionCallingHarness, FunctionTool, HarnessRequest
from app.services.model_contract import ModelResponse, ModelUsage, ToolCall


@pytest.mark.asyncio
async def test_no_tools_summary_is_charged_to_original_token_budget():
    calls = []

    class SummaryModel:
        async def complete(self, request, tool_results, *, tools_enabled=True):
            calls.append(tools_enabled)
            if tools_enabled:
                return ModelResponse(tool_calls=(ToolCall("one", "read", {}),), usage=ModelUsage(1))
            assert len(tool_results) == 1
            return ModelResponse(content="over-budget answer", usage=ModelUsage(99))

    async def read(_arguments):
        return "real result"

    harness = FunctionCallingHarness(SummaryModel(), [FunctionTool("read", read, lambda args: args)],
                                    max_iterations=1, max_total_tokens=2)
    result = await harness.execute(HarnessRequest("task", "text", 5))
    assert not result.sandbox.success
    assert result.usage.total_tokens == 100
    assert "total-token budget exhausted" in result.sandbox.error
    assert calls == [True, False]
