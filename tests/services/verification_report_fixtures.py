"""Small versioned test and security reports for verifier boundary tests."""

import hashlib
import json

from app.services.artifact_integrity_service import ArtifactByteVerification
from tests.domain.factories import build_artifact


def make_test_report(statuses=("passed",), **updates):
    values = {
        "schemaVersion": 1, "reportType": "test-run", "missionId": "mis-1", "workUnitId": "wu-1",
        "attempt": 1, "conclusion": "passed", "exitCode": 0, "total": len(statuses),
        "passed": statuses.count("passed"), "failed": statuses.count("failed"),
        "errored": statuses.count("errored"), "skipped": statuses.count("skipped"),
        "cases": [{"id": f"case-{index}", "status": status} for index, status in enumerate(statuses)],
    }
    values.update(updates)
    return values


def security_report(severities=(), **updates):
    values = {
        "schemaVersion": 1, "reportType": "security-scan", "missionId": "mis-1", "workUnitId": "wu-1",
        "attempt": 1, "conclusion": "passed", "exitCode": 0, "findingCount": len(severities),
        "maximumSeverity": max(severities, default=0),
        "findings": [{"id": f"finding-{index}", "severity": severity} for index, severity in enumerate(severities)],
    }
    values.update(updates)
    return values


def report_artifact(document, kind="test-result", **updates):
    content = document if isinstance(document, bytes) else json.dumps(document).encode()
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    artifact = build_artifact(kind=kind, digest=digest, content_address="local:sha256/" + digest.removeprefix("sha256:"),
                              size_bytes=len(content), **updates)
    observation = ArtifactByteVerification(artifact.id, digest, len(content), report_content=content)
    return artifact, observation
