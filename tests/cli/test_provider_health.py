from app.cli.provider_health import ProviderHealth, summarize_matrix


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
