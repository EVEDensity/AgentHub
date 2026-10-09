"""Actual PostgreSQL selectors, authenticated claim targets and tenant quota."""
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio

from app.domain import ActorRef, MissionStatus
from app.services.mission_service import MissionService
from app.services.runner_service import MissionControlRunnerClient
from app.services.workspace_admission_service import WorkspaceClaimAdmissionPolicy
from tests.domain.factories import build_work_unit
from tests.integration.test_workspace_claim_postgres import _build_app


pytestmark = pytest.mark.skipif(not os.getenv("AGENTHUB_TEST_POSTGRES_DSN"),
    reason="AGENTHUB_TEST_POSTGRES_DSN is required for actual targeted claim tests")


@pytest_asyncio.fixture
async def postgres_claims():
    # Reuse the established isolated real schema/IAM setup, without inheriting
    # or duplicating its tests. Cleanup uses this fixture's current event loop.
    from tests.integration.test_workspace_claim_postgres import WorkspaceClaimPostgresIntegrationTests
    fixture = WorkspaceClaimPostgresIntegrationTests("test_kind_filter_leaves_unsupported_rows_unleased")
    try:
        await fixture.asyncSetUp()
        yield fixture
    finally:
        if hasattr(fixture, "_pool"):
            await fixture._pool.close()
        if hasattr(fixture, "_schema"):
            await fixture._drop_schema()


def claim_arguments(*, runner_id="runner-a", limit=0, target=None):
    return {"agent_id": "reviewer", "adapter_type": "local_codex",
        "supported_work_unit_kinds": ("a2a.inbound",), "runner_id": runner_id,
        "actor": ActorRef(type="runner", id=runner_id), "lease_seconds": 300,
        "admission_policy": WorkspaceClaimAdmissionPolicy("tenant-1", limit), "resume_mission_id": target}


async def running_unit(fixture):
    service = MissionService(fixture._plain_repository)
    claimed = await service.claim_workspace_bound_work_unit("workspace-1", **claim_arguments())
    assert claimed.work_unit.id == "work-a"
    return await service.start_work_unit("mission-a", "work-a", runner_id="runner-a",
        lease_id=claimed.work_unit.lease.id, actor=ActorRef(type="runner", id="runner-a"))


async def pending_sibling(fixture):
    repository = fixture._plain_repository
    other = await repository.get_mission("mission-b")
    await repository.update_mission(other.model_copy(update={"status": MissionStatus.CANCELLED}))
    await repository.add_work_unit(build_work_unit(id="work-a-sibling", mission_id="mission-a",
        kind="a2a.inbound", required_capabilities=["a2a.receive"], assigned_agent_id="reviewer",
        assigned_adapter="local_codex"))


@pytest.mark.asyncio
async def test_normal_pg_claim_prefers_pending_sibling_over_owned_running_lease(postgres_claims):
    await running_unit(postgres_claims)
    await pending_sibling(postgres_claims)
    outcome = await MissionService(postgres_claims._plain_repository).claim_workspace_bound_work_unit(
        "workspace-1", **claim_arguments())
    assert outcome.work_unit.id == "work-a-sibling" and outcome.work_unit.status.value == "LEASED"
    assert (await postgres_claims._plain_repository.get_work_unit("work-a")).status.value == "RUNNING"


@pytest.mark.asyncio
async def test_pg_full_quota_allows_owned_resume_but_denies_new_lease(postgres_claims):
    original = await running_unit(postgres_claims)
    await pending_sibling(postgres_claims)
    service = MissionService(postgres_claims._plain_repository)
    resumed = await service.claim_workspace_bound_work_unit("workspace-1", **claim_arguments(limit=1, target="mission-a"))
    assert resumed.status.value == "claimed" and resumed.work_unit.id == original.id
    assert resumed.work_unit.lease == original.lease and resumed.work_unit.attempt == original.attempt
    ordinary = await service.claim_workspace_bound_work_unit("workspace-1", **claim_arguments(limit=1))
    assert ordinary.status.value == "claimed" and ordinary.work_unit.lease == original.lease
    other = await service.claim_workspace_bound_work_unit("workspace-1", **claim_arguments(runner_id="runner-b", limit=1))
    assert other.status.value == "capacity_saturated" and other.work_unit is None
    assert (await postgres_claims._plain_repository.get_work_unit("work-a-sibling")).attempt == 0


@pytest.mark.asyncio
async def test_pg_expired_lease_releases_capacity_for_new_pending_claim(postgres_claims):
    original = await running_unit(postgres_claims)
    await pending_sibling(postgres_claims)
    lease = original.lease.model_copy(update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
    await postgres_claims._plain_repository.update_work_unit(original.model_copy(update={"lease": lease}))
    assert await postgres_claims._plain_repository.count_tenant_active_runner_work_units("tenant-1") == 0
    claimed = await MissionService(postgres_claims._plain_repository).claim_workspace_bound_work_unit(
        "workspace-1", **claim_arguments(limit=1))
    assert claimed.work_unit.id == "work-a-sibling" and claimed.work_unit.attempt == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["other_owner", "expired", "pending_only", "other_mission"])
async def test_pg_http_target_refuses_other_owner_expiry_and_new_work(postgres_claims, condition):
    target = "mission-a"
    if condition != "pending_only":
        original = await running_unit(postgres_claims)
        if condition == "other_owner":
            lease = original.lease.model_copy(update={"runner_id": "runner-b"})
            await postgres_claims._plain_repository.update_work_unit(original.model_copy(update={"lease": lease}))
        elif condition == "expired":
            lease = original.lease.model_copy(update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
            await postgres_claims._plain_repository.update_work_unit(original.model_copy(update={"lease": lease}))
        else:
            target = "mission-b"
    app = _build_app(postgres_claims._plain_repository, postgres_claims._grant_authorizer,
        postgres_claims._admission_policy_resolver)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        control = MissionControlRunnerClient("http://test", access_token="runner-a", http_client=http)
        result = await control.claim_ready_work_unit("workspace-1", runner_id="runner-a", agent_id="reviewer",
            adapter_type="local_codex", supported_work_unit_kinds=("a2a.inbound",), lease_seconds=300,
            resume_mission_id=target)
    assert result == {"claimStatus": "idle", "workUnit": None}
    pending = await postgres_claims._plain_repository.get_work_unit("work-b")
    assert pending.status.value == "PENDING" and pending.attempt == 0
