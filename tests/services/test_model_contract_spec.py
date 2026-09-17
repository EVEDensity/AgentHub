import asyncio

from app.services.harness_service import FunctionCallingHarness, FunctionTool, HarnessRequest
from app.services.model_contract import (
    Message,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    ToolCall,
)


def test_production_model_contract_aliases_preserve_legacy_fields():
    call = ToolCall(call_id="call-1", name="search", arguments={"q": "x"})
    assert call.id == "call-1"
    assert call.call_id == "call-1"
    assert call.arguments_complete is True

    request = ModelRequest(
        messages=(Message(role="user", content="hello", source_id="m-1"),),
        stream=True,
        tool_choice="auto",
        timeout_seconds=12.0,
    )
    assert request.messages[0].source_id == "m-1"
    assert request.timeout_seconds == 12.0

    response = ModelResponse(text="done", tool_calls=(call,))
    assert response.text == "done"
    assert response.content == "done"

    event = ModelStreamEvent(
        kind="tool_call_delta",
        tool_call=call,
        usage={"completion_tokens": 2},
    )
    assert event.tool_call is call
    assert event.usage["completion_tokens"] == 2


def test_harness_calls_model_with_canonical_request_and_tool_messages() -> None:
    class CanonicalModel:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        async def complete(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            if len(self.requests) == 1:
                return ModelResponse(
                    tool_calls=(
                        ToolCall(call_id="call-1", name="lookup", arguments={}),
                    )
                )
            return ModelResponse(text="done")

    async def lookup(_arguments):
        return "value"

    async def scenario() -> None:
        model = CanonicalModel()
        harness = FunctionCallingHarness(
            model,
            [
                FunctionTool(
                    name="lookup",
                    handler=lookup,
                    validate_arguments=dict,
                    parameters={"type": "object"},
                )
            ],
        )
        result = await harness.execute(
            HarnessRequest(code="inspect", language="text", timeout=1)
        )
        assert result.sandbox.stdout == "done"
        assert isinstance(model.requests[0], ModelRequest)
        assert model.requests[0].tools[0]["function"]["name"] == "lookup"
        assert model.requests[1].messages[-1].role == "tool"
        assert model.requests[1].messages[-1].source_id == "call-1"

    asyncio.run(scenario())
