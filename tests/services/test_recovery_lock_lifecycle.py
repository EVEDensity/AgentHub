"""An actual private attempt lock releases when standalone binding fails."""
from unittest.mock import Mock

import pytest

from app.services.harness_checkpoint import HarnessExecutionContext
from app.services.harness_service import HarnessRequest
from app.services.recovery_lock import RecoveryExecutionLock
from app.services.recovery_store import runner_state_directory
from app.services.runner.model import DesktopTaskHarnessFactory
from app.services.tools.policy import ToolExecutionPolicy
from tests.integration.recovery_worker import ProcessModelFactory
from tests.services.test_desktop_local_runner import desktop_claim_payload


def _harness(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    factory = DesktopTaskHarnessFactory(ProcessModelFactory(tmp_path), tools=[], workspace_root=workspace,
        recovery_state_root=tmp_path / "private", tool_policy=ToolExecutionPolicy.for_mode("edit", workspace))
    _, context = desktop_claim_payload()
    return factory.build(context), workspace


def _lock(workspace, tmp_path):
    return RecoveryExecutionLock(runner_state_directory(workspace, tmp_path / "private"),
                                 "mis-desktop-1/wu-desktop-1/1")


@pytest.mark.asyncio
async def test_standalone_checkpoint_bind_failure_immediately_releases_attempt_lock(tmp_path):
    harness, workspace = _harness(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    harness._checkpoint_port = harness._recovery.journal(Mock(), None)
    try:
        with pytest.raises(ValueError, match="workspace differs"):
            await harness.execute(HarnessRequest("objective", "text", 60, cwd=other,
                execution=HarnessExecutionContext("mis-desktop-1", "wu-desktop-1", 1)))
        lock = _lock(workspace, tmp_path)
        lock.close()
    finally:
        harness.close_recovery()
