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
