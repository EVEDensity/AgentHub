"""Facade — re-exports everything from app.services.mission/*.

Kept for backwards compatibility.  External code continues to import
from ``app.services.mission_service``; the real implementation lives in
``app/services/mission/``.

Split: 2026-09-01, T0 debt-clearing slice.
"""
from __future__ import annotations

from app.services.mission._types import *  # noqa: F401,F403 — constants, errors, helpers, outcome classes
from app.services.mission._service import MissionService  # noqa: F401

# Re-export the full set of public names so ``from mission_service import X``
# keeps working exactly as before the split.  This is intentionally a wildcard
# from _types plus the one class from _service.

__all__ = [
    # constants
    "DESKTOP_TASK_WORK_UNIT_KIND",
    # helpers
    "build_human_actor",
    "build_runner_actor",
    "build_verifier_actor",
    "new_identifier",
    "_checkpoint_event_payload",
    # error classes
    "MissionNotFoundError",
    "WorkUnitNotFoundError",
    "WorkUnitNotReadyError",
    "LeaseOwnershipError",
    "LeaseExpiredError",
    "AgentBindingNotFoundError",
    "DecisionNotFoundError",
    "DecisionConflictError",
    "ContractRevisionConflictError",
    # outcome / context classes
    "ClaimedExecutionContext",
    "VerificationContext",
    "VerificationDiscoveryOutcome",
    "DecisionExpiryOutcome",
    "WorkUnitClaimOutcome",
    "MissionForkOutcome",
    # service
    "MissionService",
]
