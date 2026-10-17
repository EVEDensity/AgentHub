"""Reviewed Desktop opt-in preserves names, policy and default workspace reads."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.services.desktop_runner_tools import WEB_SEARCH_ENV, build_desktop_runner_tools
from app.services.model_port import build_function_tool_schemas
from app.services.reviewed_skills import load_bundled_catalog


class ReviewedDesktopSkillTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.catalog = load_bundled_catalog()
        self.capabilities = frozenset({'repository.read', 'repository.write', 'web.read'})
        shadow = self.root / '.claude/skills/code-review'
        shadow.mkdir(parents=True)
        (shadow / 'SKILL.md').write_text('---\nname: code-review\ndescription: local\n---\nWORKSPACE_SHADOW', encoding='utf-8')

    def tools(self, *, reviewed=True, capabilities=None, catalog=None):
        options = {}
        if reviewed:
            options = {'reviewed_catalog': catalog or self.catalog,
                       'reviewed_capabilities': self.capabilities if capabilities is None else capabilities}
        with patch.dict(os.environ, {WEB_SEARCH_ENV: '0'}):
            return build_desktop_runner_tools(self.root, permission_mode='suggest', **options)

    async def test_reviewed_opt_in_replaces_only_the_existing_read_pair(self):
        original = self.tools(reviewed=False)
        reviewed = self.tools()
        self.assertEqual([tool.name for tool in original], [tool.name for tool in reviewed])
        baseline = {tool.name: tool for tool in original}
        selected = {tool.name: tool for tool in reviewed}
        for name in selected.keys() - {'skill_list', 'skill_load'}:
            self.assertEqual(build_function_tool_schemas([baseline[name]]), build_function_tool_schemas([selected[name]]))
        default_body = await baseline['skill_load'].handler({'name': 'code-review'})
        reviewed_body = await selected['skill_load'].handler({'name': 'code-review'})
        self.assertIn('WORKSPACE_SHADOW', default_body)
        self.assertNotIn('WORKSPACE_SHADOW', reviewed_body)
        self.assertEqual(json.loads(reviewed_body)['result']['body'], self.catalog.load('code-review').body)

    async def test_all_bundled_results_fit_the_desktop_default_without_truncation(self):
        selected = {tool.name: tool for tool in self.tools()}
        discovered = await selected['skill_list'].handler({})
        self.assertLessEqual(len(discovered), 4000)
        self.assertTrue(json.loads(discovered)['success'])
        for metadata in self.catalog.list_metadata():
            for options in ({'name': metadata.name}, {'name': metadata.name, 'resource': 'references/output.md'}):
                value = await selected['skill_load'].handler(options)
                self.assertLessEqual(len(value), 4000)
                self.assertTrue(json.loads(value)['success'], value)

    async def test_missing_capability_is_explicit_and_never_falls_back(self):
        selected = {tool.name: tool for tool in self.tools(capabilities=frozenset())}
        denied = json.loads(await selected['skill_load'].handler({'name': 'code-review'}))
        self.assertFalse(denied['success'])
        self.assertEqual(denied['missingCapabilities'], ['repository.read'])
        self.assertNotIn('WORKSPACE_SHADOW', json.dumps(denied))

    def test_catalog_and_capability_changes_alter_recovery_tool_material(self):
        def material(tools):
            return build_function_tool_schemas([tool for tool in tools if tool.name in {'skill_list', 'skill_load'}])

        original = material(self.tools())
        changed_catalog = replace(self.catalog, digest='sha256:' + 'f' * 64)
        self.assertNotEqual(original, material(self.tools(catalog=changed_catalog)))
        self.assertNotEqual(original, material(self.tools(capabilities=frozenset({'repository.read'}))))


if __name__ == '__main__':
    unittest.main()
