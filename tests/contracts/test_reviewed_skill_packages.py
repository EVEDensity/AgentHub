"""Frozen original skill resources must match their manifest and real source commit."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

from app.services.reviewed_skills import load_bundled_catalog
from app.services.reviewed_skills.catalog import canonical_text_bytes, package_digest

ROOT = Path(__file__).resolve().parents[2]
RESOURCES = ROOT / "app/services/reviewed_skills"
CAPABILITIES = {
    "repository-fix": ["repository.read", "repository.write"],
    "code-review": ["repository.read"],
    "source-traceable-research": ["web.read"],
}
FILES = {"LICENSE", "SKILL.md", "references/output.md"}


def normalized(content: bytes) -> bytes:
    text = content.decode("utf-8")
    if text.startswith("\ufeff"):
        raise ValueError("reviewed resources must not have a UTF-8 BOM")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


class ReviewedSkillPackageContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((RESOURCES / "catalog.json").read_text(encoding="utf-8"))

    def test_catalog_declares_only_original_packages_and_requirements_never_grants(self):
        self.assertEqual(set(self.catalog), {"schemaVersion", "skills"})
        self.assertEqual(self.catalog["schemaVersion"], 1)
        entries = self.catalog["skills"]
        self.assertEqual([item["name"] for item in entries], sorted(CAPABILITIES))
        for entry in entries:
            with self.subTest(skill=entry["name"]):
                self.assertEqual(set(entry), {"name", "version", "license", "sourceUrl", "sourceRevision",
                                              "requiredCapabilities", "files", "packageDigest"})
                self.assertEqual(entry["requiredCapabilities"], CAPABILITIES[entry["name"]])
                self.assertEqual((entry["version"], entry["license"]), ("1.0.0", "Apache-2.0"))
                self.assertRegex(entry["sourceRevision"], r"\A[0-9a-f]{40}\Z")
                self.assertEqual(entry["sourceUrl"],
                    f"https://github.com/EVEDensity/AgentHub/blob/{entry['sourceRevision']}/"
                    f"app/services/reviewed_skills/packages/{entry['name']}/SKILL.md")

    def test_file_manifest_is_complete_and_package_digest_uses_canonical_sorted_manifest(self):
        for entry in self.catalog["skills"]:
            package = RESOURCES / "packages" / entry["name"]
            actual = {path.relative_to(package).as_posix() for path in package.rglob("*") if path.is_file()}
            self.assertEqual(actual, FILES)
            self.assertEqual([item["path"] for item in entry["files"]], sorted(FILES))
            expected = []
            for item in entry["files"]:
                path = package / item["path"]
                self.assertFalse(path.is_symlink())
                content = normalized(path.read_bytes())
                expected.append({"path": item["path"], "sizeBytes": len(content), "sha256": digest(content)})
            self.assertEqual(entry["files"], expected)
            manifest = json.dumps(expected, sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.assertEqual(entry["packageDigest"], digest(manifest))
            self.assertEqual(package_digest(entry["files"]), digest(manifest))

    def test_bundled_loader_exposes_the_same_frozen_revision_and_reference_bytes(self):
        catalog = load_bundled_catalog()
        self.assertEqual([item.name for item in catalog.list_metadata()], sorted(CAPABILITIES))
        for entry in self.catalog["skills"]:
            document = catalog.load(entry["name"])
            self.assertEqual(document.metadata.source_revision, entry["sourceRevision"])
            self.assertEqual(document.metadata.package_digest, entry["packageDigest"])
            self.assertEqual(list(document.metadata.required_capabilities), entry["requiredCapabilities"])
            self.assertEqual(document.resource_paths, ("references/output.md",))
            expected = (RESOURCES / "packages" / entry["name"] / "references/output.md").read_bytes()
            resource = catalog.read_resource(entry["name"], "references/output.md")
            self.assertEqual(resource.content.encode(), normalized(expected))
            self.assertEqual(resource.content.encode(), canonical_text_bytes(expected))

    def test_agent_skills_metadata_license_and_reference_are_portable(self):
        for entry in self.catalog["skills"]:
            package = RESOURCES / "packages" / entry["name"]
            skill = normalized((package / "SKILL.md").read_bytes()).decode()
            self.assertTrue(skill.startswith("---\n"))
            metadata, body = skill[4:].split("\n---\n", 1)
            frontmatter = yaml.safe_load(metadata)
            self.assertEqual(frontmatter["name"], package.name)
            self.assertEqual(frontmatter["license"], entry["license"])
            self.assertEqual(frontmatter["metadata"], {"version": entry["version"], "author": "AgentHub"})
            self.assertTrue(1 <= len(frontmatter["description"]) <= 1024)
            self.assertTrue(1 <= len(frontmatter["compatibility"]) <= 500)
            self.assertNotIn("disable-model-invocation", frontmatter)
            self.assertIn("(references/output.md)", body)
            self.assertIn("(LICENSE)", body)
            self.assertLess(len(skill.splitlines()), 500)
            license_text = normalized((package / "LICENSE").read_bytes()).decode()
            self.assertIn("Copyright 2026 AgentHub contributors", license_text)
            self.assertIn("Version 2.0, January 2004", license_text)
            self.assertIn("END OF TERMS AND CONDITIONS", license_text)

    def test_source_revision_contains_the_exact_frozen_package_bytes(self):
        git = shutil.which("git")
        if git is None:
            self.skipTest("Git is unavailable; source commit bytes cannot be read")
        if self._git(git, "rev-parse", "--is-inside-work-tree").returncode:
            self.skipTest("Source checkout has no Git repository")
        shallow = self._git(git, "rev-parse", "--is-shallow-repository").stdout.strip() == b"true"
        for entry in self.catalog["skills"]:
            revision = entry["sourceRevision"]
            self.assertIsNotNone(re.fullmatch(r"[0-9a-f]{40}", revision))
            available = self._git(git, "cat-file", "-e", revision + "^{commit}")
            if available.returncode and shallow:
                self.skipTest("Pinned source commit is unavailable in this shallow checkout; no network fetch")
            self.assertEqual(available.returncode, 0, "Pinned source commit must exist in the source repository")
            for item in entry["files"]:
                relative = f"app/services/reviewed_skills/packages/{entry['name']}/{item['path']}"
                blob = self._git(git, "show", f"{revision}:{relative}")
                self.assertEqual(blob.returncode, 0, f"Pinned commit lacks {relative}")
                self.assertNotIn(b"\r", blob.stdout, "Committed original resources must use LF")
                self.assertEqual(blob.stdout, normalized((ROOT / relative).read_bytes()))
                self.assertEqual(digest(blob.stdout), item["sha256"])
                self.assertEqual(len(blob.stdout), item["sizeBytes"])

    @staticmethod
    def _git(executable, *arguments):
        return subprocess.run([executable, *arguments], cwd=ROOT, capture_output=True, check=False, timeout=10)
