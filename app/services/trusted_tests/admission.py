"""Mission Control checks the whole registered attempt before trusted PASS."""

from __future__ import annotations

from app.domain import Artifact, WorkUnit
from app.repositories import MissionRepository
from app.services.trusted_tests.policy import TRUSTED_TEST_RUN_V1


async def require_complete_attempt_artifacts(
    repository: MissionRepository, plan, work_unit: WorkUnit, selected: tuple[Artifact, ...],
) -> None:
    if plan.evaluator != TRUSTED_TEST_RUN_V1:
        return
    # One extra row is enough to refuse a non-exact five-Artifact attempt.
    registered = await repository.list_work_unit_artifacts(
        work_unit.mission_id, work_unit.id, work_unit.attempt, limit=6,
    )
    if len(registered) != 5 or {item.id: item for item in registered} != {item.id: item for item in selected}:
        from app.services.mission._types import WorkUnitNotReadyError

        raise WorkUnitNotReadyError(
            "trusted test PASS requires the complete registered current-attempt Artifact bundle"
        )
