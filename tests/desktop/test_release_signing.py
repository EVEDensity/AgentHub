"""Structural tests for the Windows artifact signing path (I-5).

The release policy (desktop/release-policy.ps1) gates public tags on
secret presence; desktop/sign-windows-artifacts.ps1 is where the
certificate is actually applied, and package-windows.ps1 invokes it on
the updater-enabled (public) path. These tests pin that contract.
"""

from __future__ import annotations

import unittest
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[2] / "desktop"


class SigningScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = (DESKTOP_DIR / "sign-windows-artifacts.ps1").read_text(
            encoding="utf-8"
        )

    def test_consumes_ci_injected_certificate_secrets(self) -> None:
        self.assertIn("AGENTHUB_WINDOWS_SIGNING_CERT_BASE64", self.script)
        self.assertIn("AGENTHUB_WINDOWS_SIGNING_PASSWORD", self.script)

    def test_verifies_signature_after_signing(self) -> None:
        # Honest verification: the script must re-check the signature
        # and fail (not skip) when Set-AuthenticodeSignature did not
        # produce a Valid signature.
        self.assertIn("Get-AuthenticodeSignature", self.script)
        self.assertIn("-ne 'Valid'", self.script)

    def test_skips_cleanly_without_secrets(self) -> None:
        # Local developer builds have no certificate secrets and must
        # not fail.
        self.assertIn("skip (no certificate secrets)", self.script)

    def test_skips_already_signed_files(self) -> None:
        self.assertIn("already signed", self.script)

    def test_signs_distributable_extensions(self) -> None:
        self.assertIn("'.exe', '.msi', '.dll'", self.script)

    def test_uses_timestamp_server(self) -> None:
        self.assertIn("timestamp.digicert.com", self.script)


class PackageIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = (DESKTOP_DIR / "package-windows.ps1").read_text(
            encoding="utf-8"
        )

    def test_public_path_invokes_signing_script(self) -> None:
        self.assertIn("sign-windows-artifacts.ps1", self.script)
        # Wired to the updater-enabled (public tag) path only.
        self.assertIn("$env:AGENTHUB_UPDATE_ENABLED -eq '1'", self.script)

    def test_signing_runs_before_release_manifest_invocation(self) -> None:
        # The invocation is "& $releaseManifestScript ..."; signing must
        # appear before it in the script body.
        sign_index = self.script.index("sign-windows-artifacts.ps1")
        manifest_index = self.script.index("& $releaseManifestScript")
        self.assertLess(sign_index, manifest_index)


class WorkflowWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (
            Path(__file__).resolve().parents[2]
            / ".github"
            / "workflows"
            / "desktop-windows.yml"
        ).read_text(encoding="utf-8")

    def test_policy_step_passes_all_five_secrets(self) -> None:
        for secret in (
            "AGENTHUB_WINDOWS_SIGNING_CERT_BASE64",
            "AGENTHUB_WINDOWS_SIGNING_PASSWORD",
            "AGENTHUB_UPDATE_PRIVATE_KEY",
            "AGENTHUB_UPDATE_PUBLIC_KEY",
            "AGENTHUB_UPDATE_ENDPOINT",
        ):
            self.assertIn(secret, self.workflow)

    def test_policy_gates_public_tags_only(self) -> None:
        self.assertIn("release-policy.ps1 -PublicRelease", self.workflow)

    def test_stack_publish_attaches_manifest_and_verifies(self) -> None:
        self.assertIn("publish-stack", self.workflow)
        self.assertIn("stack-manifest.json", self.workflow)
        self.assertIn("asset verification failed", self.workflow)


class RunbookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runbook = (
            Path(__file__).resolve().parents[2]
            / "docs"
            / "operations"
            / "release-unblocking-runbook.md"
        ).read_text(encoding="utf-8")

    def test_documents_all_five_secrets(self) -> None:
        for secret in (
            "AGENTHUB_WINDOWS_SIGNING_CERT_BASE64",
            "AGENTHUB_WINDOWS_SIGNING_PASSWORD",
            "AGENTHUB_UPDATE_PRIVATE_KEY",
            "AGENTHUB_UPDATE_PUBLIC_KEY",
            "AGENTHUB_UPDATE_ENDPOINT",
        ):
            self.assertIn(secret, self.runbook)

    def test_documents_updater_key_generation(self) -> None:
        self.assertIn("tauri signer generate", self.runbook)

    def test_documents_tag_release_commands(self) -> None:
        self.assertIn("desktop-v0.3.0", self.runbook)
        self.assertIn("cli-v0.3.0", self.runbook)

    def test_self_signed_route_is_labeled_honestly(self) -> None:
        self.assertIn("不能消除 SmartScreen 警告", self.runbook)


if __name__ == "__main__":
    unittest.main()
