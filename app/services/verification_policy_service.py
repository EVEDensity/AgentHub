"""Fail-closed resolution of explicit, deterministic verification policies."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.domain import (
    Artifact,
    ArtifactKind,
    EvaluationPolicyReason,
    MissionContract,
    WorkUnit,
)

_ARTIFACT_SET_EVALUATOR = "artifact-set.v1"
_POLICY_KEYS = frozenset(
    {
        "evaluator",
        "workUnitKinds",
        "minimumArtifacts",
        "requiredArtifactKinds",
    }
)


@dataclass(frozen=True, slots=True)
class ArtifactSetEvaluationPlan:
    criterion_id: str
    evaluator: str
    configuration_digest: str
    minimum_artifacts: int
    required_artifact_kinds: tuple[str, ...]

    def to_public_dict(self) -> dict:
        return {
            "status": "ready",
            "criterionId": self.criterion_id,
            "evaluator": self.evaluator,
            "configurationDigest": self.configuration_digest,
            "parameters": {
                "minimumArtifacts": self.minimum_artifacts,
                "requiredArtifactKinds": list(self.required_artifact_kinds),
            },
        }


@dataclass(frozen=True, slots=True)
class EvaluationPolicyDecision:
    plan: ArtifactSetEvaluationPlan | None = None
    reason: EvaluationPolicyReason | None = None
    criterion_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.plan is None) == (self.reason is None):
            raise ValueError(
                "evaluation policy decision requires exactly one plan or reason"
            )
        if self.plan is not None and self.criterion_ids:
            raise ValueError("ready evaluation policy cannot carry criterion IDs")
        if len(self.criterion_ids) != len(set(self.criterion_ids)) or any(
            not criterion_id for criterion_id in self.criterion_ids
        ):
            raise ValueError("evaluation policy criterion IDs must be unique")

    @classmethod
    def ready(cls, plan: ArtifactSetEvaluationPlan) -> EvaluationPolicyDecision:
        return cls(plan=plan)

    @classmethod
    def inconclusive(
        cls,
        reason: EvaluationPolicyReason,
        *,
        criterion_ids: Sequence[str] = (),
    ) -> EvaluationPolicyDecision:
        return cls(
            reason=reason,
            criterion_ids=tuple(sorted(criterion_ids)),
        )

    def to_public_dict(self) -> dict:
        if self.plan is not None:
            return self.plan.to_public_dict()
        assert self.reason is not None
        return {
            "status": "inconclusive",
            "reasonCode": self.reason.value,
            "criterionIds": list(self.criterion_ids),
        }


class VerificationPolicyResolver(Protocol):
    def resolve(
        self,
        contract: MissionContract,
        work_unit: WorkUnit,
        artifacts: tuple[Artifact, ...],
    ) -> EvaluationPolicyDecision: ...


@dataclass(frozen=True, slots=True)
class _ParsedPolicy:
    criterion_id: str
    evaluator: str
    work_unit_kinds: tuple[str, ...]
    minimum_artifacts: int
    required_artifact_kinds: tuple[str, ...]

    def normalized_configuration(self) -> dict:
        return {
            "evaluator": self.evaluator,
            "workUnitKinds": list(self.work_unit_kinds),
            "minimumArtifacts": self.minimum_artifacts,
            "requiredArtifactKinds": list(self.required_artifact_kinds),
        }


class StrictVerificationPolicyResolver:
    """Resolve only explicitly bound, schema-valid deterministic evaluators."""

    def resolve(
        self,
        contract: MissionContract,
        work_unit: WorkUnit,
        artifacts: tuple[Artifact, ...],
    ) -> EvaluationPolicyDecision:
        applicable: list[_ParsedPolicy] = []
        configured_criterion_ids: list[str] = []
        invalid_criterion_ids: list[str] = []
        for criterion in contract.acceptance_criteria:
            configuration = criterion.to_public_dict().get("configuration", {})
            if not isinstance(configuration, dict):
                invalid_criterion_ids.append(criterion.id)
                continue
            has_policy_key = bool(_POLICY_KEYS & configuration.keys())
            if not has_policy_key:
                continue
            configured_criterion_ids.append(criterion.id)
            try:
                parsed = _parse_policy(criterion.id, configuration)
            except ValueError:
                invalid_criterion_ids.append(criterion.id)
                continue
            if work_unit.kind in parsed.work_unit_kinds:
                applicable.append(parsed)

        if invalid_criterion_ids:
            return EvaluationPolicyDecision.inconclusive(
                EvaluationPolicyReason.INVALID_CONFIGURATION,
                criterion_ids=invalid_criterion_ids,
            )
        if not applicable:
            return EvaluationPolicyDecision.inconclusive(
                EvaluationPolicyReason.NO_APPLICABLE_POLICY,
                criterion_ids=(
                    configured_criterion_ids
                    or [criterion.id for criterion in contract.acceptance_criteria]
                ),
            )
        if len(applicable) != 1:
            return EvaluationPolicyDecision.inconclusive(
                EvaluationPolicyReason.AMBIGUOUS_POLICY,
                criterion_ids=[policy.criterion_id for policy in applicable],
            )

        policy = applicable[0]
        if policy.evaluator != _ARTIFACT_SET_EVALUATOR:
            return EvaluationPolicyDecision.inconclusive(
                EvaluationPolicyReason.UNSUPPORTED_EVALUATOR,
                criterion_ids=[policy.criterion_id],
            )
        artifact_kinds = {artifact.kind.value for artifact in artifacts}
        if (
            len(artifacts) < policy.minimum_artifacts
            or not set(policy.required_artifact_kinds) <= artifact_kinds
        ):
            return EvaluationPolicyDecision.inconclusive(
                EvaluationPolicyReason.ARTIFACT_REQUIREMENTS_NOT_MET,
                criterion_ids=[policy.criterion_id],
            )

        encoded_configuration = json.dumps(
            policy.normalized_configuration(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return EvaluationPolicyDecision.ready(
            ArtifactSetEvaluationPlan(
                criterion_id=policy.criterion_id,
                evaluator=policy.evaluator,
                configuration_digest=(
                    "sha256:" + hashlib.sha256(encoded_configuration).hexdigest()
                ),
                minimum_artifacts=policy.minimum_artifacts,
                required_artifact_kinds=policy.required_artifact_kinds,
            )
        )


def _parse_policy(criterion_id: str, configuration: dict) -> _ParsedPolicy:
    if set(configuration) != _POLICY_KEYS:
        raise ValueError("evaluation policy requires exactly the supported fields")
    evaluator = _required_string(configuration, "evaluator")
    work_unit_kinds = _required_unique_strings(
        configuration,
        "workUnitKinds",
        maximum_items=32,
    )
    minimum_artifacts = configuration["minimumArtifacts"]
    if (
        isinstance(minimum_artifacts, bool)
        or not isinstance(minimum_artifacts, int)
        or not 1 <= minimum_artifacts <= 200
    ):
        raise ValueError("minimumArtifacts must be between 1 and 200")
    required_artifact_kinds = _required_artifact_kinds(configuration)
    return _ParsedPolicy(
        criterion_id=criterion_id,
        evaluator=evaluator,
        work_unit_kinds=tuple(sorted(work_unit_kinds)),
        minimum_artifacts=minimum_artifacts,
        required_artifact_kinds=tuple(sorted(required_artifact_kinds)),
    )


def _required_string(configuration: dict, field: str) -> str:
    value = configuration.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _required_unique_strings(
    configuration: dict,
    field: str,
    *,
    maximum_items: int,
) -> tuple[str, ...]:
    value = configuration.get(field)
    if not isinstance(value, list) or not 1 <= len(value) <= maximum_items:
        raise ValueError(f"{field} must be a bounded non-empty array")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 255:
            raise ValueError(f"{field} entries must be non-empty strings")
        normalized.append(item.strip())
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} entries must be unique")
    return tuple(normalized)


def _required_artifact_kinds(configuration: dict) -> tuple[str, ...]:
    value = configuration["requiredArtifactKinds"]
    if not isinstance(value, list) or len(value) > len(ArtifactKind):
        raise ValueError("requiredArtifactKinds must be a bounded array")
    allowed = {kind.value for kind in ArtifactKind}
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in allowed:
            raise ValueError("requiredArtifactKinds contains an unknown kind")
        normalized.append(item)
    if len(normalized) != len(set(normalized)):
        raise ValueError("requiredArtifactKinds entries must be unique")
    return tuple(normalized)
