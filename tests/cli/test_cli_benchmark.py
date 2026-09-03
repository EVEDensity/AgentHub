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
        kwargs["on_text"]("hello")
        return Result()

    monkeypatch.setattr(cli_benchmark, "execute_objective", fake_execute)
    monkeypatch.setattr("sys.argv", ["cli_benchmark", "task"])
    assert cli_benchmark.main() == 0
    record = json.loads(capsys.readouterr().out)
    assert record["schemaVersion"] == 1
    assert record["events"] == 1
    assert record["firstTokenSeconds"] is not None
