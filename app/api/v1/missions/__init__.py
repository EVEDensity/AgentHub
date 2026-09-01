"""v1 missions API — public facade package."""
from app.api.v1._missions_impl import router
from app.api.v1.missions._deps import (
    get_agent_binding_resolver,
    get_artifact_byte_verifier,
    get_desktop_execution_workspace_root,
    get_mission_repository,
    get_runner_workspace_grant_authorizer,
    get_verifier_workspace_grant_authorizer,
    get_workspace_claim_admission_policy_resolver,
)

__all__ = [
    "router",
    "get_agent_binding_resolver",
    "get_artifact_byte_verifier",
    "get_desktop_execution_workspace_root",
    "get_mission_repository",
    "get_runner_workspace_grant_authorizer",
    "get_verifier_workspace_grant_authorizer",
    "get_workspace_claim_admission_policy_resolver",
]
