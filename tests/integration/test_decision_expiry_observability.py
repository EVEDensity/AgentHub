from __future__ import annotations

import unittest
from pathlib import Path

import yaml


class DecisionExpiryObservabilityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        rules = yaml.safe_load(
            Path("deploy/agenthub_rules.yml").read_text(encoding="utf-8")
        )
        cls.rule_groups: list[dict[str, object]] = rules["groups"]

    def test_alerts_use_only_process_local_low_cardinality_metrics(self) -> None:
        rules = {
            rule["alert"]: rule
            for group in self.rule_groups
            for rule in group["rules"]
            if rule.get("alert", "").startswith("DecisionExpiry")
        }
        self.assertEqual(
            set(rules),
            {"DecisionExpiryRepeatedFailures", "DecisionExpiryPollStalled"},
        )
        rendered = yaml.safe_dump(rules)
        self.assertIn("agenthub_decision_expiry_consecutive_failures", rendered)
        self.assertIn(
            "agenthub_decision_expiry_last_success_timestamp_seconds",
            rendered,
        )
        for forbidden in ("mission_id", "decision_id", "work_unit_id", "tenant_id"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
