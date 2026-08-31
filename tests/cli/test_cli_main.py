"""Unit tests for the developer CLI (North Star M0).

These cover pure logic: argument parsing, model-settings resolution,
exit-code mapping, contract shape, and workspace file listing. The
end-to-end mission loop is covered by tests/cli/test_cli_e2e.py.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from app.cli import runtime
from app.cli.main import build_parser, cli_main, _load_config
from app.cli.runtime import (
    EXIT_MISSION_CANCELLED,
    EXIT_MISSION_FAILED,
    EXIT_OK,
    EXIT_WAIT_TIMEOUT,
    build_contract,
    list_workspace_files,
    resolve_model_settings,
    status_to_exit_code,
)


class ParserTests(unittest.TestCase):
    def test_run_requires_objective(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["run"])

    def test_run_parses_flags(self) -> None:
        args = build_parser().parse_args(
            [
                "run",
                "fix the bug",
                "--model",
                "test-model",
                "--provider",
                "openai",
                "--max-total-tokens",
                "1000",
                "--mission-timeout",
                "30",
            ]
        )
        self.assertEqual(args.objective, "fix the bug")
        self.assertEqual(args.model, "test-model")
        self.assertEqual(args.provider, "openai")
        self.assertEqual(args.max_total_tokens, 1000)
        self.assertEqual(args.mission_timeout, 30.0)

    def test_exec_supports_json_flag(self) -> None:
        args = build_parser().parse_args(["exec", "do it", "--json"])
        self.assertTrue(args.json)
        args = build_parser().parse_args(["run", "do it"])
        self.assertFalse(hasattr(args, "json"))


class ModelSettingsTests(unittest.TestCase):
    def test_no_key_defaults_to_mock(self) -> None:
        settings = resolve_model_settings(
            provider=None, model=None, base_url=None, config={}
        )
        self.assertEqual(settings.provider, "mock")
        self.assertTrue(settings.is_mock)

    def test_explicit_provider_requires_key(self) -> None:
        with self.assertRaises(SystemExit):
            resolve_model_settings(
                provider="openai", model=None, base_url=None, config={}
            )

    def test_config_file_precedence(self) -> None:
        settings = resolve_model_settings(
            provider=None,
            model=None,
            base_url=None,
            config={"provider": "mock", "model": "my-mock"},
        )
        self.assertEqual(settings.model, "my-mock")

    def test_flags_override_config(self) -> None:
        settings = resolve_model_settings(
            provider="mock",
            model="flag-model",
            base_url=None,
            config={"model": "config-model"},
        )
        self.assertEqual(settings.model, "flag-model")


class ExitCodeTests(unittest.TestCase):
    def test_mapping(self) -> None:
        self.assertEqual(status_to_exit_code("SUCCEEDED", False), EXIT_OK)
        self.assertEqual(status_to_exit_code("FAILED", False), EXIT_MISSION_FAILED)
        self.assertEqual(
            status_to_exit_code("CANCELLED", False), EXIT_MISSION_CANCELLED
        )
        self.assertEqual(status_to_exit_code("RUNNING", True), EXIT_WAIT_TIMEOUT)
        self.assertEqual(status_to_exit_code("VERIFYING", False), EXIT_WAIT_TIMEOUT)


class ContractTests(unittest.TestCase):
    def test_contract_has_verifier_gate(self) -> None:
        contract = build_contract("contract-x", 60)
        self.assertEqual(contract["budgets"]["timeSeconds"], 60)
        criteria = contract["acceptanceCriteria"]
        self.assertEqual(len(criteria), 1)
        configuration = criteria[0]["configuration"]
        self.assertEqual(configuration["evaluator"], "artifact-set.v1")
        self.assertIn("desktop.task", configuration["workUnitKinds"])


class WorkspaceFileTests(unittest.TestCase):
    def test_excludes_agenthub_and_git(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("x=1", encoding="utf-8")
            (root / ".agenthub").mkdir()
            (root / ".agenthub" / "db.sqlite").write_text("", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("", encoding="utf-8")
            files = list_workspace_files(root)
        self.assertEqual(files, ["src/a.py"])


class ConfigLoadTests(unittest.TestCase):
    def test_missing_config_is_empty(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_load_config(Path(tmp)), {})


class ServerEnvTests(unittest.TestCase):
    def test_env_contains_sqlite_and_runner_gates(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            workspace = run_dir / "ws"
            model = runtime.CliModelSettings(
                provider="mock", model="mock-llm", api_key="mock", base_url=""
            )
            env = runtime.build_server_env(
                run_dir=run_dir,
                workspace_root=workspace,
                port=28123,
                model=model,
                max_total_tokens=12345,
                runner_timeout_seconds=99.0,
            )
        self.assertEqual(env["AGENTHUB_DB_BACKEND"], "sqlite")
        self.assertEqual(env["AGENTHUB_DESKTOP_LOCAL_RUNNER"], "1")
        self.assertEqual(env["AGENTHUB_DESKTOP_LOCAL_RUNNER_BASE_URL"], "http://127.0.0.1:28123")
        self.assertEqual(env["AGENTHUB_DESKTOP_LOCAL_RUNNER_VERIFY"], "1")
        self.assertEqual(env["AGENTHUB_DESKTOP_LOCAL_RUNNER_PROVIDER"], "mock")
        self.assertEqual(
            env["AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_TOTAL_TOKENS"], "12345"
        )
        # The API key must travel only via the environment.
        self.assertEqual(env["AGENTHUB_DESKTOP_MODEL_API_KEY"], "mock")


if __name__ == "__main__":
    unittest.main()
