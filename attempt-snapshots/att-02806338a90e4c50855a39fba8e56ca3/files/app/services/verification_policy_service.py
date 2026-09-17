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

_ARTIFACT_SET_V1 = "artifact-set.v1"
_ARTIFACT_SET_V2 = "artifact-set.v2"
_TEST_RUN_V1 = "test-run.v1"
_BUILD_ARTIFACT_V1 = "build-artifact.v1"
_SECURITY_SCAN_V1 = "security-scan.v1"

# Evaluator-id → required policy fields.  The resolver dispatches validation
# and admission checks by this map.  Adding a new evaluator is two edits:
# (1) append its id and field set here, (2) register an evaluator
# implementation in verification_evaluator_service.py.
_EVALUATOR_POLICY_KEYS: dict[str, frozenset[str]] = {
    _ARTIFACT_SET_V1: frozenset(
        {
            "evaluator",
            "workUnitKinds",
            "minimumArtifacts",
            "requiredArtifactKinds",
        }
    ),
    _ARTIFACT_SET_V2: frozenset(
        {
            "evaluator",
            "workUnitKinds",
            "minimumArtifacts",
            "requiredArtifactKinds",
            "optionalArtifactKinds",
            "maxArtifactSizeBytes",
        }
    ),
    _TEST_RUN_V1: frozenset(
        {
            "evaluator",
            "workUnitKinds",
            "minimumTestResults",
            "minimumPassRate",
        }
    ),
    _BUILD_ARTIFACT_V1: frozenset(
        {
            "evaluator",
            "workUnitKinds",
            "minimumBuildArtifacts",
            "maxArtifactSizeBytes",
        }
    ),
    _SECURITY_SCAN_V1: frozenset(
        {
            "evaluator",
            "workUnitKinds",
            "minimumScanReports",
            "maxSeverity",
        }
    ),
}
_SUPPORTED_EVALUATORS = frozenset(_EVALUATOR_POLICY_KEYS)
_DEFAULT_POLICY_KEYS = frozenset({"evaluator", "workUnitKinds"})


@dataclass(frozen=True, slots=True)
class ArtifactSetEvaluationPlan:
    criterion_id: str
    evaluator: str
    configuration_digest: str
    minimum_artifacts: int
    required_artifact_kinds: tuple[str, ...]
    # v2 extensions — omitted by v1 plans, always present for v2.
    optional_artifact_kinds: tuple[str, ...] = ()
    max_artifact_size_bytes: int | None = None

    def to_public_dict(self) -> dict:
        parameters: dict[str, object] = {
            "minimumArtifacts": self.minimum_artifacts,
            "requiredArtifactKinds": list(self.required_artifact_kinds),
        }
        if self.optional_artifact_kinds:
            parameters["optionalArtifactKinds"] = list(self.optional_artifact_kinds)
        if self.max_artifact_size_bytes is not None:
            parameters["maxArtifactSizeBytes"] = self.max_artifact_size_bytes
        return {
            "status": "ready",
            "criterionId": self.criterion_id,
            "evaluator": self.evaluator,
            "configurationDigest": self.configuration_digest,
            "parameters": parameters,
        }


@dataclass(frozen=True, slots=True)
class TestRunEvaluationPlan:
    """Plan for a test-run.v1 evaluator.

    Test-run evaluators verify that at least ``minimum_test_results``
    ``ArtifactKind.TEST_RESULT`` artifacts exist and that their combined
    pass-rate meets ``minimum_pass_rate`` (a float in [0.0, 1.0]).
    The evaluator does *not* execute the test suite itself — the test-run
    is expected to have completed and deposited TEST_RESULT artifacts
    upstream.
    """

    criterion_id: str
    evaluator: str
    configuration_digest: str
    minimum_test_results: int
    minimum_pass_rate: float

    def to_public_dict(self) -> dict:
        return {
            "status": "ready",
            "criterionId": self.criterion_id,
            "evaluator": self.evaluator,
            "configurationDigest": self.configuration_digest,
            "parameters": {
                "minimumTestResults": self.minimum_test_results,
                "minimumPassRate": self.minimum_pass_rate,
            },
        }


@dataclass(frozen=True, slots=True)
class BuildArtifactEvaluationPlan:
    """Plan for a build-artifact.v1 evaluator.

    Requires at least ``minimum_build_artifacts`` ``ArtifactKind.BUILD``
    artifacts, each of which must not exceed ``max_artifact_size_bytes``.
    Build evaluators verify that the compiled output of a work-unit exists
    and is within expected size bounds — they do not execute the build
    themselves (the Runner/Harness layer does that upstream).
    """

    criterion_id: str
    evaluator: str
    configuration_digest: str
    minimum_build_artifacts: int
    max_artifact_size_bytes: int

    def to_public_dict(self) -> dict:
        return {
            "status": "ready",
            "criterionId": self.criterion_id,
            "evaluator": self.evaluator,
            "configurationDigest": self.configuration_digest,
            "parameters": {
                "minimumBuildArtifacts": self.minimum_build_artifacts,
                "maxArtifactSizeBytes": self.max_artifact_size_bytes,
            },
        }


@dataclass(frozen=True, slots=True)
class SecurityScanEvaluationPlan:
    """Plan for a security-scan.v1 evaluator.

    Requires at least ``minimum_scan_reports`` ``ArtifactKind.REPORT``
    artifacts (security-scan result reports).  ``max_severity`` is the
    highest accepted severity grade on the standard CVSS-style scale
    (0=None, 1=info, 2=low, 3=medium, 4=high, 5=critical).  The evaluator
    only counts the presence of report artifacts — severity arithmetic is
    delegated to the verifier worker, which parses the report body.
    """

    criterion_id: str
    evaluator: str
    configuration_digest: str
    minimum_scan_reports: int
    max_severity: int

    def to_public_dict(self) -> dict:
        return {
            "status": "ready",
            "criterionId": self.criterion_id,
            "evaluator": self.evaluator,
            "configurationDigest": self.configuration_digest,
            "parameters": {
                "minimumScanReports": self.minimum_scan_reports,
                "maxSeverity": self.max_severity,
            },
        }


@dataclass(frozen=True, slots=True)
class EvaluationPolicyDecision:
    plan: (
        ArtifactSetEvaluationPlan
        | TestRunEvaluationPlan
        | BuildArtifactEvaluationPlan
        | SecurityScanEvaluationPlan
        | None
    ) = None
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
    def ready(
        cls,
        plan: ArtifactSetEvaluationPlan | TestRunEvaluationPlan,
    ) -> EvaluationPolicyDecision:
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
    evaluator: str
    work_unit_kinds: tuple[str, ...]
    criterion_id: str
    # artifact-set fields
    minimum_artifacts: int | None = None
    required_artifact_kinds: tuple[str, ...] = ()
    optional_artifact_kinds: tuple[str, ...] = ()
    max_artifact_size_bytes: int | None = None
    # test-run fields
    minimum_test_results: int | None = None
    minimum_pass_rate: float | None = None
    # build-artifact fields
    minimum_build_artifacts: int | None = None
    build_max_size_bytes: int | None = None
    # security-scan fields
    minimum_scan_reports: int | None = None
    max_severity: int | None = None

    def normalized_configuration(self) -> dict:
        common = {
            "evaluator": self.evaluator,
            "workUnitKinds": list(self.work_unit_kinds),
        }
        if self.evaluator == _TEST_RUN_V1:
            return {
                **common,
                "minimumTestResults": self.minimum_test_results,
                "minimumPassRate": self.minimum_pass_rate,
            }
        if self.evaluator == _BUILD_ARTIFACT_V1:
            return {
                **common,
                "minimumBuildArtifacts": self.minimum_build_artifacts,
                "maxArtifactSizeBytes": self.build_max_size_bytes,
            }
        if self.evaluator == _SECURITY_SCAN_V1:
            return {
                **common,
                "minimumScanReports": self.minimum_scan_reports,
                "maxSeverity": self.max_severity,
            }
        # artifact-set.v1 / v2
        config: dict[str, object] = {
            **common,
            "minimumArtifacts": self.minimum_artifacts,
            "requiredArtifactKinds": list(self.required_artifact_kinds),
        }
        if self.evaluator == _ARTIFACT_SET_V2:
            config["optionalArtifactKinds"] = list(self.optional_artifact_kinds)
            config["maxArtifactSizeBytes"] = self.max_artifact_size_bytes
        return config


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
            # Only configurations that carry the default policy markers
            # are treated as explicit verification policies.  Anything else
            # is ignored — keeps legacy criteria with unrelated metadata
            # flowing through unchanged.
            if not (_DEFAULT_POLICY_KEYS & configuration.keys()):
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
        if policy.evaluator not in _SUPPORTED_EVALUATORS:
            return EvaluationPolicyDecision.inconclusive(
                EvaluationPolicyReason.UNSUPPORTED_EVALUATOR,
                criterion_ids=[policy.criterion_id],
            )
        artifact_kinds = {artifact.kind.value for artifact in artifacts}
        if not _policy_satisfied(policy, artifacts, artifact_kinds):
            return EvaluationPolicyDecision.inconclusive(
                EvaluationPolicyReason.ARTIFACT_REQUIREMENTS_NOT_MET,
                criterion_ids=[policy.criterion_id],
            )

        encoded_configuration = json.dumps(
            policy.normalized_configuration(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = "sha256:" + hashlib.sha256(encoded_configuration).hexdigest()

        if policy.evaluator == _TEST_RUN_V1:
            assert policy.minimum_test_results is not None
            assert policy.minimum_pass_rate is not None
            return EvaluationPolicyDecision.ready(
                TestRunEvaluationPlan(
                    criterion_id=policy.criterion_id,
                    evaluator=policy.evaluator,
                    configuration_digest=digest,
                    minimum_test_results=policy.minimum_test_results,
                    minimum_pass_rate=policy.minimum_pass_rate,
                )
            )
        if policy.evaluator == _BUILD_ARTIFACT_V1:
            assert policy.minimum_build_artifacts is not None
            assert policy.build_max_size_bytes is not None
            return EvaluationPolicyDecision.ready(
                BuildArtifactEvaluationPlan(
                    criterion_id=policy.criterion_id,
                    evaluator=policy.evaluator,
                    configuration_digest=digest,
                    minimum_build_artifacts=policy.minimum_build_artifacts,
                    max_artifact_size_bytes=policy.build_max_size_bytes,
                )
            )
        if policy.evaluator == _SECURITY_SCAN_V1:
            assert policy.minimum_scan_reports is not None
            assert policy.max_severity is not None
            return EvaluationPolicyDecision.ready(
                SecurityScanEvaluationPlan(
                    criterion_id=policy.criterion_id,
                    evaluator=policy.evaluator,
                    configuration_digest=digest,
                    minimum_scan_reports=policy.minimum_scan_reports,
                    max_severity=policy.max_severity,
                )
            )
        # artifact-set.v1 / v2
        assert policy.minimum_artifacts is not None
        return EvaluationPolicyDecision.ready(
            ArtifactSetEvaluationPlan(
                criterion_id=policy.criterion_id,
                evaluator=policy.evaluator,
                configuration_digest=digest,
                minimum_artifacts=policy.minimum_artifacts,
                required_artifact_kinds=policy.required_artifact_kinds,
                optional_artifact_kinds=policy.optional_artifact_kinds,
                max_artifact_size_bytes=policy.max_artifact_size_bytes,
            )
        )


def _parse_policy(criterion_id: str, configuration: dict) -> _ParsedPolicy:
    evaluator = _required_string(configuration, "evaluator")
    allowed_keys = _EVALUATOR_POLICY_KEYS.get(evaluator)
    if allowed_keys is None:
        # Unknown evaluator — still validate against the v1 field set so we
        # can tell "weird fields" apart from "recognisable field layout but
        # unsupported evaluator".  The latter returns UNSUPPORTED_EVALUATOR
        # from the resolver, the former returns INVALID_CONFIGURATION.
        allowed_keys = _EVALUATOR_POLICY_KEYS[_ARTIFACT_SET_V1]
    if set(configuration) != allowed_keys:
        raise ValueError("evaluation policy requires exactly the supported fields")

    work_unit_kinds = _required_unique_strings(
        configuration,
        "workUnitKinds",
        maximum_items=32,
    )

    if evaluator == _TEST_RUN_V1:
        minimum_test_results = configuration["minimumTestResults"]
        if (
            isinstance(minimum_test_results, bool)
            or not isinstance(minimum_test_results, int)
            or not 1 <= minimum_test_results <= 200
        ):
            raise ValueError("minimumTestResults must be between 1 and 200")
        minimum_pass_rate = configuration["minimumPassRate"]
        if (
            isinstance(minimum_pass_rate, bool)
            or not isinstance(minimum_pass_rate, (int, float))
            or not 0.0 <= float(minimum_pass_rate) <= 1.0
        ):
            raise ValueError("minimumPassRate must be between 0.0 and 1.0")
        return _ParsedPolicy(
            criterion_id=criterion_id,
            evaluator=evaluator,
            work_unit_kinds=tuple(sorted(work_unit_kinds)),
            minimum_test_results=minimum_test_results,
            minimum_pass_rate=float(minimum_pass_rate),
        )

    if evaluator == _BUILD_ARTIFACT_V1:
        minimum_build = configuration["minimumBuildArtifacts"]
        if (
            isinstance(minimum_build, bool)
            or not isinstance(minimum_build, int)
            or not 1 <= minimum_build <= 200
        ):
            raise ValueError("minimumBuildArtifacts must be between 1 and 200")
        max_size = configuration["maxArtifactSizeBytes"]
        if (
            isinstance(max_size, bool)
            or not isinstance(max_size, int)
            or max_size <= 0
            or max_size > 100_000_000_000  # 100 GB
        ):
            raise ValueError("maxArtifactSizeBytes must be 1..100000000000")
        return _ParsedPolicy(
            criterion_id=criterion_id,
            evaluator=evaluator,
            work_unit_kinds=tuple(sorted(work_unit_kinds)),
            minimum_build_artifacts=minimum_build,
            build_max_size_bytes=max_size,
        )

    if evaluator == _SECURITY_SCAN_V1:
        minimum_scan = configuration["minimumScanReports"]
        if (
            isinstance(minimum_scan, bool)
            or not isinstance(minimum_scan, int)
            or not 1 <= minimum_scan <= 200
        ):
            raise ValueError("minimumScanReports must be between 1 and 200")
        max_severity = configuration["maxSeverity"]
        if (
            isinstance(max_severity, bool)
            or not isinstance(max_severity, int)
            or not 0 <= max_severity <= 5
        ):
            raise ValueError("maxSeverity must be between 0 and 5 (CVSS-style)")
        return _ParsedPolicy(
            criterion_id=criterion_id,
            evaluator=evaluator,
            work_unit_kinds=tuple(sorted(work_unit_kinds)),
            minimum_scan_reports=minimum_scan,
            max_severity=max_severity,
        )

    # artifact-set.v1 / v2
    minimum_artifacts = configuration["minimumArtifacts"]
    if (
        isinstance(minimum_artifacts, bool)
        or not isinstance(minimum_artifacts, int)
        or not 1 <= minimum_artifacts <= 200
    ):
        raise ValueError("minimumArtifacts must be between 1 and 200")
    required_artifact_kinds = _required_artifact_kinds(
        configuration, field="requiredArtifactKinds", allow_empty=True
    )

    optional_kinds: tuple[str, ...] = ()
    max_size: int | None = None
    if evaluator == _ARTIFACT_SET_V2:
        optional_kinds = _required_artifact_kinds(
            configuration, field="optionalArtifactKinds", allow_empty=True
        )
        max_size_value = configuration["maxArtifactSizeBytes"]
        if (
            isinstance(max_size_value, bool)
            or not isinstance(max_size_value, int)
            or max_size_value < 0
            or max_size_value > 100_000_000_000  # 100 GB
        ):
            raise ValueError("maxArtifactSizeBytes must be 0..100000000000")
        max_size = max_size_value

    return _ParsedPolicy(
        criterion_id=criterion_id,
        evaluator=evaluator,
        work_unit_kinds=tuple(sorted(work_unit_kinds)),
        minimum_artifacts=minimum_artifacts,
        required_artifact_kinds=tuple(sorted(required_artifact_kinds)),
        optional_artifact_kinds=tuple(sorted(optional_kinds)),
        max_artifact_size_bytes=max_size,
    )


def _policy_satisfied(
    policy: _ParsedPolicy,
    artifacts: tuple[Artifact, ...],
    artifact_kinds: set[str],
) -> bool:
    if policy.evaluator == _TEST_RUN_V1:
        test_result_count = sum(
            1 for artifact in artifacts if artifact.kind == ArtifactKind.TEST_RESULT
        )
        assert policy.minimum_test_results is not None
        return test_result_count >= policy.minimum_test_results
    if policy.evaluator == _BUILD_ARTIFACT_V1:
        assert policy.minimum_build_artifacts is not None
        assert policy.build_max_size_bytes is not None
        build_artifacts = [a for a in artifacts if a.kind == ArtifactKind.BUILD]
        if len(build_artifacts) < policy.minimum_build_artifacts:
            return False
        return all(a.size_bytes <= policy.build_max_size_bytes for a in build_artifacts)
    if policy.evaluator == _SECURITY_SCAN_V1:
        assert policy.minimum_scan_reports is not None
        scan_count = sum(1 for a in artifacts if a.kind == ArtifactKind.REPORT)
        return scan_count >= policy.minimum_scan_reports
    # artifact-set
    assert policy.minimum_artifacts is not None
    if len(artifacts) < policy.minimum_artifacts:
        return False
    if not set(policy.required_artifact_kinds) <= artifact_kinds:
        return False
    if policy.evaluator != _ARTIFACT_SET_V2:
        return True
    # v2: any artifact over the size cap fails the policy.
    if policy.max_artifact_size_bytes is not None:
        for artifact in artifacts:
            if artifact.size_bytes > policy.max_artifact_size_bytes:
                return False
    return True


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


def _required_artifact_kinds(
    configuration: dict,
    *,
    field: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    value = configuration[field]
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    if not allow_empty and len(value) == 0:
        raise ValueError(f"{field} must be non-empty")
    if len(value) > len(ArtifactKind):
        raise ValueError(f"{field} must be a bounded array")
    allowed = {kind.value for kind in ArtifactKind}
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in allowed:
            raise ValueError(f"{field} contains an unknown kind")
        normalized.append(item)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} entries must be unique")
    return tuple(normalized)