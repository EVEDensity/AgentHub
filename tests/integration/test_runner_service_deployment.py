from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml


class RunnerServiceDeploymentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        document = yaml.safe_load(
            Path("deploy/docker-compose.platform.yml").read_text(encoding="utf-8")
        )
        if not isinstance(document, dict):
            raise TypeError("platform Compose document must be an object")
        cls.document: dict[str, Any] = document
        cls.service: dict[str, Any] = document["services"]["runner-service"]
        cls.secrets: dict[str, Any] = document["secrets"]

    def test_service_is_opt_in_and_uses_the_runner_image(self) -> None:
        self.assertEqual(self.service["profiles"], ["mission-runner"])
        self.assertEqual(self.service["build"]["context"], "..")
        self.assertEqual(
            self.service["build"]["dockerfile"],
            "services/python/runner_service/Dockerfile",
        )
        self.assertEqual(self.service["expose"], ["8097"])
        self.assertNotIn("ports", self.service)

    def test_service_uses_read_only_root_and_explicit_artifact_mount(self) -> None:
        self.assertTrue(self.service["read_only"])
        self.assertEqual(self.service["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", self.service["security_opt"])
        self.assertIn("/tmp", " ".join(self.service["tmpfs"]))
        artifact_mount = self.service["volumes"][0]
        self.assertEqual(artifact_mount["type"], "bind")
        self.assertIn("AGENTHUB_RUNNER_ARTIFACT_HOST_PATH", artifact_mount["source"])
        self.assertEqual(artifact_mount["target"], "/var/lib/agenthub/artifacts")
        self.assertFalse(artifact_mount["read_only"])
        self.assertFalse(artifact_mount["bind"]["create_host_path"])

    def test_external_endpoints_and_identity_are_explicit(self) -> None:
        environment = self.service["environment"]
        for name in (
            "RUNNER_ID",
            "WORKSPACE_ID",
            "ASSIGNED_AGENT_ID",
            "ASSIGNED_ADAPTER",
            "MISSION_CONTROL_URL",
            "MODEL_GATEWAY_URL",
            "MODEL",
            "MCP_ENDPOINT",
        ):
            self.assertIn(f"AGENTHUB_RUNNER_{name}", environment)
            self.assertEqual(
                environment[f"AGENTHUB_RUNNER_{name}"],
                "${AGENTHUB_RUNNER_" + name + ":-}",
            )
        self.assertNotIn("a2a.outbound", str(self.service))
        self.assertNotIn("depends_on", self.service)

    def test_credentials_are_file_backed_and_binding_manifest_is_read_only(self) -> None:
        environment = self.service["environment"]
        self.assertEqual(
            environment["AGENTHUB_RUNNER_MISSION_CONTROL_TOKEN_FILE"],
            "/run/secrets/runner-mission-control-token",
        )
        self.assertEqual(
            environment["AGENTHUB_RUNNER_MODEL_GATEWAY_TOKEN_FILE"],
            "/run/secrets/runner-model-gateway-token",
        )
        self.assertEqual(
            environment["AGENTHUB_RUNNER_MCP_TOKEN_FILE"],
            "/run/secrets/runner-mcp-token",
        )
        self.assertEqual(
            self.service["secrets"],
            [
                "runner-mission-control-token",
                "runner-model-gateway-token",
                "runner-mcp-token",
            ],
        )
        for secret_name in self.service["secrets"]:
            self.assertIn("AGENTHUB_RUNNER_", self.secrets[secret_name]["file"])
        manifest_mount = self.service["volumes"][1]
        self.assertEqual(manifest_mount["type"], "bind")
        self.assertIn(
            "AGENTHUB_RUNNER_MCP_BINDINGS_FILE",
            manifest_mount["source"],
        )
        self.assertEqual(manifest_mount["target"], "/run/configs/runner-mcp-bindings.json")
        self.assertTrue(manifest_mount["read_only"])
        self.assertFalse(manifest_mount["bind"]["create_host_path"])

    def test_readiness_and_shutdown_follow_runner_deadline(self) -> None:
        probe = " ".join(self.service["healthcheck"]["test"])
        self.assertIn("/readyz", probe)
        self.assertEqual(self.service["stop_grace_period"], "35s")
        self.assertEqual(
            self.service["environment"]["AGENTHUB_RUNNER_SHUTDOWN_TIMEOUT_SECONDS"],
            "30.0",
        )


if __name__ == "__main__":
    unittest.main()
