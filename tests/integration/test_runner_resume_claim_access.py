"""Real SQLite Mission Control rejects foreign and expired RUNNING leases."""
from datetime import datetime, timedelta, timezone

import pytest

from app.domain import ActorRef
from app.services.runner_service import WorkUnitRunner
from app.services.workspace_admission_service import WorkspaceClaimStatus
from tests.integration.recovery_worker import RealControl, control_repository
from tests.integration.test_recovery_process import seed
from tests.services.test_runner_service import FakePublisher


@pytest.mark.asyncio
@pytest.mark.parametrize("runner_id,expired", [("foreign-runner", False), ("runner-1", True)])
async def test_complete_claim_entry_never_executes_foreign_or_expired_running_lease(tmp_path, runner_id, expired):
    await seed(tmp_path)

    class NeverResolve:
        async def resolve(self, payload):
            pytest.fail("Mission Control must reject the lease before input resolution")

    async with control_repository(tmp_path / "control.sqlite3") as repository:
        unit = await repository.get_work_unit("wu-1")
        if expired:
            unit = unit.model_copy(update={"lease": unit.lease.model_copy(update={
                "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
            })})
            await repository.update_work_unit(unit)
        control = RealControl(repository, tmp_path, "no-execution")
        control.actor = ActorRef(type="runner", id=runner_id)
        publisher = FakePublisher()
        runner = WorkUnitRunner(control, runner_id=runner_id, assigned_agent_id="agent-1",
            assigned_adapter="function-calling", claimed_work_resolver=NeverResolve(),
            supported_work_unit_kinds=("desktop.task",), publisher=publisher)
        poll = await runner.claim_ready_and_run("workspace-1")
        assert poll.claim_status == WorkspaceClaimStatus.IDLE and poll.run_result is None
        current = await repository.get_work_unit("wu-1")
        assert current.status.value == "RUNNING" and current.attempt == 1
        assert current.lease.runner_id == "runner-1"
        assert await repository.list_execution_checkpoints("mis-1") == []
        assert publisher.contents == []
