from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.cli.runtime import CliModelSettings, MissionControlProcess


def _process() -> MissionControlProcess:
    return MissionControlProcess(
        state_dir=Path(".agenthub-test"),
        workspace_root=Path("."),
        model=CliModelSettings(provider="mock", model="mock", api_key="", base_url=""),
    )


def test_startup_failure_reports_actual_log_path_without_secondary_error():
    process = _process()
    process._process = Mock()
    process._process.poll.return_value = 7
    process._process.returncode = 7
    process._log_handle = Mock(name="log-handle")
    process._log_handle.name = "C:/tmp/mission-control.log"

    with pytest.raises(RuntimeError, match="code 7") as exc:
        process._wait_ready(timeout=0.01)

    assert "mission-control.log" in str(exc.value)
    assert "_run_dir" not in str(exc.value)


def test_startup_wait_accepts_health_response():
    process = _process()
    process._process = Mock()
    process._process.poll.return_value = None
    response = Mock(status_code=200)
    with patch("app.cli.runtime.httpx.get", return_value=response):
        process._wait_ready(timeout=0.2)
