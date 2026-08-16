from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import yaml

from scripts.decision_expiry_smoke import (
    DECISION_ID,
    _assert_sanitized_metrics,
    _assert_sanitized_readiness,
    _published_port,
    _run,
    _seed_expired_decision,
)


class _FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def execute(self, sql: str, *args: object) -> None:
        self.executed.append((sql, args))

    async def close(self) -> None:
        self.closed = True


class DecisionExpirySmokeAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        compose_path = Path("deploy/docker-compose.decision-expiry-smoke.yml")
        document = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise TypeError("smoke Compose document must be an object")
        cls.document: dict[str, Any] = document
        cls.services: dict[str, Any] = document["services"]

    def test_topology_contains_only_ephemeral_postgres_and_supervisor(self) -> None:
        self.assertEqual(
            set(self.services),
            {"postgres", "decision-expiry-service"},
        )
        self.assertNotIn("volumes", self.document)
        self.assertNotIn("volumes", self.services["postgres"])
        self.assertNotIn("volumes", self.services["decision-expiry-service"])

    def test_ports_are_random_and_loopback_only(self) -> None:
        self.assertEqual(self.services["postgres"]["ports"], ["127.0.0.1::5432"])
        self.assertEqual(
            self.services["decision-expiry-service"]["ports"],
            ["127.0.0.1::8099"],
        )

    def test_supervisor_uses_secret_and_real_readiness(self) -> None:
        service = self.services["decision-expiry-service"]
        environment = service["environment"]
        self.assertNotIn("DATABASE_URL", environment)
        self.assertEqual(
            environment["AGENTHUB_DECISION_EXPIRY_DATABASE_URL_FILE"],
            "/run/secrets/decision-expiry-database-url",
        )
        self.assertEqual(service["secrets"], ["decision-expiry-database-url"])
        self.assertIn("/readyz", " ".join(service["healthcheck"]["test"]))
        self.assertEqual(
            service["depends_on"]["postgres"]["condition"],
            "service_healthy",
        )

    def test_published_port_parser_rejects_non_loopback_or_invalid_values(self) -> None:
        self.assertEqual(_published_port("127.0.0.1:49152\n"), 49152)
        for output in ("", "example.test:1234", "127.0.0.1:0", "invalid"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                _published_port(output)

    def test_command_output_is_decoded_independently_of_windows_locale(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["docker"],
            returncode=0,
            stdout=b"built \x81 image\n",
            stderr=b"",
        )
        with patch("scripts.decision_expiry_smoke.subprocess.run", return_value=completed):
            output = _run(["docker", "compose"], environment={}, timeout=30)

        self.assertEqual(output, "built \ufffd image")

    def test_readiness_assertion_accepts_only_one_sanitized_expiry(self) -> None:
        valid = {
            "status": "ready",
            "worker": {"expired": 1, "failedPolls": 0},
        }
        _assert_sanitized_readiness(valid)

        invalid_payloads = (
            {"status": "not-ready", "worker": {}},
            {"status": "ready", "worker": {"expired": 2, "failedPolls": 0}},
            {
                "status": "ready",
                "worker": {
                    "expired": 1,
                    "failedPolls": 0,
                    "decision": "dec-decision-expiry-smoke",
                },
            },
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(AssertionError):
                _assert_sanitized_readiness(payload)

    def test_metrics_assertion_accepts_only_sanitized_operational_state(self) -> None:
        valid = (
            "agenthub_decision_expiry_process_healthy 1\n"
            "agenthub_decision_expiry_ready 1\n"
            "agenthub_decision_expiry_decisions_expired_total 1\n"
            "agenthub_decision_expiry_failed_polls_total 0\n"
            "agenthub_decision_expiry_last_success_timestamp_seconds 1.0"
        )
        _assert_sanitized_metrics(valid)

        invalid_payloads = (
            valid.replace("failed_polls_total 0", "failed_polls_total 1"),
            valid.replace("timestamp_seconds 1.0", "timestamp_seconds 0.0"),
            valid + "\n" + DECISION_ID,
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(AssertionError):
                _assert_sanitized_metrics(payload)


class DecisionExpirySmokeSeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_seed_binds_mission_to_exact_contract_revision(self) -> None:
        connection = _FakeConnection()

        with patch(
            "scripts.decision_expiry_smoke.asyncpg.connect",
            new=AsyncMock(return_value=connection),
        ):
            await _seed_expired_decision("postgresql://smoke.invalid/database")

        mission_sql, _args = next(
            statement
            for statement in connection.executed
            if "INSERT INTO missions" in statement[0]
        )
        self.assertIn("contract_id", mission_sql)
        self.assertIn("contract_version", mission_sql)
        self.assertIn("$6, 1", mission_sql)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
