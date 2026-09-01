"""v1 missions/_crud.py — Mission CRUD, contract revision, fork, workspace listing."""
from __future__ import annotations

from app.api.v1.missions._deps import *

router = APIRouter()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_mission(
    request: MissionCreateRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    authorize_workspace(user, request.workspace_id)
    service = MissionService(repository)
    try:
        mission = await service.create_mission(
            mission_id=request.id,
            workspace_id=request.workspace_id,
            title=request.title,
            objective=request.objective,
            source=request.source,
            contract=request.contract,
            actor=build_human_actor(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return mission.to_public_dict()

@router.post(
    "/{source_mission_id}/forks",
    status_code=status.HTTP_201_CREATED,
)
async def fork_mission(
    source_mission_id: str,
    request: MissionForkRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    artifact_byte_verifier: ArtifactByteVerifierDep,
    agent_binding_resolver: AgentBindingResolverDep,
) -> dict:
    _authorize_human_fork_access(user)
    await _authorized_mission(
        source_mission_id,
        user=user,
        repository=repository,
    )
    service = MissionService(
        repository,
        artifact_byte_verifier=artifact_byte_verifier,
        agent_binding_resolver=agent_binding_resolver,
    )
    try:
        outcome = await service.fork_mission(
            source_mission_id,
            mission_id=request.id,
            work_unit_id=request.work_unit_id,
            title=request.title,
            objective=request.objective,
            checkpoint_id=request.checkpoint_id,
            artifact_refs=request.artifact_refs,
            expected_outputs=request.expected_outputs,
            required_capabilities=request.required_capabilities,
            agent_id=request.agent_id,
            actor=build_human_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except AgentBindingUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ArtifactBytesUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail=str(exc),
        ) from exc
    except (AgentBindingNotFoundError, ArtifactIntegrityError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return outcome.to_public_dict()

@router.get("/{mission_id}")
async def get_mission(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    mission = await repository.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    authorize_workspace(user, mission.workspace_id)
    return mission.to_public_dict()

@router.post(
    "/{mission_id}/contract/revisions",
    status_code=status.HTTP_201_CREATED,
)
async def revise_contract(
    mission_id: str,
    request: ContractRevisionRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    _authorize_human_contract_access(user)
    await _authorized_mission(mission_id, user=user, repository=repository)
    service = MissionService(repository)
    try:
        contract = await service.revise_contract(
            mission_id,
            expected_version=request.expected_version,
            contract=request.contract,
            reason=request.reason,
            actor=build_human_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except ContractRevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return contract.to_public_dict()

@router.get("")
async def list_missions(
    workspace_id: WorkspaceId,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    limit: MissionLimit = 100,
    offset: MissionOffset = 0,
) -> dict:
    authorize_workspace(user, workspace_id)
    missions = await repository.list_missions(
        workspace_id,
        limit=limit,
        offset=offset,
    )
    return {"missions": [mission.to_public_dict() for mission in missions]}
