"""Signed fixtures, never a production producer or execution-success stub."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.artifact_integrity_service import ArtifactByteVerification
from app.services.trusted_tests.models import (
    ExecutionManifest,
    artifact_id,
    canonical_bytes,
    content_digest,
)
from app.services.trusted_tests.policy import (
    TRUSTED_TEST_RUN_V1,
    policy_configuration_digest,
)
from app.services.trusted_tests.signing import TrustedProducerRegistry, sign_receipt
from tests.domain.factories import build_artifact
from tests.services.verification_report_fixtures import make_test_report

INPUT_DIGEST = "sha256:" + "a" * 64
CANDIDATE_DIGEST = "sha256:" + "b" * 64
PROFILE_DIGEST = "sha256:" + "c" * 64


def trust_registry(private_key=None, *, profiles=None):
    private_key = private_key or Ed25519PrivateKey.from_private_bytes(b"k" * 32)
    public = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return TrustedProducerRegistry.model_validate({"schemaVersion": 1, "keys": [{
        "producerId": "producer-1", "keyId": "key-1", "publicKeyBase64": base64.b64encode(public).decode(),
        "testProfileDigests": profiles or [PROFILE_DIGEST],
    }]})


def trusted_criterion(**updates):
    configuration = {
        "evaluator": TRUSTED_TEST_RUN_V1, "workUnitKinds": ["desktop.task"],
        "minimumTestResults": 1, "minimumPassRate": 1.0, "expectedInputSnapshotDigest": INPUT_DIGEST,
        "testProfileDigest": PROFILE_DIGEST, "producerId": "producer-1", "keyId": "key-1", "executionMode": "trusted-local",
    }
    configuration.update(updates)
    return {"id": "trusted-tests", "kind": "test", "description": "Operator-trusted local tests", "required": True,
            "configuration": configuration}


def signed_bundle(*, statuses=("passed",), report_updates=None, receipt_updates=None, manifest_updates=None,
                  signing_key=None, tamper_signature=False, scope_updates=None):
    private_key = signing_key or Ed25519PrivateKey.from_private_bytes(b"k" * 32)
    report = make_test_report(statuses, **(report_updates or {}))
    content = {"patch": b"real patch fixture", "log": b"independent process output", "report": canonical_bytes(report)}
    scope = {"schemaVersion": 1, "executionMode": "trusted-local", "missionId": "mis-1", "workUnitId": "wu-1", "attempt": 1,
             "contractId": "contract-1", "contractVersion": 1,
             "configurationDigest": policy_configuration_digest(trusted_criterion()["configuration"]),
             "inputSnapshotDigest": INPUT_DIGEST, "candidateSnapshotDigest": CANDIDATE_DIGEST, "testProfileDigest": PROFILE_DIGEST}
    scope.update(scope_updates or {})
    payload = {**scope, "receiptId": "receipt-1", "producerId": "producer-1", "keyId": "key-1",
               "patchDigest": content_digest(content["patch"]), "reportDigest": content_digest(content["report"]),
               "logDigest": content_digest(content["log"]), "exitCode": report["exitCode"], "timedOut": False,
               "startedAt": "2026-10-17T00:00:00Z", "finishedAt": "2026-10-17T00:00:01Z"}
    payload.update(receipt_updates or {})
    receipt = sign_receipt(payload, private_key)
    if tamper_signature:
        receipt = receipt.model_copy(update={"signature": base64.b64encode(b"\0" * 64).decode()})
    content["receipt"] = canonical_bytes(receipt)
    refs = {role: {"id": artifact_id("wu-1", 1, role, content_digest(body)), "digest": content_digest(body)}
            for role, body in content.items()}
    manifest = ExecutionManifest.model_validate({**scope, "manifestType": "trusted-test-execution", **refs, **(manifest_updates or {})})
    content["manifest"] = canonical_bytes(manifest)
    kinds = {"patch": "diff", "report": "test-result", "log": "log", "receipt": "report", "manifest": "report"}
    artifacts, observations, bodies = [], [], {}
    for role, body in content.items():
        digest = content_digest(body)
        identifier = artifact_id("wu-1", 1, role, digest)
        artifact = build_artifact(id=identifier, kind=kinds[role], digest=digest,
                                  content_address="local:sha256/" + digest[7:], size_bytes=len(body))
        artifacts.append(artifact)
        observations.append(ArtifactByteVerification(identifier, digest, len(body), report_content=body if role in {"report", "receipt", "manifest"} else None))
        bodies[identifier] = body
    return tuple(artifacts), tuple(observations), bodies
