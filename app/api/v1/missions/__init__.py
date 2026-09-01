"""v1 missions API — public facade package.

The implementation lives in ``_missions_impl.py`` (all endpoint handlers,
auth helpers, dependency-injection callables, and the main ``router``).
Only the symbols imported below are part of the v1 public surface; the
rest are private to the implementation module and may be reorganised into
sub-modules (``_deps.py``, ``_auth.py``, ``work_units.py``, …) without
changing any downstream import path.
"""

from app.api.v1._missions_impl import (
    get_agent_binding_resolver,
    get_artifact_byte_verifier,
    get_desktop_execution_workspace_root,
    get_mission_repository,
    get_runner_workspace_grant_authorizer,
    get_verifier_workspace_grant_authorizer,
    get_workspace_claim_admission_policy_resolver,
    router,
)

__all__ = [
    "get_agent_binding_resolver",
    "get_artifact_byte_verifier",
    "get_desktop_execution_workspace_root",
    "get_mission_repository",
    "get_runner_workspace_grant_authorizer",
    "get_verifier_workspace_grant_authorizer",
    "get_workspace_claim_admission_policy_resolver",
    "router",
]
