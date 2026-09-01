"""Structural tests for the @agenthub/cli npm distribution (I-2).

These pin the packaging contract so `npm i -g @agenthub/cli` keeps
working: zero runtime dependencies, a bin launcher that resolves the
platform binary package, and platform packages gated by os/cpu with
the frozen exe whitelisted. The binary itself is built and exercised
end to end by the npm-cli release workflow on cli-v* tags.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

NPM_ROOT = Path(__file__).resolve().parents[2] / "distributions" / "npm"
MAIN_DIR = NPM_ROOT / "cli"
PLATFORM_DIR = NPM_ROOT / "cli-win32-x64"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class MainPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = _read_json(MAIN_DIR / "package.json")

    def test_package_identity(self) -> None:
        self.assertEqual(self.manifest["name"], "@agenthub/cli")
        self.assertEqual(self.manifest["license"], "Apache-2.0")

    def test_bin_exposes_agenthub_command(self) -> None:
        self.assertEqual(self.manifest["bin"], {"agenthub": "bin/agenthub.js"})

    def test_zero_runtime_dependencies(self) -> None:
        # The one-line install promise: nothing but the binary.
        self.assertNotIn("dependencies", self.manifest)
        self.assertEqual(self.manifest.get("dependencies", {}), {})

    def test_platform_binary_is_optional_dependency(self) -> None:
        optional = self.manifest.get("optionalDependencies", {})
        self.assertIn("@agenthub/cli-win32-x64", optional)

    def test_files_whitelist_keeps_the_package_minimal(self) -> None:
        files = self.manifest["files"]
        self.assertIn("bin", files)
        self.assertNotIn("node_modules", files)


class LauncherScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = (MAIN_DIR / "bin" / "agenthub.js").read_text(encoding="utf-8")

    def test_maps_win32_x64_to_platform_package(self) -> None:
        self.assertIn("'win32-x64': '@agenthub/cli-win32-x64'", self.script)

    def test_resolves_binary_from_platform_package(self) -> None:
        self.assertIn("require.resolve", self.script)
        self.assertIn("agenthub.exe", self.script)

    def test_forwards_argv_and_stdio(self) -> None:
        self.assertIn("process.argv.slice(2)", self.script)
        self.assertIn("stdio: 'inherit'", self.script)

    def test_propagates_exit_code(self) -> None:
        self.assertIn("process.exit(", self.script)

    def test_unsupported_platform_fails_with_clear_error(self) -> None:
        self.assertIn("127", self.script)
        self.assertIn("no prebuilt binary", self.script)


class PlatformPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = _read_json(PLATFORM_DIR / "package.json")

    def test_package_identity_and_gates(self) -> None:
        self.assertEqual(self.manifest["name"], "@agenthub/cli-win32-x64")
        self.assertEqual(self.manifest["os"], ["win32"])
        self.assertEqual(self.manifest["cpu"], ["x64"])

    def test_files_whitelist_contains_only_the_binary(self) -> None:
        files = self.manifest["files"]
        self.assertIn("agenthub.exe", files)
        self.assertNotIn("dependencies", self.manifest)

    def test_binary_is_never_committed(self) -> None:
        # Build artifacts are injected at publish time by CI.
        self.assertTrue((PLATFORM_DIR / ".gitignore").is_file())
        gitignore = (PLATFORM_DIR / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("agenthub.exe", gitignore)
        self.assertFalse((PLATFORM_DIR / "agenthub.exe").exists())


class FreezeScriptTests(unittest.TestCase):
    def test_freeze_script_exists_and_targets_cli_entrypoint(self) -> None:
        script = (
            Path(__file__).resolve().parents[2] / "scripts" / "build-cli-windows.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("cli_entrypoint.py", script)
        # mission-control (main) must be collected for the _serve path.
        self.assertIn("--hidden-import main", script)
        self.assertIn("--onefile", script)

    def test_workflow_publishes_on_cli_tags(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[2]
            / ".github"
            / "workflows"
            / "npm-cli.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("cli-v*", workflow)
        self.assertIn("npm publish --access public", workflow)
        # The frozen binary must pass a closed-loop smoke before publish.
        self.assertIn("--provider mock", workflow)


if __name__ == "__main__":
    unittest.main()
