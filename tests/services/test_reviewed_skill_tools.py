"""Read tools disclose only requested reviewed content and never grant authority."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.services.reviewed_skills import load_catalog
from app.services.reviewed_skills.tools import build_reviewed_skill_tools
from tests.services.test_reviewed_skill_catalog import BODY, REFERENCE, write_fixture


def invoke(tool, arguments):
    return json.loads(asyncio.run(tool.handler(arguments)))


class ReviewedSkillToolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        write_fixture(self.root)
        self.catalog = load_catalog(self.root)

    def tools(self, **kwargs):
        return build_reviewed_skill_tools(self.catalog, **kwargs)

    def available_tools(self, **kwargs):
        return self.tools(
            available_capabilities=frozenset({"repository.read"}), **kwargs
        )

    def test_discovery_exposes_provenance_and_missing_requirements_without_content(
        self,
    ):
        listing, _loading = self.tools()
        response = invoke(listing, {})
        result = response["result"]
        self.assertTrue(response["success"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["catalogDigest"], self.catalog.digest)
        self.assertEqual(result["permissionEffect"], "none")
        metadata = result["skills"][0]
        self.assertFalse(metadata["available"])
        self.assertEqual(metadata["missingCapabilities"], ["repository.read"])
        self.assertEqual(metadata["sourceRevision"], "a" * 40)
        self.assertEqual(metadata["version"], "1.0.0")
        self.assertEqual(metadata["license"], "Apache-2.0")
        self.assertNotIn("body", metadata)
        self.assertNotIn("resourcePaths", metadata)
        self.assertNotIn(BODY, json.dumps(response))
        self.assertNotIn(REFERENCE.strip(), json.dumps(response))

    def test_default_capabilities_refuse_both_body_and_explicit_resource(self):
        _listing, loading = self.tools()
        for arguments in (
            {"name": "fixture-skill"},
            {"name": "fixture-skill", "resource": "references/output.md"},
        ):
            with self.subTest(arguments=arguments):
                response = invoke(loading, arguments)
                self.assertFalse(response["success"])
                self.assertEqual(
                    response["error"], "reviewed_skill_missing_capabilities"
                )
                self.assertEqual(response["missingCapabilities"], ["repository.read"])
                self.assertNotIn(BODY, json.dumps(response))

    def test_explicit_body_has_all_provenance_and_never_adds_a_tool(self):
        tools = self.available_tools()
        self.assertEqual([tool.name for tool in tools], ["skill_list", "skill_load"])
        response = invoke(tools[1], {"name": "fixture-skill"})
        self.assertTrue(response["success"])
        result = response["result"]
        self.assertEqual(result["body"], BODY)
        self.assertEqual(result["resourcePaths"], ["references/output.md"])
        self.assertEqual(result["allowedTools"], "Read Bash")
        self.assertEqual(result["permissionEffect"], "none")
        self.assertEqual(
            result["packageDigest"],
            self.catalog.load("fixture-skill").metadata.package_digest,
        )
        self.assertEqual(
            result["skillMdDigest"], self.catalog.load("fixture-skill").skill_md_digest
        )
        self.assertEqual([tool.name for tool in tools], ["skill_list", "skill_load"])

    def test_reference_is_an_explicit_stateless_selection_without_prior_body_load(self):
        _listing, loading = self.available_tools()
        arguments = {"name": "fixture-skill", "resource": "references/output.md"}
        first = invoke(loading, arguments)
        self.assertTrue(first["success"])
        self.assertEqual(first["result"]["content"], REFERENCE)
        self.assertEqual(first["result"]["path"], "references/output.md")
        self.assertEqual(first["result"]["sizeBytes"], len(REFERENCE.encode()))
        _second_list, second_loading = self.available_tools()
        self.assertEqual(invoke(second_loading, arguments), first)
        invoke(loading, {"name": "fixture-skill"})
        self.assertEqual(invoke(loading, arguments), first)

    def test_unknown_names_and_unlisted_references_fail_with_static_safe_json(self):
        _listing, loading = self.available_tools()
        for arguments in (
            {"name": "missing"},
            {"name": "fixture-skill", "resource": "references/missing.md"},
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(
                    invoke(loading, arguments),
                    {"success": False, "error": "reviewed_skill_not_found"},
                )

    def test_output_budget_never_truncates_json_or_a_successful_content_result(self):
        listing, loading = self.available_tools(max_result_chars=64)
        for tool, arguments in (
            (listing, {}),
            (loading, {"name": "fixture-skill"}),
            (loading, {"name": "fixture-skill", "resource": "references/output.md"}),
        ):
            with self.subTest(arguments=arguments):
                raw = asyncio.run(tool.handler(arguments))
                self.assertLessEqual(len(raw), 64)
                self.assertEqual(
                    json.loads(raw),
                    {"success": False, "error": "reviewed_skill_output_too_large"},
                )

    def test_oversized_body_does_not_prevent_a_separate_small_reference(self):
        write_fixture(self.root, body="large body " * 1000)
        self.catalog = load_catalog(self.root)
        _listing, loading = self.available_tools(max_result_chars=1500)
        self.assertEqual(
            invoke(loading, {"name": "fixture-skill"})["error"],
            "reviewed_skill_output_too_large",
        )
        reference = invoke(
            loading, {"name": "fixture-skill", "resource": "references/output.md"}
        )
        self.assertTrue(reference["success"])
        self.assertEqual(reference["result"]["content"], REFERENCE)

    def test_descriptions_bind_content_and_admission_to_recovery_fingerprints(self):
        first = self.available_tools()
        for tool in first:
            self.assertIn(self.catalog.digest, tool.description)
        denied = self.tools()
        self.assertNotEqual(first[0].description, denied[0].description)
        self.assertNotEqual(first[1].description, denied[1].description)
        write_fixture(self.root, body=BODY + " Revised.")
        self.catalog = load_catalog(self.root)
        revised = self.available_tools()
        self.assertNotEqual(first[0].description, revised[0].description)
        self.assertNotEqual(first[1].description, revised[1].description)
        self.assertEqual(first[1].parameters, revised[1].parameters)

    def test_capability_order_has_a_stable_description_and_no_mutation(self):
        available = frozenset({"repository.read", "repository.write"})
        first = self.tools(available_capabilities=available)
        second = self.tools(
            available_capabilities=frozenset(sorted(available, reverse=True))
        )
        invoke(first[1], {"name": "fixture-skill"})
        self.assertEqual(first[0].description, second[0].description)
        self.assertEqual(available, frozenset({"repository.read", "repository.write"}))

    def test_validators_reject_traversal_unknown_fields_and_wrong_types(self):
        listing, loading = self.available_tools()
        for arguments in ({"unknown": True}, [], None):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                listing.validate_arguments(arguments)
        invalid = (
            {},
            {"name": "../escape"},
            {"name": "fixture-skill", "resource": "../LICENSE"},
            {"name": "fixture-skill", "resource": "references/x/y.md"},
            {"name": "fixture-skill", "url": "https://example.com"},
            {"name": 1},
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                loading.validate_arguments(arguments)

    def test_operator_configuration_requires_explicit_types_and_a_minimum_budget(self):
        for limit in (True, 63, "4000"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.tools(max_result_chars=limit)
        for capabilities in (["repository.read"], {"repository.read"}, frozenset({1})):
            with self.subTest(capabilities=capabilities), self.assertRaises(TypeError):
                self.tools(available_capabilities=capabilities)
