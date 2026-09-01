from __future__ import annotations

from app.repositories import MissionRepository
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
        artifact_byte_verifier: ArtifactByteVerifier | None = None,
        agent_binding_resolver: AgentBindingResolver | None = None,
        verification_policy_resolver: VerificationPolicyResolver | None = None,
        verification_evaluator: VerificationEvaluator | None = None,
        evidence_integrity_hasher: EvidenceIntegrityHasher | None = None,
    ) -> None:
        self._repository = repository or MissionRepository()
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
