import json
from pathlib import Path
from unittest.mock import patch

from scripts import cli_provider_smoke


class _Response:
    def raise_for_status(self):
        return None

    def iter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"hello"}}]}'
        yield 'data: {"choices":[{"delta":{"tool_calls":[{"id":"call-1","function":{"name":"file_read","arguments":"{\\"path\\":\\"README.md\\"}"}}]}}]}'
        yield "data: [DONE]"


class _Stream:
    def __enter__(self):
        return _Response()

    def __exit__(self, *args):
        return False


def test_provider_smoke_writes_redacted_stream_summary(tmp_path: Path, monkeypatch, capsys):
    output = tmp_path / "summary.json"
    monkeypatch.setenv("AGENTHUB_CLI_MODEL_API_KEY", "test-key")
    monkeypatch.setenv("AGENTHUB_CLI_PROVIDER", "deepseek")
    monkeypatch.setenv("AGENTHUB_CLI_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("AGENTHUB_CLI_PROVIDER_TOOL_SMOKE", "1")
    monkeypatch.setenv("AGENTHUB_CLI_PROVIDER_SMOKE_OUTPUT", str(output))
    with patch("httpx.stream", return_value=_Stream()):
        assert cli_provider_smoke.main() == 0
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["status"] == "PASS"
    assert record["toolCallIds"] == 1
    assert record["toolArgumentsComplete"] is True
    assert "test-key" not in capsys.readouterr().out


def test_provider_smoke_accepts_tool_argument_fragments_without_repeated_id(tmp_path: Path, monkeypatch):
    class FragmentResponse(_Response):
        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"arguments":"{\\"path\\":"}}]}}]}'
            yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"README.md\\"}"}}]}}]}'
    class FragmentStream(_Stream):
        def __enter__(self):
            return FragmentResponse()
    output = tmp_path / "fragment.json"
    monkeypatch.setenv("AGENTHUB_CLI_MODEL_API_KEY", "test-key")
    monkeypatch.setenv("AGENTHUB_CLI_PROVIDER", "deepseek")
    monkeypatch.setenv("AGENTHUB_CLI_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("AGENTHUB_CLI_PROVIDER_TOOL_SMOKE", "1")
    monkeypatch.setenv("AGENTHUB_CLI_PROVIDER_SMOKE_OUTPUT", str(output))
    with patch("httpx.stream", return_value=FragmentStream()):
        assert cli_provider_smoke.main() == 0
    assert json.loads(output.read_text(encoding="utf-8"))["toolArgumentsComplete"] is True


def test_validate_event_chain_requires_order_and_all_stages():
    ok, missing = cli_provider_smoke.validate_event_chain([
        "assistant.delta", "tool.started", "tool.output", "checkpoint.created",
        "verification.started", "verification.completed", "mission.completed",
    ])
    assert ok and not missing
    ok, missing = cli_provider_smoke.validate_event_chain(["assistant.delta", "mission.completed"])
    assert not ok and "tool.started" in missing


def test_provider_smoke_reports_http_status_and_redacted_detail(tmp_path: Path, monkeypatch, capsys):
    class ErrorResponse:
        status_code = 404
        text = '{"error":{"message":"model not found"}}'
        def json(self): return {"error": {"message": "model not found"}}
        def raise_for_status(self):
            request = httpx.Request("POST", "https://example.test")
            raise httpx.HTTPStatusError("bad", request=request, response=self)
        def iter_lines(self): return iter(())
    class ErrorStream(_Stream):
        def __enter__(self): return ErrorResponse()
    import httpx
    monkeypatch.setenv("AGENTHUB_CLI_MODEL_API_KEY", "test-key")
    monkeypatch.setenv("AGENTHUB_CLI_PROVIDER", "deepseek")
    monkeypatch.setenv("AGENTHUB_CLI_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("AGENTHUB_CLI_PROVIDER_SMOKE_OUTPUT", str(tmp_path / "error.json"))
    with patch("httpx.stream", return_value=ErrorStream()):
        assert cli_provider_smoke.main() == 1
    output = capsys.readouterr().out
    assert '"statusCode": 404' in output
    assert "model not found" in output
