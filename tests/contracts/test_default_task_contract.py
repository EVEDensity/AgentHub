"""Entry points must agree on output-integrity acceptance and grant no tools."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from app.api.v1.chat_mission import _build_chat_contract
from app.cli.runtime import build_contract
from app.domain import MissionContract
from app.services.default_mission_contract import build_default_task_contract
from app.services.verification_policy_service import StrictVerificationPolicyResolver
from tests.domain.factories import build_artifact, build_work_unit

ROOT = Path(__file__).resolve().parents[2]


class DefaultTaskContractTests(unittest.TestCase):
    def test_cli_and_chat_use_same_acceptance_and_preserve_budgets(self):
        cli = build_contract("contract-cli", 60)
        chat = _build_chat_contract("contract-chat").to_public_dict()
        self.assertEqual(cli["acceptanceCriteria"], chat["acceptanceCriteria"])
        self.assertEqual(cli["budgets"], {"timeSeconds": 60, "modelCost": 1, "retries": 0})
        self.assertEqual(chat["budgets"], {"timeSeconds": 600, "modelCost": 5, "retries": 1})
        for contract in (cli, chat):
            self.assertEqual(contract["allowedCapabilities"], [])
            self.assertEqual(contract["repositoryScopes"], [])
            self.assertIn("Does not prove code correctness or test execution", contract["acceptanceCriteria"][0]["description"])

    def test_desktop_projection_executes_and_matches_python_defaults(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required to execute the Desktop contract projection")
        script = (ROOT / "desktop/ui/default-contract.js").read_text(encoding="utf-8")
        result = subprocess.run(
            [node, "-e", script + "\nprocess.stdout.write(JSON.stringify(buildDefaultTaskContract('contract-desktop')));"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(json.loads(result.stdout), build_default_task_contract("contract-desktop"))
        html = (ROOT / "desktop/ui/index.html").read_text(encoding="utf-8")
        self.assertLess(html.index('./default-contract.js'), html.index('./main.js'))
        ui = (ROOT / "desktop/ui/main.js").read_text(encoding="utf-8")
        self.assertIn('const contract = buildDefaultTaskContract(`desktop-${Date.now()}`)', ui)

    def test_fresh_contracts_cannot_mutate_a_later_request(self):
        first = build_default_task_contract("first")
        first["acceptanceCriteria"][0]["configuration"]["requiredArtifactKinds"].clear()
        second = build_default_task_contract("second")
        self.assertEqual(second["acceptanceCriteria"][0]["configuration"]["requiredArtifactKinds"], ["test-result"])

    def test_policy_requires_runner_output_kind_without_claiming_test_execution(self):
        resolver = StrictVerificationPolicyResolver()
        contract = MissionContract.model_validate(build_default_task_contract("contract-1"))
        unit = build_work_unit(kind="desktop.task", status="VERIFYING", attempt=1)
        output = build_artifact(kind="test-result")
        ready = resolver.resolve(contract, unit, (output,))
        self.assertEqual(ready.to_public_dict()["status"], "ready")
        self.assertEqual(ready.to_public_dict()["evaluator"], "artifact-set.v1")
        for artifacts in ((), (build_artifact(kind="diff"),)):
            self.assertEqual(resolver.resolve(contract, unit, artifacts).to_public_dict()["status"], "inconclusive")
