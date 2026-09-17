from __future__ import annotations

from app.config import LLM_GATEWAY, NEWAPI_BASE_URL
from app.services.adapter_manager import (
    AdapterManager,
    NewAPIGatewayAdapter,
)


def test_default_mode_keeps_per_provider_adapters() -> None:
    assert LLM_GATEWAY.strip().lower() != "newapi"
    manager = AdapterManager()
    assert type(manager.get_adapter("openai")).__name__ == "OpenAIAdapter"
    assert type(manager.get_adapter("qwen")).__name__ == "QwenAdapter"
    assert type(manager.get_adapter("anthropic")).__name__ == "AnthropicAdapter"
    # local/mock adapters must never be replaced by the gateway
    assert type(manager.get_adapter("mock")).__name__ == "MockAdapter"
    assert "newapi" not in manager.adapters


def test_gateway_mode_routes_remote_providers_through_newapi(monkeypatch) -> None:
    manager = AdapterManager()
    monkeypatch.setattr("app.services.adapter_manager.LLM_GATEWAY", "newapi")
    try:
        assert isinstance(manager.get_adapter("openai"), NewAPIGatewayAdapter)
        assert isinstance(manager.get_adapter("qwen"), NewAPIGatewayAdapter)
        assert isinstance(manager.get_adapter("anthropic"), NewAPIGatewayAdapter)
        # local adapters stay local even in gateway mode
        assert type(manager.get_adapter("mock")).__name__ == "MockAdapter"
    finally:
        monkeypatch.undo()
        manager.adapters.pop("newapi", None)


def test_gateway_adapter_points_at_newapi_endpoint() -> None:
    assert NewAPIGatewayAdapter.default_base_url == NEWAPI_BASE_URL
    assert NewAPIGatewayAdapter.default_base_url.endswith("/v1")