from __future__ import annotations

import json
from types import SimpleNamespace

from scripts import cli_provider_mission_smoke


def test_mission_smoke_records_skip_without_credentials(tmp_path, monkeypatch) -> None:
    output = tmp_path / "mission-skip.json"
    monkeypatch.delenv("AGENTHUB_CLI_MODEL_API_KEY", raising=False)
    monkeypatch.setenv("AGENTHUB_CLI_PROVIDER_MISSION_OUTPUT", str(output))

    assert cli_provider_mission_smoke.main() == 0

    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["status"] == "SKIP"
    assert evidence["scope"] == "mission-closed-loop"
    assert evidence["errorType"] == "missing_credentials"


def test_mission_smoke_pass_requires_complete_ordered_event_chain(
    tmp_path, monkeypatch
) -> None:
    output = tmp_path / "mission-pass.json"
    monkeypatch.setenv("AGENTHUB_CLI_MODEL_API_KEY", "secret-never-emitted")
    monkeypatch.setenv("AGENTHUB_CLI_PROVIDER_MISSION_OUTPUT", str(output))

    def execute_objective(**kwargs):
        for index, event_type in enumerate(
            cli_provider_mission_smoke.REQUIRED_EVENTS, start=1
        ):
            payload = {"callId": "call-1"} if event_type == "tool.started" else {}
            kwargs["on_event"](
                {
                    "schemaVersion": 1,
                    "eventId": f"evt-{index}",
                    "type": event_type,
                    "sequence": index,
                    "aggregateType": "mission",
                    "payload": payload,
                }
            )
        return SimpleNamespace(
            mission_id="mis-1",
            status="SUCCEEDED",
            artifacts=[{"id": "art-1"}],
        )

    monkeypatch.setattr(
        cli_provider_mission_smoke, "execute_objective", execute_objective
    )

    assert cli_provider_mission_smoke.main() == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["status"] == "PASS"
    assert evidence["eventOrderValid"] is True
    assert evidence["missingEvents"] == []
    assert "secret-never-emitted" not in output.read_text(encoding="utf-8")


def test_mission_smoke_rejects_duplicate_tool_execution(tmp_path, monkeypatch) -> None:
    output = tmp_path / "mission-fail.json"
    monkeypatch.setenv("AGENTHUB_CLI_MODEL_API_KEY", "secret")
    monkeypatch.setenv("AGENTHUB_CLI_PROVIDER_MISSION_OUTPUT", str(output))

    def execute_objective(**kwargs):
        for index, event_type in enumerate(
            (*cli_provider_mission_smoke.REQUIRED_EVENTS, "tool.started"), start=1
        ):
            kwargs["on_event"](
                {
                    "eventId": f"evt-{index}",
                    "type": event_type,
                    "sequence": index,
                    "aggregateType": "mission",
                    "payload": {"callId": "call-1"},
                }
            )
        return SimpleNamespace(
            mission_id="mis-1", status="SUCCEEDED", artifacts=[{}]
        )

    monkeypatch.setattr(
        cli_provider_mission_smoke, "execute_objective", execute_objective
    )

    assert cli_provider_mission_smoke.main() == 1
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["status"] == "FAIL"
    assert evidence["duplicateToolExecution"] is True

