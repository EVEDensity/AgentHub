from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from app.cli.main import _doctor_report, cmd_doctor


def test_doctor_reports_sqlite_integrity_and_redacts_credentials(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / ".agenthub" / "db"
    state.mkdir(parents=True)
    database = state / "agenthub.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sample (value TEXT)")
    connection.commit()
    connection.close()

    monkeypatch.setenv("AGENTHUB_CLI_PROVIDER", "deepseek")
    monkeypatch.setenv("AGENTHUB_CLI_MODEL_API_KEY", "sk-doctor-test-secret")
    report = _doctor_report(tmp_path)

    assert report["checks"]["sqlite"]["ok"] is True
    assert report["checks"]["sqlite"]["readWrite"] is True
    assert report["checks"]["provider_network"]["credentials"] == {"configured": True}
    assert "sk-doctor-test-secret" not in json.dumps(report)


def test_doctor_provider_probe_is_redacted_and_records_latency(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTHUB_CLI_PROVIDER", "deepseek")
    monkeypatch.setenv("AGENTHUB_CLI_MODEL_API_KEY", "sk-doctor-test-secret")
    monkeypatch.setenv("AGENTHUB_CLI_MODEL_BASE_URL", "https://provider.example/v1?token=secret")

    class Response:
        status_code = 200

    with patch("httpx.get", return_value=Response()):
        report = _doctor_report(tmp_path)

    provider = report["checks"]["provider_network"]
    assert provider["ok"] is True
    assert provider["statusCode"] == 200
    assert provider["endpoint"] == "https://provider.example"
    assert "secret" not in json.dumps(report)


def test_doctor_json_command_emits_parseable_report(tmp_path: Path, capsys) -> None:
    exit_code = cmd_doctor(tmp_path, json_mode=True)
    assert exit_code in {0, 70}
    payload = json.loads(capsys.readouterr().out)
    assert payload["schemaVersion"] == 2
    assert "checks" in payload
