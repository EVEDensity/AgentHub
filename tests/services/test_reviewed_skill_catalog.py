"""Reviewed content is bounded, provenance-pinned and frozen before exposure."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from app.services.reviewed_skills import ReviewedSkillError, load_catalog
from app.services.reviewed_skills.catalog import package_digest, sha256_digest
from app.services.reviewed_skills.frontmatter import (
    MAX_TEXT_BYTES,
    canonical_text_bytes,
    parse_skill,
)

HEADER = """name: fixture-skill
description: Reviewed fixture metadata only
license: Apache-2.0
compatibility: Requires an explicitly supplied repository reader
allowed-tools: Read Bash
metadata:
  version: "1.0.0"
  author: AgentHub
"""
BODY = "BODY-ONLY-MARKER: Read the selected file."
REFERENCE = "REFERENCE-ONLY-MARKER: Summarize the observed evidence.\n"


def skill_text(header=HEADER, body=BODY):
    return f"---\n{header}---\n\n{body}\n".encode()


def write_fixture(root, *, body=BODY, reference=REFERENCE, capabilities=None):
    """Create original operator-selected test data; never discover user files."""
    package = root / "packages" / "fixture-skill"
    (package / "references").mkdir(parents=True, exist_ok=True)
    content = {
        "SKILL.md": skill_text(body=body),
        "LICENSE": b"Apache License 2.0 fixture\n",
        "references/output.md": reference.encode(),
    }
    entries = []
    for path, value in sorted(content.items()):
        (package / path).write_bytes(value)
        entries.append(
            {"path": path, "sizeBytes": len(value), "sha256": sha256_digest(value)}
        )
    revision = "a" * 40
    specification = {
        "name": "fixture-skill",
        "version": "1.0.0",
        "license": "Apache-2.0",
        "sourceUrl": f"https://github.com/EVEDensity/AgentHub/blob/{revision}/app/services/reviewed_skills/packages/fixture-skill/SKILL.md",
        "sourceRevision": revision,
        "requiredCapabilities": ["repository.read"]
        if capabilities is None
        else capabilities,
        "files": entries,
        "packageDigest": package_digest(entries),
    }
    index = {"schemaVersion": 1, "skills": [specification]}
    save_index(root, index)
    return index


def save_index(root, index):
    (root / "catalog.json").write_text(json.dumps(index), encoding="utf-8")


class ReviewedCatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.index = write_fixture(self.root)
        self.package = self.root / "packages" / "fixture-skill"

    def test_freezes_verified_content_and_complete_provenance(self):
        catalog = load_catalog(self.root)
        metadata = catalog.list_metadata()[0]
        document = catalog.load("fixture-skill")
        self.assertEqual(document.body, BODY)
        self.assertEqual(metadata.version, "1.0.0")
        self.assertEqual(metadata.license, "Apache-2.0")
        self.assertEqual(metadata.source_revision, "a" * 40)
        self.assertEqual(
            metadata.package_digest, self.index["skills"][0]["packageDigest"]
        )
        self.assertEqual(metadata.required_capabilities, ("repository.read",))
        self.assertEqual(document.resource_paths, ("references/output.md",))
        self.assertEqual(
            catalog.read_resource("fixture-skill", "references/output.md").content,
            REFERENCE,
        )
        with self.assertRaises(FrozenInstanceError):
            metadata.version = "evil"
        public = metadata.to_public_dict()
        public["metadata"]["version"] = "evil"
        public["requiredCapabilities"].clear()
        self.assertEqual(metadata.version, "1.0.0")
        self.assertEqual(metadata.required_capabilities, ("repository.read",))

    def test_later_filesystem_mutations_cannot_replace_frozen_body_or_resource(self):
        catalog = load_catalog(self.root)
        (self.package / "SKILL.md").write_bytes(b"replacement")
        (self.package / "references/output.md").unlink()
        self.assertEqual(catalog.load("fixture-skill").body, BODY)
        self.assertEqual(
            catalog.read_resource("fixture-skill", "references/output.md").content,
            REFERENCE,
        )
        with self.assertRaises(ReviewedSkillError):
            load_catalog(self.root)

    def test_crlf_and_bare_cr_have_the_same_explicit_canonical_digest(self):
        expected = load_catalog(self.root).digest
        for ending in (b"\r\n", b"\r"):
            with self.subTest(ending=ending):
                write_fixture(self.root)
                for path in self.package.rglob("*"):
                    if path.is_file():
                        path.write_bytes(path.read_bytes().replace(b"\n", ending))
                catalog = load_catalog(self.root)
                self.assertEqual(catalog.digest, expected)
                self.assertEqual(catalog.load("fixture-skill").body, BODY)

    def test_each_file_is_required_exactly_and_digest_verified(self):
        for path in ("SKILL.md", "LICENSE", "references/output.md"):
            with self.subTest(path=path):
                write_fixture(self.root)
                (self.package / path).write_bytes(b"tampered\n")
                with self.assertRaises(ReviewedSkillError):
                    load_catalog(self.root)
        write_fixture(self.root)
        (self.package / "references/output.md").unlink()
        with self.assertRaises(ReviewedSkillError):
            load_catalog(self.root)
        write_fixture(self.root)
        (self.package / "unlisted.txt").write_text("unreviewed")
        with self.assertRaises(ReviewedSkillError):
            load_catalog(self.root)

    def test_index_rejects_wrong_hash_size_package_digest_and_metadata(self):
        changes = (
            ("version", "2.0.0"),
            ("license", "MIT"),
            ("packageDigest", "sha256:" + "0" * 64),
            ("sourceRevision", "b" * 40),
            ("requiredCapabilities", ["x", "x"]),
            ("name", "../escape"),
            ("sourceUrl", "https://example.com/skill"),
        )
        for key, value in changes:
            with self.subTest(key=key):
                index = copy.deepcopy(self.index)
                index["skills"][0][key] = value
                save_index(self.root, index)
                with self.assertRaises(ReviewedSkillError):
                    load_catalog(self.root)
        for key, value in (("sizeBytes", 2), ("sha256", "sha256:" + "0" * 64)):
            with self.subTest(key=key):
                index = copy.deepcopy(self.index)
                specification = index["skills"][0]
                specification["files"][0][key] = value
                specification["packageDigest"] = package_digest(specification["files"])
                save_index(self.root, index)
                with self.assertRaises(ReviewedSkillError):
                    load_catalog(self.root)

    def test_unlisted_packages_nested_directories_and_duplicate_names_are_rejected(
        self,
    ):
        index = copy.deepcopy(self.index)
        index["skills"].append(copy.deepcopy(index["skills"][0]))
        save_index(self.root, index)
        with self.assertRaises(ReviewedSkillError):
            load_catalog(self.root)
        save_index(self.root, self.index)
        (self.package / "references" / "nested").mkdir()
        with self.assertRaises(ReviewedSkillError):
            load_catalog(self.root)
        (self.package / "references" / "nested").rmdir()
        (self.root / "packages" / "unlisted").mkdir()
        with self.assertRaises(ReviewedSkillError):
            load_catalog(self.root)

    def test_catalog_rejects_invalid_json_and_implicit_schema_version(self):
        for text in (
            '{"schemaVersion":1,"schemaVersion":1}',
            '{"schemaVersion":NaN}',
            "{}",
            "[]",
        ):
            with self.subTest(text=text):
                (self.root / "catalog.json").write_text(text)
                with self.assertRaises(ReviewedSkillError):
                    load_catalog(self.root)
        for version in (True, "1", 2):
            with self.subTest(version=version):
                index = copy.deepcopy(self.index)
                index["schemaVersion"] = version
                save_index(self.root, index)
                with self.assertRaises(ReviewedSkillError):
                    load_catalog(self.root)

    def test_hardlinked_files_are_rejected_before_reading(self):
        original = self.package / "LICENSE"
        alias = self.root / "license-alias"
        try:
            os.link(original, alias)
        except OSError:
            self.skipTest("Hard links are unavailable on this filesystem")
        with self.assertRaises(ReviewedSkillError):
            load_catalog(self.root)

    def test_unknown_name_and_resource_traversal_are_rejected(self):
        catalog = load_catalog(self.root)
        with self.assertRaises(ReviewedSkillError):
            catalog.load("missing")
        for path in (
            "../SKILL.md",
            "/etc/passwd",
            "references/../LICENSE",
            "references/x/y.md",
            "references/missing.md",
        ):
            with self.subTest(path=path), self.assertRaises(ReviewedSkillError):
                catalog.read_resource("fixture-skill", path)


class ReviewedFrontmatterTests(unittest.TestCase):
    def test_rejects_duplicate_keys_anchors_tags_and_non_string_metadata(self):
        headers = (
            HEADER + "description: duplicate\n",
            HEADER.replace('version: "1.0.0"', 'version: "1.0.0"\n  version: "1.0.0"'),
            HEADER.replace(
                "description: Reviewed fixture metadata only",
                "description: &value anchor",
            ),
            HEADER + "compatibility: *missing\n",
            HEADER.replace(
                "description: Reviewed fixture metadata only",
                "description: !!python/object:dict {}",
            ),
            HEADER.replace('version: "1.0.0"', "version: 1"),
            HEADER.replace('version: "1.0.0"', "version: [nested]"),
            HEADER + "scripts: run.sh\n",
            HEADER.replace("name: fixture-skill", "name: Other-Skill"),
            HEADER.replace("license: Apache-2.0\n", ""),
            HEADER.replace("metadata:\n", "metadata: {}\nignored:\n"),
        )
        for header in headers:
            with self.subTest(header=header), self.assertRaises(ReviewedSkillError):
                parse_skill(skill_text(header), expected_name="fixture-skill")

    def test_requires_matching_name_complete_boundaries_and_nonempty_body(self):
        samples = (
            b"no frontmatter",
            b"---\nname: fixture-skill\n",
            skill_text(body=""),
            skill_text(HEADER.replace("fixture-skill", "different")),
            skill_text(HEADER + "#" * 8193),
        )
        for content in samples:
            with (
                self.subTest(content=content[:30]),
                self.assertRaises(ReviewedSkillError),
            ):
                parse_skill(content, expected_name="fixture-skill")

    def test_rejects_invalid_utf8_bom_nul_and_over_byte_bound(self):
        for content in (
            b"\xff",
            b"\xef\xbb\xbftext",
            b"x\x00x",
            b"x" * (MAX_TEXT_BYTES + 1),
        ):
            with self.subTest(size=len(content)), self.assertRaises(ReviewedSkillError):
                canonical_text_bytes(content)

    def test_allowed_tools_remain_information_without_a_permission_side_effect(self):
        parsed = parse_skill(skill_text(), expected_name="fixture-skill")
        self.assertEqual(parsed.allowed_tools, "Read Bash")
        self.assertEqual(parsed.version, "1.0.0")
        self.assertEqual(
            parsed.metadata, (("author", "AgentHub"), ("version", "1.0.0"))
        )
