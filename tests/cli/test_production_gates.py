from __future__ import annotations

import json

from scripts import (
    cli_postgres_evidence,
    cli_provider_mission_smoke,
    cli_sse_recovery_evidence,
    cli_tty_evidence,
)


def test_provider_mission_smoke_uses_canonical_edit_permission(monkeypatch, tmp_path):
    captured = {}

    def fake_execute_objective(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop after argument capture")

    monkeypatch.setenv("AGENTHUB_CLI_MODEL_API_KEY", "test-secret")
    monkeypatch.setenv(
        "AGENTHUB_CLI_PROVIDER_MISSION_OUTPUT", str(tmp_path / "mission.json")
    )
    monkeypatch.setattr(cli_provider_mission_smoke, "execute_objective", fake_execute_objective)

    assert cli_provider_mission_smoke.main() == 1
    assert captured["tool_permission_mode"] == "edit"


def test_postgres_gate_is_honest_without_database(tmp_path, monkeypatch):
    output = tmp_path / "postgres.json"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AGENTHUB_POSTGRES_EVIDENCE_OUTPUT", str(output))

    assert cli_postgres_evidence.main() == 0
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["status"] == "SKIP"
    assert record["scope"] == "postgres-listener"
    assert record["runId"].startswith("run-")


def test_sse_gate_requires_real_configuration(tmp_path, monkeypatch):
    output = tmp_path / "sse.json"
    for name in (
        "AGENTHUB_CLI_BASE_URL",
        "AGENTHUB_CLI_AUTH_TOKEN",
        "AGENTHUB_CLI_SSE_MISSION_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENTHUB_SSE_EVIDENCE_OUTPUT", str(output))

    assert cli_sse_recovery_evidence.main() == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "SKIP"


def test_tty_gate_skips_when_process_is_not_a_tty(tmp_path, monkeypatch):
    output = tmp_path / "tty.json"
    monkeypatch.setenv("AGENTHUB_TTY_EVIDENCE_OUTPUT", str(output))
    monkeypatch.setattr(cli_tty_evidence.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli_tty_evidence.sys.stdout, "isatty", lambda: False)

    assert cli_tty_evidence.main() == 0
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["status"] == "SKIP"
    assert record["errorType"] == "physical_tty_required"


def test_tty_gate_passes_at_supported_width_when_terminal_is_attached(tmp_path, monkeypatch):
    output = tmp_path / "tty-pass.json"
    monkeypatch.setenv("AGENTHUB_TTY_EVIDENCE_OUTPUT", str(output))
    monkeypatch.setenv("AGENTHUB_CLI_TTY_WIDTH", "40")
    monkeypatch.setattr(cli_tty_evidence.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli_tty_evidence.sys.stdout, "isatty", lambda: True)

    assert cli_tty_evidence.main() == 0
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["status"] == "PASS"
    assert record["width"] == 40
    assert record["ansiRendered"] is True
