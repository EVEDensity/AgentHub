from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.runner_deployment_preflight import validate_runner_deployment


class RunnerDeploymentPreflightTests(unittest.TestCase):
    def _environment(self, root: Path) -> dict[str, str]:
        for name in ("mission-control.token", "model.token", "mcp.token"):
            (root / name).write_text(f"{name}-value\n", encoding="utf-8")
        manifest = root / "bindings.json"
        manifest.write_text(
            json.dumps(
                {
                    "schemaVersion": "agenthub.runner.mcp-bindings.v1",
                    "bindings": [],
                }
            ),
            encoding="utf-8",
        )
        artifacts = root / "artifacts"
        artifacts.mkdir()
        return {
            "AGENTHUB_RUNNER_RUNNER_ID": "runner-1",
            "AGENTHUB_RUNNER_WORKSPACE_ID": "workspace-1",
            "AGENTHUB_RUNNER_ASSIGNED_AGENT_ID": "reviewer",
            "AGENTHUB_RUNNER_ASSIGNED_ADAPTER": "local_codex",
            "AGENTHUB_RUNNER_MODEL": "production-model",
            "AGENTHUB_RUNNER_MISSION_CONTROL_URL": "https://control.example.test",
            "AGENTHUB_RUNNER_MODEL_GATEWAY_URL": "https://model.example.test/v1",
            "AGENTHUB_RUNNER_MCP_ENDPOINT": "https://mcp.example.test/mcp/rpc",
            "AGENTHUB_RUNNER_MISSION_CONTROL_TOKEN_FILE": str(
                root / "mission-control.token"
            ),
            "AGENTHUB_RUNNER_MODEL_GATEWAY_TOKEN_FILE": str(root / "model.token"),
            "AGENTHUB_RUNNER_MCP_TOKEN_FILE": str(root / "mcp.token"),
            "AGENTHUB_RUNNER_MCP_BINDINGS_FILE": str(manifest),
            "AGENTHUB_RUNNER_ARTIFACT_HOST_PATH": str(artifacts),
        }

    def test_offline_preflight_accepts_complete_safe_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures = validate_runner_deployment(
                self._environment(Path(directory)),
                check_network=False,
                connect_timeout_seconds=1,
            )
        self.assertEqual(failures, [])

    def test_preflight_reports_missing_or_unsafe_configuration_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = self._environment(Path(directory))
            environment["AGENTHUB_RUNNER_ASSIGNED_ADAPTER"] = "a2a.outbound"
            environment["AGENTHUB_RUNNER_MISSION_CONTROL_URL"] = ""
            environment["AGENTHUB_RUNNER_MCP_TOKEN_FILE"] = str(
                Path(directory) / "missing.token"
            )
            environment["AGENTHUB_RUNNER_ARTIFACT_HOST_PATH"] = str(
                Path(directory) / "missing-artifacts"
            )
            failures = validate_runner_deployment(
                environment,
                check_network=False,
                connect_timeout_seconds=1,
            )

        self.assertIn("AGENTHUB_RUNNER_ASSIGNED_ADAPTER cannot be a2a.outbound", failures)
        self.assertIn("AGENTHUB_RUNNER_MISSION_CONTROL_URL is required", failures)
        self.assertIn(
            "AGENTHUB_RUNNER_MCP_TOKEN_FILE is not a valid single-value secret file",
            failures,
        )
        self.assertIn(
            "AGENTHUB_RUNNER_ARTIFACT_HOST_PATH is not an existing directory",
            failures,
        )
        self.assertNotIn("missing.token", "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
