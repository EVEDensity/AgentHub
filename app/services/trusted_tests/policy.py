"""One aggregate acceptance policy for attested trusted-local test bundles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.trusted_tests.models import Digest, Identifier

TRUSTED_TEST_RUN_V1 = "trusted-test-run.v1"


class TrustedTestParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    minimumTestResults: Literal[1]
    minimumPassRate: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    expectedInputSnapshotDigest: Digest
    testProfileDigest: Digest
    producerId: Identifier
    keyId: Identifier
    executionMode: Literal["trusted-local"]

    @model_validator(mode="before")
    @classmethod
    def exact_report_count(cls, value):
        if not isinstance(value, dict) or type(value.get("minimumTestResults")) is not int:
            raise ValueError("minimumTestResults must be the integer one")
        return value


TRUSTED_TEST_POLICY_KEYS = frozenset({"evaluator", "workUnitKinds", *TrustedTestParameters.model_fields})


@dataclass(frozen=True, slots=True)
class TrustedTestEvaluationPlan:
    criterion_id: str
    evaluator: str
    configuration_digest: str
    parameters: TrustedTestParameters
    contract_id: str
    contract_version: int

    def to_public_dict(self) -> dict:
        return {"status": "ready", "criterionId": self.criterion_id, "evaluator": self.evaluator,
                "configurationDigest": self.configuration_digest, "parameters": self.parameters.model_dump(mode="json")}


@dataclass(frozen=True, slots=True)
class _TrustedParsedPolicy:
    criterion_id: str
    evaluator: str
    work_unit_kinds: tuple[str, ...]
    parameters: TrustedTestParameters

    def normalized_configuration(self) -> dict:
        return {"evaluator": self.evaluator, "workUnitKinds": list(self.work_unit_kinds),
                **self.parameters.model_dump(mode="json")}


def parse_with_trusted_policy(criterion_id, configuration, legacy_parser):
    if configuration.get("evaluator") != TRUSTED_TEST_RUN_V1:
        return legacy_parser(criterion_id, configuration)
    if set(configuration) != TRUSTED_TEST_POLICY_KEYS:
        raise ValueError("trusted test policy requires exactly its versioned fields")
    kinds = configuration.get("workUnitKinds")
    if not isinstance(kinds, list) or not 1 <= len(kinds) <= 32:
        raise ValueError("trusted test policy requires bounded WorkUnit kinds")
    if any(not isinstance(kind, str) or not kind.strip() or len(kind) > 255 for kind in kinds):
        raise ValueError("trusted test policy WorkUnit kinds must be identifiers")
    if len(set(kinds)) != len(kinds):
        raise ValueError("trusted test policy WorkUnit kinds must be unique")
    values = {key: configuration[key] for key in TrustedTestParameters.model_fields}
    if type(values["minimumTestResults"]) is not int:
        raise ValueError("minimumTestResults must be the integer one")
    return _TrustedParsedPolicy(criterion_id, TRUSTED_TEST_RUN_V1, tuple(sorted(kinds)), TrustedTestParameters.model_validate(values))


def satisfied_with_trusted_policy(policy, artifacts, artifact_kinds, legacy_predicate):
    if policy.evaluator != TRUSTED_TEST_RUN_V1:
        return legacy_predicate(policy, artifacts, artifact_kinds)
    # Incomplete bundles reach the existing explicit Decision path. Contents,
    # exact refs and signatures are checked only after actual byte verification.
    kinds = [artifact.kind.value for artifact in artifacts]
    return len(kinds) == 5 and sorted(kinds) == ["diff", "log", "report", "report", "test-result"]


def _unsupported_policy(*_arguments):
    raise ValueError("configuration digest requires a trusted test policy")


def policy_configuration_digest(configuration: dict) -> str:
    """The same canonical policy digest used by Mission Control admission."""
    policy = parse_with_trusted_policy("digest-only", configuration, _unsupported_policy)
    content = json.dumps(policy.normalized_configuration(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return "sha256:" + hashlib.sha256(content).hexdigest()
