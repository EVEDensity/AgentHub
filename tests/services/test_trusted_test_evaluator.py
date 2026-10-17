from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.domain import MissionContract
from app.services.trusted_tests.models import artifact_id, canonical_bytes
from app.services.trusted_tests.signing import (
    TRUST_KEYS_FILE_ENV,
)
from app.services.verification_evaluator_service import (
    StrictVerificationEvaluator,
    VerificationEvaluationError,
)
from app.services.verification_policy_service import StrictVerificationPolicyResolver
from tests.domain.factories import build_contract, build_work_unit
from tests.services.trusted_test_fixtures import (
    INPUT_DIGEST,
    PROFILE_DIGEST,
    signed_bundle,
    trust_registry,
    trusted_criterion,
)


class TrustedTestEvaluatorTests(unittest.TestCase):
    def plan(self, artifacts, **updates):
        contract = build_contract(acceptance_criteria=[trusted_criterion(**updates)])
        decision = StrictVerificationPolicyResolver().resolve(contract, build_work_unit(kind="desktop.task", attempt=1), artifacts)
        self.assertIsNotNone(decision.plan, decision.to_public_dict())
        return decision.plan

    def evaluate(self, *, registry=None, **updates):
        artifacts, observations, _ = signed_bundle(**updates)
        evaluator = StrictVerificationEvaluator(trusted_producers=registry or trust_registry())
        return evaluator.evaluate(self.plan(artifacts), artifacts, observations)

    def test_valid_signature_profile_snapshot_and_test_conclusion_pass(self):
        self.assertEqual(self.evaluate().verdict.value, "PASS")

    def test_untrusted_key_profile_and_tampered_signature_fail(self):
        bad_key = Ed25519PrivateKey.from_private_bytes(b"x" * 32)
        cases = ({"signing_key": bad_key}, {"tamper_signature": True},
                 {"registry": trust_registry(profiles=[INPUT_DIGEST])})
        for updates in cases:
            with self.subTest(updates=updates):
                self.assertEqual(self.evaluate(**updates).verdict.value, "FAIL")

    def test_valid_signature_cannot_rebind_snapshot_profile_attempt_or_process_result(self):
        mutations = ({"inputSnapshotDigest": "sha256:" + "d" * 64},
                     {"testProfileDigest": "sha256:" + "e" * 64}, {"attempt": 2},
                     {"workUnitId": "other"}, {"exitCode": 7},
                     {"contractId": "other-contract"}, {"contractVersion": 2},
                     {"configurationDigest": INPUT_DIGEST},
                     {"timedOut": True, "exitCode": None})
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertEqual(self.evaluate(receipt_updates=mutation).verdict.value, "FAIL")
        self.assertEqual(self.evaluate(manifest_updates={"candidateSnapshotDigest": INPUT_DIGEST}).verdict.value, "FAIL")
        for scope in ({"contractId": "other-contract"}, {"contractVersion": 2}, {"configurationDigest": INPUT_DIGEST}):
            self.assertEqual(self.evaluate(scope_updates=scope).verdict.value, "FAIL")

    def test_failing_empty_skipped_inconclusive_and_forged_reports_do_not_pass(self):
        cases = [(("failed",), {"conclusion": "failed", "exitCode": 1}),
                 (("errored",), {"conclusion": "failed", "exitCode": 2}),
                 ((), {"conclusion": "inconclusive", "exitCode": 0}),
                 (("skipped",), {"conclusion": "inconclusive", "exitCode": 0}),
                 (("passed",), {"conclusion": "inconclusive", "exitCode": None}),
                 (("passed", "failed"), {"passed": 2, "failed": 0})]
        for statuses, updates in cases:
            with self.subTest(statuses=statuses, updates=updates):
                self.assertEqual(self.evaluate(statuses=statuses, report_updates=updates).verdict.value, "FAIL")

    def test_registered_scope_and_manifest_digest_closure_are_mandatory(self):
        artifacts, observations, _ = signed_bundle()
        plan = self.plan(artifacts)
        evaluator = StrictVerificationEvaluator(trusted_producers=trust_registry())
        stale = (artifacts[0].model_copy(update={"attempt": 2}), *artifacts[1:])
        self.assertEqual(evaluator.evaluate(plan, stale, observations).verdict.value, "FAIL")
        self.assertEqual(evaluator.evaluate(plan, artifacts[:-1], observations[:-1]).verdict.value, "FAIL")
        with self.assertRaises(VerificationEvaluationError):
            evaluator.evaluate(plan, artifacts, observations[:-1])

    def test_raw_manifest_and_receipt_bytes_must_match_observed_metadata(self):
        artifacts, observations, _ = signed_bundle()
        evaluator = StrictVerificationEvaluator(trusted_producers=trust_registry())
        plan = self.plan(artifacts)
        for role in ("manifest", "receipt"):
            selected = next(item for item in observations if f"-{role}-" in item.artifact_id)
            document = json.loads(selected.report_content)
            reordered = json.dumps(dict(reversed(list(document.items()))), separators=(",", ":"), ensure_ascii=False).encode()
            self.assertEqual(len(reordered), selected.size_bytes)
            self.assertNotEqual(reordered, selected.report_content)
            changed = tuple(replace(item, report_content=reordered) if item is selected else item for item in observations)
            self.assertEqual(evaluator.evaluate(plan, artifacts, changed).verdict.value, "FAIL")

    def test_operator_file_supplies_trust_and_missing_or_invalid_configuration_fails_closed(self):
        artifacts, observations, _ = signed_bundle()
        plan = self.plan(artifacts)
        evaluator = StrictVerificationEvaluator()
        with patch.dict(os.environ, {TRUST_KEYS_FILE_ENV: ""}):
            self.assertEqual(evaluator.evaluate(plan, artifacts, observations).verdict.value, "FAIL")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operator-trust.json"
            path.write_bytes(canonical_bytes(trust_registry()))
            with patch.dict(os.environ, {TRUST_KEYS_FILE_ENV: str(path)}):
                self.assertEqual(evaluator.evaluate(plan, artifacts, observations).verdict.value, "PASS")
                path.write_text('{"schemaVersion":1,"keys":[],"publicKey":"untrusted"}', encoding="utf-8")
                self.assertEqual(evaluator.evaluate(plan, artifacts, observations).verdict.value, "FAIL")

    def test_contract_cannot_provide_a_public_key_and_legacy_policies_are_not_rewritten(self):
        artifacts, _, _ = signed_bundle()
        criterion = trusted_criterion(publicKeyBase64="self supplied")
        contract = build_contract(acceptance_criteria=[criterion])
        decision = StrictVerificationPolicyResolver().resolve(contract, build_work_unit(kind="desktop.task"), artifacts)
        self.assertEqual(decision.to_public_dict()["reasonCode"], "invalid_configuration")
        self.assertEqual(MissionContract.model_validate(build_contract()).version, 1)
        self.assertEqual(len(artifact_id("w" * 255, 2_147_483_647, "manifest", PROFILE_DIGEST)) < 255, True)
