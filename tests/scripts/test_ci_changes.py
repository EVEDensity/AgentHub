"""CI must select checks whenever their production inputs change."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from scripts.ci_changes import select_checks


class CiChangeSelectionTests(unittest.TestCase):
    def test_documentation_does_not_rebuild_unaffected_runtime_images(self) -> None:
        checks = select_checks(["docs/development/ci.md"])
        self.assertFalse(any(value for value in checks.values() if isinstance(value, bool)))
        self.assertEqual(checks["images"], {"include": []})

    def test_ci_changes_run_every_optional_check(self) -> None:
        for path in (".github/workflows/ci.yml", "scripts/ci_changes.py", ".dockerignore"):
            with self.subTest(path=path):
                checks = select_checks([path])
                self.assertTrue(all(value for value in checks.values() if isinstance(value, bool)))
                self.assertEqual(len(checks["images"]["include"]), 24)

    def test_contract_change_checks_every_consumer(self) -> None:
        checks = select_checks(["platform/contracts/v1/mission.json"])
        for name in ("go", "frontend", "rust", "windows_cli"):
            self.assertTrue(checks[name], name)

    def test_go_shared_change_rebuilds_every_go_image(self) -> None:
        checks = select_checks(["services/go/shared/events/events.go"])
        self.assertTrue(checks["go"])
        self.assertTrue(checks["smoke"])
        self.assertEqual(len(checks["images"]["include"]), 10)
        self.assertFalse(checks["rust"])

    def test_frontend_change_runs_types_browser_flow_and_image(self) -> None:
        checks = select_checks(["frontend/pages/index.tsx"])
        self.assertTrue(checks["frontend"])
        self.assertEqual([image["name"] for image in checks["images"]["include"]], ["frontend"])

    def test_python_service_change_checks_packaging_and_running_services(self) -> None:
        checks = select_checks(["services/python/model_adapter_service/main.py"])
        self.assertTrue(checks["smoke"])
        self.assertEqual([image["name"] for image in checks["images"]["include"]], ["model-adapter"])

    def test_deployment_changes_run_real_compose_health_checks(self) -> None:
        self.assertTrue(select_checks(["deploy/docker-compose.ci.yml"])["smoke"])

    def test_cli_dependencies_select_windows_flow(self) -> None:
        for path in ("requirements.txt", "app/services/runner/loops.py", "tests/cli/test_cli_e2e.py"):
            with self.subTest(path=path):
                self.assertTrue(select_checks([path])["windows_cli"])

    def test_manual_run_selects_all_checks(self) -> None:
        checks = select_checks([], all_checks=True)
        self.assertTrue(checks["go"])
        self.assertEqual(len(checks["images"]["include"]), 24)

    def test_each_supported_service_dockerfile_and_source_select_its_actual_image(self) -> None:
        root = Path(__file__).resolve().parents[2]
        all_images = select_checks([], all_checks=True)["images"]["include"]
        for image in all_images:
            for path in (image["file"], str(Path(image["file"]).parent / "main.py")):
                with self.subTest(path=path):
                    selected = select_checks([path])["images"]["include"]
                    self.assertIn(image, selected)
                    self.assertTrue((root / image["file"]).is_file())
                    self.assertEqual(image["context"], ".")

    def test_shared_app_changes_select_every_image_that_copies_app(self) -> None:
        root = Path(__file__).resolve().parents[2]
        expected = {
            path.relative_to(root).as_posix()
            for path in (root / "services/python").glob("*/Dockerfile")
            if "COPY app " in path.read_text(encoding="utf-8")
        }
        images = select_checks(["app/services/runner/recovery.py"])["images"]["include"]
        self.assertEqual({image["file"] for image in images}, expected)
        self.assertEqual({image["name"] for image in images}, {"runner", "verifier", "decision-expiry"})

    def test_python_shared_and_project_metadata_select_actual_copy_consumers(self) -> None:
        root = Path(__file__).resolve().parents[2]
        expected = {
            path.relative_to(root).as_posix()
            for path in (root / "services/python").glob("*/Dockerfile")
            if "COPY services/python/shared " in path.read_text(encoding="utf-8")
        }
        for path in ("services/python/shared/base.py", "services/python/pyproject.toml"):
            with self.subTest(path=path):
                images = select_checks([path])["images"]["include"]
                self.assertEqual({image["file"] for image in images}, expected)

    def test_rust_workspace_inputs_build_deployed_images_with_root_context(self) -> None:
        checks = select_checks(["services/rust/Cargo.lock"])
        self.assertTrue(checks["rust"])
        self.assertTrue(checks["docker"])
        self.assertEqual({image["name"] for image in checks["images"]["include"]}, {
            "stream-core", "retrieval-core", "fanout-core", "patch-merge-core", "memory-segment-core",
        })
        self.assertTrue(all(image["context"] == "." for image in checks["images"]["include"]))

    def test_supported_inventory_covers_current_service_recipes_without_silent_omissions(self) -> None:
        root = Path(__file__).resolve().parents[2]
        all_images = select_checks([], all_checks=True)["images"]["include"]
        self.assertEqual(len({image["name"] for image in all_images}), len(all_images))
        recipes = {path.relative_to(root).as_posix() for path in (root / "services").rglob("Dockerfile")}
        selected = {image["file"] for image in all_images}
        self.assertEqual(recipes - selected, {"services/rust/crates/agentnet-core/Dockerfile"})

    def test_api_schema_changes_run_frontend_consumer_checks(self) -> None:
        checks = select_checks(["app/schemas/mission.py"])
        self.assertTrue(checks["frontend"])
        self.assertTrue(checks["windows_cli"])
        self.assertIn("frontend", {image["name"] for image in checks["images"]["include"]})

    def test_invalid_base_is_an_error_instead_of_omitting_checks(self) -> None:
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [sys.executable, "scripts/ci_changes.py", "--base-ref", "missing-ci-base-ref"],
            cwd=root, capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
