"""Shared hermetic Mission API wiring; production state remains behind injected ports."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from app.api.v1.missions import (
    get_agent_binding_resolver,
    get_artifact_byte_verifier,
    get_mission_repository,
    get_runner_workspace_grant_authorizer,
    get_verifier_workspace_grant_authorizer,
    get_workspace_claim_admission_policy_resolver,
    router,
)
from app.services.agent_binding_service import (
    AgentBindingResolver,
    UnavailableAgentBindingResolver,
)
from app.services.auth_service import get_current_user
from app.services.workspace_access_service import (
    RunnerWorkspaceGrantAuthorizer,
    VerifierWorkspaceGrantAuthorizer,
)
from app.services.workspace_admission_service import (
    WorkspaceClaimAdmissionPolicyResolver,
)

if TYPE_CHECKING:
    from tests.api.test_missions_api import (
        FakeArtifactByteVerifier,
        FakeMissionRepository,
    )

class FakeRunnerPresenceRepository:
    """Contact recorder for fake HTTP wiring; not durable database evidence."""

    def __init__(self):
        self.observations = []

    async def observe_poll(self, workspace_id, **observation):
        self.observations.append({"workspace_id": workspace_id, **observation})


def build_app(
    repository: FakeMissionRepository,
    user: dict[str, Any],
    *,
    artifact_byte_verifier: FakeArtifactByteVerifier | None = None,
    agent_binding_resolver: AgentBindingResolver | None = None,
    runner_workspace_grant_authorizer: RunnerWorkspaceGrantAuthorizer | None = None,
    verifier_workspace_grant_authorizer: (
        VerifierWorkspaceGrantAuthorizer | None
    ) = None,
    workspace_claim_admission_policy_resolver: (
        WorkspaceClaimAdmissionPolicyResolver | None
    ) = None,
) -> FastAPI:
    # Resolve existing fake adapters only when called, after the legacy test
    # module has finished importing this compatibility fixture.
    from tests.api.test_missions_api import (
        FakeArtifactByteVerifier,
        FakeRunnerWorkspaceGrantAuthorizer,
        FakeVerifierWorkspaceGrantAuthorizer,
        FakeWorkspaceClaimAdmissionPolicyResolver,
    )

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    from app.api.v1.execution_status import get_runner_presence_repository
    presence = FakeRunnerPresenceRepository()
    app.dependency_overrides[get_runner_presence_repository] = lambda: presence
    verifier = artifact_byte_verifier or FakeArtifactByteVerifier()
    verifier.repository = repository
    app.dependency_overrides[get_mission_repository] = lambda: repository
    app.dependency_overrides[get_artifact_byte_verifier] = lambda: verifier
    binding_resolver = agent_binding_resolver or UnavailableAgentBindingResolver()
    app.dependency_overrides[get_agent_binding_resolver] = lambda: binding_resolver
    grant_authorizer = (
        runner_workspace_grant_authorizer or FakeRunnerWorkspaceGrantAuthorizer()
    )
    app.dependency_overrides[get_runner_workspace_grant_authorizer] = lambda: (
        grant_authorizer
    )
    verifier_grant_authorizer = (
        verifier_workspace_grant_authorizer or FakeVerifierWorkspaceGrantAuthorizer()
    )
    app.dependency_overrides[get_verifier_workspace_grant_authorizer] = lambda: (
        verifier_grant_authorizer
    )
    admission_policy_resolver = (
        workspace_claim_admission_policy_resolver
        or FakeWorkspaceClaimAdmissionPolicyResolver()
    )
    app.dependency_overrides[get_workspace_claim_admission_policy_resolver] = lambda: (
        admission_policy_resolver
    )
    app.dependency_overrides[get_current_user] = lambda: user
    return app
