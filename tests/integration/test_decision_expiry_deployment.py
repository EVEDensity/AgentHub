from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml


class DecisionExpiryDeploymentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        compose_path = Path("deploy/docker-compose.platform.yml")
        document = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise TypeError("platform Compose document must be an object")
        cls.document: dict[str, Any] = document
        cls.services: dict[str, Any] = document["services"]
        cls.service: dict[str, Any] = cls.services["decision-expiry-service"]
        cls.secrets: dict[str, Any] = document["secrets"]

    def test_service_is_opt_in_and_uses_the_dedicated_image(self) -> None:
        self.assertEqual(self.service["profiles"], ["mission-supervision"])
        self.assertEqual(self.service["build"]["context"], "..")
        self.assertEqual(
            self.service["build"]["dockerfile"],
            "services/python/decision_expiry_service/Dockerfile",
        )

    def test_service_waits_for_postgres_health(self) -> None:
        postgres = self.services["postgres"]
        self.assertIn("pg_isready", " ".join(postgres["healthcheck"]["test"]))
        self.assertEqual(
            self.service["depends_on"]["postgres"]["condition"],
            "service_healthy",
        )

    def test_service_has_no_host_port_or_durable_local_state(self) -> None:
        self.assertNotIn("ports", self.service)
        self.assertEqual(self.service["expose"], ["8099"])
        self.assertTrue(self.service["read_only"])
        self.assertEqual(self.service["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", self.service["security_opt"])
        self.assertNotIn("volumes", self.service)
        tmpfs = " ".join(self.service["tmpfs"])
        self.assertIn("/tmp", tmpfs)
        self.assertIn("/srv/agenthub/data", tmpfs)

    def test_readiness_probe_and_shutdown_deadline_are_explicit(self) -> None:
        probe = " ".join(self.service["healthcheck"]["test"])
        self.assertIn("/readyz", probe)
        self.assertEqual(self.service["stop_grace_period"], "35s")
        self.assertEqual(
            self.service["environment"][
                "AGENTHUB_DECISION_EXPIRY_SHUTDOWN_TIMEOUT_SECONDS"
            ],
            "30.0",
        )

    def test_database_credential_is_mounted_without_plaintext_environment(self) -> None:
        environment = self.service["environment"]
        self.assertNotIn("DATABASE_URL", environment)
        self.assertEqual(
            environment["AGENTHUB_DECISION_EXPIRY_DATABASE_URL_FILE"],
            "/run/secrets/decision-expiry-database-url",
        )
        self.assertEqual(
            self.service["secrets"],
            ["decision-expiry-database-url"],
        )
        secret_file = self.secrets["decision-expiry-database-url"]["file"]
        self.assertIn("AGENTHUB_DECISION_EXPIRY_DATABASE_URL_FILE", secret_file)
        self.assertNotIn("postgresql://", secret_file)
        self.assertEqual(self.service["environment"]["AGENTHUB_ENV"], "development")

    def test_local_secret_directory_is_excluded_from_source_and_build(self) -> None:
        gitignore = Path(".gitignore").read_text(encoding="utf-8")
        dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
        self.assertIn("deploy/secrets/", gitignore.splitlines())
        self.assertIn("deploy/secrets", dockerignore.splitlines())


if __name__ == "__main__":
    unittest.main()
