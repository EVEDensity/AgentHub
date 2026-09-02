from __future__ import annotations

from datetime import datetime, timezone

from app.domain import (
    ActorRef,
    Mission,
    SessionEvent,
    SessionEventType,
)
from app.repositories import MissionRepository, SessionEventRepository
from app.services.agent_binding_service import AgentBindingResolver
from app.services.artifact_integrity_service import ArtifactByteVerifier
from app.services.evidence_integrity_service import (
    EvidenceIntegrityHasher,
    Sha256EvidenceIntegrityHasher,
)
from app.services.mission._artifacts_mixin import MissionArtifactsMixin
from app.services.mission._checkpoint_mixin import MissionCheckpointMixin
from app.services.mission._decisions_mixin import MissionDecisionsMixin
from app.services.mission._fork_mixin import MissionForkMixin
from app.services.mission._lifecycle_mixin import MissionLifecycleMixin
from app.services.mission._runner_claim_mixin import MissionRunnerClaimMixin
from app.services.mission._types import *  # noqa: F401,F403
from app.services.mission._verify_mixin import MissionVerifyMixin
from app.services.mission._work_unit_lifecycle_mixin import MissionWorkUnitLifecycleMixin
from app.services.verification_evaluator_service import (
    StrictVerificationEvaluator,
    VerificationEvaluator,
)
from app.services.verification_policy_service import (
    StrictVerificationPolicyResolver,
    VerificationPolicyResolver,
)


class MissionService(
    MissionLifecycleMixin,
    MissionForkMixin,
    MissionWorkUnitLifecycleMixin,
    MissionRunnerClaimMixin,
    MissionCheckpointMixin,
    MissionArtifactsMixin,
    MissionDecisionsMixin,
    MissionVerifyMixin,
):
    """Composed mission orchestration service."""

    def __init__(
        self,
        repository: MissionRepository | None = None,
        *,
        session_event_repository: SessionEventRepository | None = None,
        artifact_byte_verifier: ArtifactByteVerifier | None = None,
        agent_binding_resolver: AgentBindingResolver | None = None,
        verification_policy_resolver: VerificationPolicyResolver | None = None,
        verification_evaluator: VerificationEvaluator | None = None,
        evidence_integrity_hasher: EvidenceIntegrityHasher | None = None,
    ) -> None:
        self._repository = repository or MissionRepository()
        self._session_events = session_event_repository
        self._artifact_byte_verifier = artifact_byte_verifier
        self._agent_binding_resolver = agent_binding_resolver
        self._verification_policy_resolver = (
            verification_policy_resolver or StrictVerificationPolicyResolver()
        )
        self._verification_evaluator = (
            verification_evaluator or StrictVerificationEvaluator()
        )
        self._evidence_integrity_hasher = (
            evidence_integrity_hasher or Sha256EvidenceIntegrityHasher()
        )

    # ── Session event bridge (best-effort, never blocks) ───────────
    async def _emit_session_terminal(
        self,
        mission: Mission,
        *,
        previous_status: str,
        reason: str | None = None,
        extra: dict | None = None,
    ) -> None:
        """Write a ``mission.completed`` session event when a Mission
        reaches a terminal status (SUCCEEDED / FAILED / CANCELLED).

        Best-effort: missing ``session_event_repository``, missing
        ``session_id`` on the Mission source, or any write failure are
        all silently ignored — the Mission lifecycle must never depend
        on session event delivery.
        """
        if self._session_events is None:
            return
        session_id = mission.source.session_id if mission.source else None
        if not session_id:
            return
        try:
            payload: dict = {
                "mission_id": mission.id,
                "terminal_status": mission.status.value,
                "previous_status": previous_status,
            }
            if reason:
                payload["reason"] = reason[:500]
            if extra:
                payload.update(extra)
            evt = SessionEvent(
                id=new_identifier("evt"),
                session_id=session_id,
                event_type=SessionEventType.MISSION_COMPLETED,
                actor=ActorRef(type="adapter", id="mission_service"),
                payload=payload,
                created_at=datetime.now(timezone.utc),
            )
            await self._session_events.add_session_event(evt)
        except Exception:  # noqa: BLE001 - observe, never block
            pass
