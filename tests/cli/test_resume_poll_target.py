"""CLI execution recovery narrows polling; compact chat starts ordinary work."""
from unittest.mock import patch

import pytest

from app.cli import runtime
from app.services.runner.settings import DesktopLocalRunnerSettings


ENV_KEY = "AGENTHUB_DESKTOP_LOCAL_RUNNER_RESUME_MISSION_ID"


@pytest.mark.parametrize("target", [None, "mission-1"])
def test_subprocess_env_explicitly_controls_target(tmp_path, monkeypatch, target):
    monkeypatch.setenv(ENV_KEY, "unrelated-ambient-mission")
    env = runtime.build_server_env(db_path=tmp_path / "control.db", data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace", port=28100,
        model=runtime.CliModelSettings(provider="mock", model="mock", api_key="test", base_url=""),
        max_total_tokens=1000, runner_timeout_seconds=10, resume_mission_id=target)
    assert env.get(ENV_KEY) == target
    assert DesktopLocalRunnerSettings.from_env(env).resume_mission_id == target


@pytest.mark.parametrize("context,target", [("", "old-mission"), ("compact context", None)])
def test_cli_sets_poll_target_before_server_starts(tmp_path, context, target):
    captured = {}

    class StoppedBeforeBoot:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            raise RuntimeError("test stops before launching server")

        def __exit__(self, *args):
            return False

    with patch.object(runtime, "MissionControlProcess", StoppedBeforeBoot):
        with pytest.raises(RuntimeError, match="test stops before"):
            runtime.execute_objective(objective="requested objective", workspace_root=tmp_path,
                state_dir=tmp_path / ".agenthub", resume_mission_id="old-mission", context_text=context,
                model=runtime.CliModelSettings(provider="mock", model="mock", api_key="test", base_url=""))
    assert captured["resume_mission_id"] == target
