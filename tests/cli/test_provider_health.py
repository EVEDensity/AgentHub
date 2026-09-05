from app.cli.provider_health import ProviderHealth, ProviderHealthRegistry, SUPPORTED_PROVIDER_MATRIX, summarize_matrix


def test_provider_health_marks_failure_as_degraded_and_recovers():
    health = ProviderHealth("deepseek", "deepseek-v4-flash")
    health.record(success=False, error_kind="429")
    assert health.status == "degraded"
    assert health.to_dict()["lastError"] == "429"
    health.record(success=True, text_stream=True, tool_call=True, tool_call_stream=True, verification=True)
    assert health.status == "healthy"
    assert health.to_dict()["capabilities"]["tool_call_stream"] is True


def test_provider_matrix_is_json_projectable():
    rows = summarize_matrix([ProviderHealth("deepseek", "deepseek-v4-flash"), ProviderHealth("deepseek", "deepseek-v4-pro")])
    assert [row["model"] for row in rows] == ["deepseek-v4-flash", "deepseek-v4-pro"]


def test_duplicate_tool_call_id_is_rejected_by_health_gate():
    health = ProviderHealth("deepseek", "deepseek-v4-pro")
    assert health.accept_call("call-1") is True
    assert health.accept_call("call-1") is False
    assert health.accept_call("") is False


def test_registry_keeps_provider_model_records_independent():
    registry = ProviderHealthRegistry()
    registry.get("deepseek", "deepseek-v4-flash").record(success=False, error_kind="timeout")
    registry.get("deepseek", "deepseek-v4-pro").record(success=True, tool_call=True)
    rows = registry.snapshot()
    assert {row["model"]: row["status"] for row in rows} == {"deepseek-v4-flash": "degraded", "deepseek-v4-pro": "healthy"}


def test_declared_provider_matrix_has_stream_and_tool_capabilities():
    assert "deepseek" in SUPPORTED_PROVIDER_MATRIX
    assert "text_stream" in SUPPORTED_PROVIDER_MATRIX["deepseek"]
    assert "tool_call" in SUPPORTED_PROVIDER_MATRIX["deepseek"]


def test_registry_roundtrip(tmp_path):
    registry = ProviderHealthRegistry()
    registry.get("deepseek", "deepseek-v4-flash").record(success=False, error_kind="429")
    path = tmp_path / "provider-health.json"
    registry.save(path)
    restored = ProviderHealthRegistry.load(path)
    assert restored.snapshot()[0]["lastError"] == "429"
