"""Versioned, bounded conclusion reports stored as registered Artifact bytes."""

from __future__ import annotations

import json
from collections import Counter as CaseCounts
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

REPORT_MAX_BYTES = 1024 * 1024
Counter = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]
Identifier = Annotated[str, Field(min_length=1, max_length=255)]


class VerificationReportError(ValueError):
    """The report cannot establish a completed result for this attempt."""


class _Report(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schemaVersion: Literal[1]
    missionId: Identifier
    workUnitId: Identifier
    attempt: Annotated[int, Field(strict=True, ge=1)]
    conclusion: Literal["passed", "failed", "inconclusive"]
    exitCode: Annotated[int, Field(strict=True, ge=-65535, le=65535)] | None

    @model_validator(mode="before")
    @classmethod
    def validate_version(cls, value):
        if not isinstance(value, dict) or type(value.get("schemaVersion")) is not int:
            raise ValueError("report schemaVersion must be an integer")
        return value


class TestCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    id: Identifier
    status: Literal["passed", "failed", "errored", "skipped"]


class SecurityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    id: Identifier
    severity: Annotated[int, Field(strict=True, ge=1, le=5)]


class TestRunReport(_Report):
    reportType: Literal["test-run"]
    total: Counter
    passed: Counter
    failed: Counter
    errored: Counter
    skipped: Counter
    cases: Annotated[list[TestCaseResult], Field(max_length=50000)]

    @model_validator(mode="after")
    def validate_counters(self):
        if self.total != self.passed + self.failed + self.errored + self.skipped:
            raise ValueError("test report counters do not sum to total")
        counts = CaseCounts(case.status for case in self.cases)
        declared = {key: getattr(self, key) for key in ("passed", "failed", "errored", "skipped")}
        if self.total != len(self.cases) or any(counts[key] != count for key, count in declared.items()):
            raise ValueError("test report counters disagree with individual cases")
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("test report contains duplicate case identities")
        if self.conclusion == "passed" and (self.exitCode != 0 or self.failed or self.errored or not self.passed):
            raise ValueError("passing test report has an unsuccessful result")
        return self


class SecurityScanReport(_Report):
    reportType: Literal["security-scan"]
    findingCount: Counter
    maximumSeverity: Annotated[int, Field(strict=True, ge=0, le=5)]
    findings: Annotated[list[SecurityFinding], Field(max_length=10000)]

    @model_validator(mode="after")
    def validate_findings(self):
        if self.findingCount != len(self.findings) or self.maximumSeverity != max((finding.severity for finding in self.findings), default=0):
            raise ValueError("security report finding count and maximum severity disagree")
        if len({finding.id for finding in self.findings}) != len(self.findings):
            raise ValueError("security report contains duplicate finding identities")
        if self.conclusion == "passed" and self.exitCode != 0:
            raise ValueError("passing security report has an unsuccessful scanner result")
        return self


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise VerificationReportError("verification report contains duplicate JSON keys")
        value[key] = item
    return value


def _invalid_constant(value):
    raise VerificationReportError("verification report contains a non-finite JSON number")


def parse_report(content: bytes, model: type[_Report]) -> _Report:
    if not isinstance(content, bytes) or not content or len(content) > REPORT_MAX_BYTES:
        raise VerificationReportError("verification report bytes are unavailable or exceed the bound")
    try:
        document = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        return model.model_validate(document)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise VerificationReportError("verification report does not match its versioned contract") from exc
