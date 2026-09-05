from __future__ import annotations

import json
from unittest import mock

from scripts import cli_benchmark


def test_benchmark_emits_versioned_metrics(monkeypatch, capsys):
    class Result:
        status = "SUCCEEDED"
        exit_code = 0
        wall_seconds = 1.2
        total_tokens = 9

    def fake_execute(**kwargs):
        kwargs["on_event"]({"type": "mission.created"})
        kwargs["on_event"]({"type": "tool.output", "payload": {"text": "ok"}})
        kwargs["on_text"]("hello")
        return Result()

    monkeypatch.setattr(cli_benchmark, "execute_objective", fake_execute)
    monkeypatch.setattr("sys.argv", ["cli_benchmark", "task"])
    assert cli_benchmark.main() == 0
    record = json.loads(capsys.readouterr().out)
    assert record["schemaVersion"] == 1
    assert record["events"] == 2
    assert record["firstTokenSeconds"] is not None
    assert record["firstToolFeedbackSeconds"] is not None
    assert record["recoverySucceeded"] is True


def test_benchmark_task_file_and_threshold_gate(monkeypatch, tmp_path, capsys):
    class Result:
        status = "SUCCEEDED"; exit_code = 0; wall_seconds = 2.0; total_tokens = 3
    def fake_execute(**kwargs):
        kwargs["on_event"]({"type": "mission.created"}); kwargs["on_text"]("x"); return Result()
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"schemaVersion": 1, "id": "t1", "objective": "from file", "thresholds": {"firstTokenSeconds": 0.0}}), encoding="utf-8")
    monkeypatch.setattr(cli_benchmark, "execute_objective", fake_execute)
    monkeypatch.setattr("sys.argv", ["cli_benchmark", "--task-file", str(task), "--check-thresholds"])
    assert cli_benchmark.main() == 1
    record = json.loads(capsys.readouterr().out)
    assert record["taskId"] == "t1"
    assert record["thresholdFailures"]
