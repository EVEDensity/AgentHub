"""v1 missions/_work_units.py — Work-unit full lifecycle."""
from __future__ import annotations

from app.api.v1.missions._deps import *

router = APIRouter()


@router.post("/{mission_id}/work-units/{work_unit_id}/stream-events", status_code=status.HTTP_201_CREATED)
async def publish_stream_event(
    mission_id: str,
    work_unit_id: str,
    request: StreamingEventRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    """Publish one bounded assistant delta behind the Runner lease fence."""
    service = MissionService(repository)
    try:
        return await service.publish_streaming_event(
            mission_id,
            work_unit_id,
            event_id=request.event_id,
            event_type=request.event_type,
            text=request.text,
            tool_name=request.tool_name,
            attempt=request.attempt,
            lease_id=request.lease_id,
            runner_id=str(user["id"]),
            actor=_build_execution_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except LeaseOwnershipError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, WorkUnitNotReadyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{mission_id}/work-units", status_code=status.HTTP_201_CREATED)
async def create_work_unit(
    mission_id: str,
    request: WorkUnitCreateRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    service = MissionService(repository)
    try:
        work_unit = await service.create_work_unit(
            mission_id,
            work_unit_id=request.id,
            kind=request.kind,
            dependencies=request.dependencies,
            input_refs=request.input_refs,
            expected_outputs=request.expected_outputs,
            required_capabilities=request.required_capabilities,
            assigned_adapter=request.assigned_adapter,
            actor=build_human_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return work_unit.to_public_dict()

@router.get("/{mission_id}/work-units")
async def list_work_units(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    limit: WorkUnitLimit = 100,
    offset: WorkUnitOffset = 0,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    work_units = await repository.list_work_units(
        mission_id,
        limit=limit,
        offset=offset,
    )
    return {"workUnits": [work_unit.to_public_dict() for work_unit in work_units]}

@router.post(
    "/{mission_id}/work-units/{parent_work_unit_id}/delegations",
    status_code=status.HTTP_202_ACCEPTED,
)
async def delegate_work_unit(
    mission_id: str,
    parent_work_unit_id: str,
    request: WorkUnitDelegationRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    agent_binding_resolver: AgentBindingResolverDep,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    service = MissionService(repository, agent_binding_resolver=agent_binding_resolver)
    try:
        work_unit = await service.delegate_work_unit(
            mission_id,
            parent_work_unit_id,
            work_unit_id=request.id,
            kind=request.kind,
            input_refs=request.input_refs,
            expected_outputs=request.expected_outputs,
            required_capabilities=request.required_capabilities,
            agent_id=request.agent_id,
            lease_id=request.lease_id,
            runner_id=str(user["id"]),
            actor=build_human_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except AgentBindingUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AgentBindingNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return work_unit.to_public_dict()

@router.post("/{mission_id}/work-units/{work_unit_id}/lease")
async def lease_work_unit(
    mission_id: str,
    work_unit_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    request: WorkUnitLeaseRequest | None = None,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    work_unit = await repository.get_work_unit(work_unit_id)
    if work_unit is None or work_unit.mission_id != mission_id:
        raise HTTPException(status_code=404, detail="WorkUnit not found")
    service = MissionService(repository)
    try:
        leased = await service.lease_work_unit(
            mission_id,
            work_unit_id,
            runner_id=str(user["id"]),
            actor=build_human_actor(user),
            lease_seconds=request.lease_seconds if request is not None else 300,
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return leased.to_public_dict()

@router.post("/work-unit-claims")
async def claim_workspace_work_unit(
    request: WorkspaceWorkUnitClaimRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    grant_authorizer: RunnerWorkspaceGrantAuthorizerDep,
    admission_policy_resolver: WorkspaceClaimAdmissionPolicyResolverDep,
) -> dict:
    """Discover and claim one ready WorkUnit in an authorized workspace."""

    await _authorize_workspace_claim(
        user,
        request.workspace_id,
        grant_authorizer=grant_authorizer,
    )
    admission_policy = await _resolve_workspace_claim_admission(
        request.workspace_id,
        resolver=admission_policy_resolver,
    )
    service = MissionService(repository)
    try:
        claimed = await service.claim_workspace_bound_work_unit(
            request.workspace_id,
            agent_id=request.agent_id,
            adapter_type=request.adapter_type,
            supported_work_unit_kinds=request.supported_work_unit_kinds,
            runner_id=str(user["id"]),
            actor=build_runner_actor(user),
            lease_seconds=request.lease_seconds,
            admission_policy=admission_policy,
        )
    except WorkspaceClaimAdmissionUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return claimed.to_public_dict()

@router.post("/verification-work-items/discover")
async def discover_verification_work(
    request: WorkspaceVerificationDiscoveryRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    grant_authorizer: VerifierWorkspaceGrantAuthorizerDep,
) -> dict:
    """Return verifier context; inconclusive policy opens a Mission Decision."""

    await _authorize_workspace_verification(
        user,
        request.workspace_id,
        grant_authorizer=grant_authorizer,
    )
    service = MissionService(repository)
    try:
        discovered = await service.discover_workspace_verification_work(
            request.workspace_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return discovered.to_public_dict()

@router.post("/{mission_id}/work-unit-claims")
async def claim_delegated_work_unit(
    mission_id: str,
    request: WorkUnitClaimRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    admission_policy_resolver: WorkspaceClaimAdmissionPolicyResolverDep,
) -> dict:
    """Claim one ready WorkUnit for an explicit Runner binding."""
    mission = await _authorized_mission(
        mission_id,
        user=user,
        repository=repository,
    )
    admission_policy = await _resolve_workspace_claim_admission(
        mission.workspace_id,
        resolver=admission_policy_resolver,
    )
    service = MissionService(repository)
    try:
        claimed = await service.claim_bound_work_unit(
            mission_id,
            agent_id=request.agent_id,
            adapter_type=request.adapter_type,
            runner_id=str(user["id"]),
            actor=build_runner_actor(user),
            lease_seconds=request.lease_seconds,
            admission_policy=admission_policy,
        )
    except WorkspaceClaimAdmissionUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return claimed.to_public_dict()

@router.post(
    "/{mission_id}/work-units/{work_unit_id}/execution-context",
)
async def get_claimed_execution_context(
    mission_id: str,
    work_unit_id: str,
    request: WorkUnitExecutionContextRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    """Return a lease-fenced context snapshot for a controlled root."""
    await _authorize_execution_work_unit(
        mission_id,
        work_unit_id,
        lease_id=request.lease_id,
        user=user,
        repository=repository,
    )
    service = MissionService(repository)
    try:
        context = await service.get_claimed_execution_context(
            mission_id,
            work_unit_id,
            lease_id=request.lease_id,
            runner_id=str(user["id"]),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"executionContext": context.to_public_dict()}

@router.post("/{mission_id}/work-units/{work_unit_id}/start")
async def start_work_unit(
    mission_id: str,
    work_unit_id: str,
    request: WorkUnitStartRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    await _authorize_execution_work_unit(
        mission_id,
        work_unit_id,
        lease_id=request.lease_id,
        user=user,
        repository=repository,
    )
    service = MissionService(repository)
    try:
        started = await service.start_work_unit(
            mission_id,
            work_unit_id,
            lease_id=request.lease_id,
            runner_id=str(user["id"]),
            actor=_build_execution_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return started.to_public_dict()

@router.post("/{mission_id}/work-units/{work_unit_id}/heartbeat")
async def heartbeat_work_unit(
    mission_id: str,
    work_unit_id: str,
    request: WorkUnitHeartbeatRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    await _authorize_execution_work_unit(
        mission_id,
        work_unit_id,
        lease_id=request.lease_id,
        user=user,
        repository=repository,
    )
    service = MissionService(repository)
    try:
        renewed = await service.heartbeat_work_unit(
            mission_id,
            work_unit_id,
            lease_id=request.lease_id,
            runner_id=str(user["id"]),
            actor=_build_execution_actor(user),
            lease_seconds=request.lease_seconds,
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return renewed.to_public_dict()

@router.post(
    "/{mission_id}/work-units/{work_unit_id}/checkpoints",
    status_code=status.HTTP_201_CREATED,
)
async def record_execution_checkpoint(
    mission_id: str,
    work_unit_id: str,
    request: ExecutionCheckpointCreateRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    await _authorize_execution_work_unit(
        mission_id,
        work_unit_id,
        lease_id=request.lease_id,
        user=user,
        repository=repository,
    )
    service = MissionService(repository)
    try:
        checkpoint = await service.record_execution_checkpoint(
            mission_id,
            work_unit_id,
            checkpoint_id=request.id,
            lease_id=request.lease_id,
            runner_id=str(user["id"]),
            sequence=request.sequence,
            phase=request.phase,
            iteration=request.iteration,
            tool_calls=request.tool_calls,
            prompt_tokens=request.prompt_tokens,
            completion_tokens=request.completion_tokens,
            model_cost=request.model_cost,
            terminal=request.terminal,
            failure_reason=request.failure_reason,
            tool_name=request.tool_name,
            tool_success=request.tool_success,
            actor=_build_execution_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return checkpoint.to_public_dict()

@router.post(
    "/{mission_id}/work-units/{work_unit_id}/artifacts",
    status_code=status.HTTP_201_CREATED,
)
async def register_artifact(
    mission_id: str,
    work_unit_id: str,
    request: ArtifactCreateRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    await _authorize_execution_work_unit(
        mission_id,
        work_unit_id,
        lease_id=request.lease_id,
        user=user,
        repository=repository,
    )
    service = MissionService(repository)
    try:
        artifact = await service.register_artifact(
            mission_id,
            work_unit_id,
            artifact_id=request.id,
            lease_id=request.lease_id,
            runner_id=str(user["id"]),
            kind=request.kind,
            digest=request.digest,
            content_address=request.content_address,
            media_type=request.media_type,
            size_bytes=request.size_bytes,
            source_repository=request.source_repository,
            base_commit=request.base_commit,
            retention=request.retention,
            sensitivity=request.sensitivity,
            actor=_build_execution_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return artifact.to_public_dict()

@router.post("/{mission_id}/work-units/{work_unit_id}/recover")
async def recover_work_unit_lease(
    mission_id: str,
    work_unit_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    work_unit = await repository.get_work_unit(work_unit_id)
    if work_unit is None or work_unit.mission_id != mission_id:
        raise HTTPException(status_code=404, detail="WorkUnit not found")
    service = MissionService(repository)
    try:
        recovered = await service.recover_expired_lease(
            mission_id,
            work_unit_id,
            actor=build_human_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return recovered.to_public_dict()

@router.post("/{mission_id}/work-units/{work_unit_id}/complete")
async def complete_work_unit(
    mission_id: str,
    work_unit_id: str,
    request: WorkUnitCompletionRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    return await _run_work_unit_execution_command(
        mission_id,
        work_unit_id,
        command="complete",
        request=request,
        user=user,
        repository=repository,
    )

@router.post("/{mission_id}/work-units/{work_unit_id}/verify")
async def verify_work_unit(
    mission_id: str,
    work_unit_id: str,
    request: WorkUnitVerificationRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    artifact_byte_verifier: ArtifactByteVerifierDep,
    grant_authorizer: VerifierWorkspaceGrantAuthorizerDep,
) -> dict:
    authorize_verifier(user)
    if user.get("role") != "admin" and request.verifier_id != str(user["id"]):
        raise HTTPException(status_code=403, detail="Verifier identity mismatch")
    await _authorize_verifier_mission(
        mission_id,
        user=user,
        repository=repository,
        grant_authorizer=grant_authorizer,
    )
    work_unit = await repository.get_work_unit(work_unit_id)
    if work_unit is None or work_unit.mission_id != mission_id:
        raise HTTPException(status_code=404, detail="WorkUnit not found")
    service = MissionService(
        repository,
        artifact_byte_verifier=artifact_byte_verifier,
    )
    try:
        evidence, updated_work_unit, updated_mission = (
            await service.verify_work_unit(
                mission_id,
                work_unit_id,
                criterion_id=request.criterion_id,
                verifier_id=request.verifier_id,
                verifier_version=request.verifier_version,
                configuration_digest=request.configuration_digest,
                verdict=EvidenceVerdict(request.verdict),
                artifact_refs=request.artifact_refs,
                summary=request.summary,
                actor=build_verifier_actor(user),
            )
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ArtifactBytesUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail=str(exc),
        ) from exc
    except ArtifactIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "evidence": evidence.to_public_dict(),
        "workUnit": updated_work_unit.to_public_dict(),
        "mission": updated_mission.to_public_dict(),
    }

@router.post("/{mission_id}/work-units/{work_unit_id}/fail")
async def fail_work_unit(
    mission_id: str,
    work_unit_id: str,
    request: WorkUnitExecutionRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    return await _run_work_unit_execution_command(
        mission_id,
        work_unit_id,
        command="fail",
        request=request,
        user=user,
        repository=repository,
    )

@router.post("/{mission_id}/work-units/{work_unit_id}/retry")
async def retry_work_unit(
    mission_id: str,
    work_unit_id: str,
    request: WorkUnitExecutionRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    return await _run_work_unit_execution_command(
        mission_id,
        work_unit_id,
        command="retry",
        request=request,
        user=user,
        repository=repository,
    )
