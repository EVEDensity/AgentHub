from __future__ import annotations

import hashlib

from app.domain import (
    AcceptanceCriterion,
    ActorRef,
    Budgets,
    CapabilityGrant,
    Mission,
    MissionContract,
    MissionSource,
    MissionStatus,
    OutputSpec,
    WorkUnit,
    WorkUnitStatus,
)
from app.repositories import MissionRepository
from app.schemas.a2a_adapter import A2ATaskCreateRequest
from app.services.mission_service import MissionService

A2A_DELEGATE_CAPABILITY = "a2a.send"
A2A_ADAPTER_ID = "a2a.outbound"


class A2ATaskNotFoundError(LookupError):
    pass


class A2ATaskConflictError(ValueError):
    pass


def _is_unique_violation(exc: Exception) -> bool:
    return getattr(exc, "sqlstate", None) == "23505"


def build_a2a_actor(user: dict) -> ActorRef:
    return ActorRef(
        type="adapter",
        id=f"a2a:{user['id']}",
        display_name=user.get("name") or user.get("username"),
    )


def _mapping_ids(workspace_id: str, task_id: str) -> tuple[str, str, str, str]:
    digest = hashlib.sha256(f"{workspace_id}\0{task_id}".encode()).hexdigest()[:32]
    return (
        f"mis-a2a-{digest}",
        f"contract-a2a-{digest}",
        f"criterion-a2a-{digest}",
        f"wu-a2a-{digest}",
    )


def _title(objective: str) -> str:
    first_line = objective.strip().splitlines()[0]
    return first_line[:255]


def _task_state(mission: Mission, work_unit: WorkUnit | None) -> str:
    mission_states = {
        MissionStatus.CANCELLED: "canceled",
        MissionStatus.FAILED: "failed",
        MissionStatus.SUCCEEDED: "completed",
        MissionStatus.WAITING_DECISION: "input-required",
    }
    if mission.status in mission_states:
        return mission_states[mission.status]
    if work_unit is None:
        return "submitted" if mission.status == MissionStatus.READY else "working"
    work_unit_states = {
        WorkUnitStatus.PENDING: "submitted",
        WorkUnitStatus.LEASED: "submitted",
        WorkUnitStatus.RUNNING: "working",
        WorkUnitStatus.VERIFYING: "working",
        WorkUnitStatus.WAITING: "input-required",
        WorkUnitStatus.RETRYING: "working",
        WorkUnitStatus.FAILED: "failed",
        WorkUnitStatus.CANCELLED: "canceled",
        WorkUnitStatus.SUCCEEDED: "working",
    }
    return work_unit_states[work_unit.status]


def task_projection(mission: Mission, work_unit: WorkUnit | None) -> dict:
    return {
        "taskId": mission.source.external_id,
        "state": _task_state(mission, work_unit),
        "missionId": mission.id,
        "missionStatus": mission.status.value,
        "workUnitId": work_unit.id if work_unit is not None else None,
        "workUnitStatus": work_unit.status.value if work_unit is not None else None,
    }


class A2AAdapterService:
    def __init__(self, repository: MissionRepository | None = None) -> None:
        self._repository = repository or MissionRepository()
        self._missions = MissionService(self._repository)

    async def submit_task(
        self,
        request: A2ATaskCreateRequest,
        *,
        actor: ActorRef,
    ) -> dict:
        mission = await self._repository.get_mission_by_source(
            request.workspace_id,
            source_type="a2a",
            external_id=request.task_id,
        )
        if mission is not None:
            await self._validate_idempotent_request(mission, request)
        else:
            try:
                mission = await self._create_mission(request, actor=actor)
            except Exception as exc:
                if not _is_unique_violation(exc):
                    raise
                mission = await self._repository.get_mission_by_source(
                    request.workspace_id,
                    source_type="a2a",
                    external_id=request.task_id,
                )
                if mission is None:
                    raise
                await self._validate_idempotent_request(mission, request)

        if mission.status == MissionStatus.READY:
            mission = await self._missions.start_mission(mission.id, actor=actor)

        work_unit = await self._get_mapped_work_unit(
            request.workspace_id,
            request.task_id,
        )
        if work_unit is not None:
            self._validate_work_unit(mission, work_unit, request)
        if work_unit is None and mission.status == MissionStatus.RUNNING:
            capabilities = self._capabilities(request.required_capabilities)
            _mission_id, _contract_id, _criterion_id, work_unit_id = _mapping_ids(
                request.workspace_id,
                request.task_id,
            )
            try:
                work_unit = await self._missions.create_work_unit(
                    mission.id,
                    work_unit_id=work_unit_id,
                    kind="a2a.delegate",
                    dependencies=[],
                    input_refs=[],
                    expected_outputs=[OutputSpec(kind="a2a.result", required=True)],
                    required_capabilities=list(capabilities),
                    assigned_adapter=A2A_ADAPTER_ID,
                    actor=actor,
                )
            except Exception as exc:
                if not _is_unique_violation(exc):
                    raise
                work_unit = await self._repository.get_work_unit(work_unit_id)
                if work_unit is None or work_unit.mission_id != mission.id:
                    raise
        return task_projection(mission, work_unit)

    async def get_task(self, workspace_id: str, task_id: str) -> dict:
        mission = await self._mission_for_task(workspace_id, task_id)
        work_unit = await self._get_mapped_work_unit(workspace_id, task_id)
        return task_projection(mission, work_unit)

    async def cancel_task(
        self,
        workspace_id: str,
        task_id: str,
        *,
        actor: ActorRef,
    ) -> dict:
        mission = await self._mission_for_task(workspace_id, task_id)
        if mission.status != MissionStatus.CANCELLED:
            try:
                mission = await self._missions.cancel_mission(mission.id, actor=actor)
            except ValueError as exc:
                raise A2ATaskConflictError(str(exc)) from exc
        work_unit = await self._get_mapped_work_unit(workspace_id, task_id)
        return task_projection(mission, work_unit)

    async def _create_mission(
        self,
        request: A2ATaskCreateRequest,
        *,
        actor: ActorRef,
    ) -> Mission:
        mission_id, contract_id, criterion_id, _work_unit_id = _mapping_ids(
            request.workspace_id,
            request.task_id,
        )
        contract = self._build_contract(
            request,
            contract_id=contract_id,
            criterion_id=criterion_id,
        )
        return await self._missions.create_mission(
            mission_id=mission_id,
            workspace_id=request.workspace_id,
            title=_title(request.objective),
            objective=request.objective,
            source=MissionSource(
                type="a2a",
                reference=request.agent_url,
                external_id=request.task_id,
            ),
            contract=contract,
            actor=actor,
        )

    def _build_contract(
        self,
        request: A2ATaskCreateRequest,
        *,
        contract_id: str,
        criterion_id: str,
    ) -> MissionContract:
        capabilities = self._capabilities(request.required_capabilities)
        return MissionContract(
            id=contract_id,
            version=1,
            repository_scopes=[],
            allowed_capabilities=[
                CapabilityGrant(
                    capability=capability,
                    scope={"agentUrl": request.agent_url},
                )
                for capability in capabilities
            ],
            budgets=Budgets(
                time_seconds=request.time_seconds,
                model_cost=request.model_cost,
                retries=request.retries,
            ),
            acceptance_criteria=[
                AcceptanceCriterion(
                    id=criterion_id,
                    kind="contract",
                    description=(
                        "The delegated A2A result is stored as an artifact and "
                        "validated by independent evidence."
                    ),
                    required=True,
                    configuration={"protocol": "a2a"},
                )
            ],
            decision_gates=[],
            forbidden_actions=[],
        )

    async def _mission_for_task(self, workspace_id: str, task_id: str) -> Mission:
        mission = await self._repository.get_mission_by_source(
            workspace_id,
            source_type="a2a",
            external_id=task_id,
        )
        if mission is None:
            raise A2ATaskNotFoundError(task_id)
        return mission

    async def _get_mapped_work_unit(
        self,
        workspace_id: str,
        task_id: str,
    ) -> WorkUnit | None:
        _mission_id, _contract_id, _criterion_id, work_unit_id = _mapping_ids(
            workspace_id,
            task_id,
        )
        return await self._repository.get_work_unit(work_unit_id)

    @staticmethod
    def _capabilities(requested: list[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys([A2A_DELEGATE_CAPABILITY, *requested]))

    async def _validate_idempotent_request(
        self,
        mission: Mission,
        request: A2ATaskCreateRequest,
    ) -> None:
        if (
            mission.objective != request.objective
            or mission.source.reference != request.agent_url
        ):
            raise A2ATaskConflictError(
                "A2A task id is already mapped to a different objective or agent"
            )
        _mission_id, contract_id, criterion_id, _work_unit_id = _mapping_ids(
            request.workspace_id,
            request.task_id,
        )
        existing_contract = await self._repository.get_contract(mission.contract_id)
        expected_contract = self._build_contract(
            request,
            contract_id=contract_id,
            criterion_id=criterion_id,
        )
        if existing_contract != expected_contract:
            raise A2ATaskConflictError(
                "A2A task id is already mapped to a different contract"
            )

    def _validate_work_unit(
        self,
        mission: Mission,
        work_unit: WorkUnit,
        request: A2ATaskCreateRequest,
    ) -> None:
        expected_capabilities = self._capabilities(request.required_capabilities)
        if (
            work_unit.mission_id != mission.id
            or work_unit.kind != "a2a.delegate"
            or work_unit.assigned_adapter != A2A_ADAPTER_ID
            or work_unit.required_capabilities != expected_capabilities
        ):
            raise A2ATaskConflictError(
                "A2A task id is already mapped to a different work unit"
            )
