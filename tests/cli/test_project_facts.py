"""Unit tests for flat key-scoped project facts (ADR-0107 items 3-4).

Covers the Markdown parse/render round trip, the key-level overwrite
semantics, and the gated injection rule: only facts sharing a keyword
with the current objective enter the prompt, never the whole store.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.cli.project_facts import (
    Fact,
    facts_block_for_objective,
    load_facts,
    memory_file_path,
    parse_facts,
    remove_fact,
    render_facts,
    select_facts_for_objective,
    set_fact,
)


class ParseRenderTests(unittest.TestCase):
    def test_parse_round_trip(self) -> None:
        facts = [
            Fact(section="python", key="interpreter", value="venv"),
            Fact(section="python", key="version", value="3.12"),
            Fact(section="build", key="target", value="windows"),
        ]
        text = render_facts(facts)
        self.assertEqual(parse_facts(text), facts)

    def test_unknown_lines_are_skipped(self) -> None:
        text = (
            "# AgentHub Project Facts\n"
            "\n"
            "- orphan: no section yet, dropped\n"
            "## python\n"
            "- interpreter: venv\n"
            "free-form prose line\n"
            "- novalue\n"
        )
        facts = parse_facts(text)
        self.assertEqual(
            facts, [Fact(section="python", key="interpreter", value="venv")]
        )


class SetRemoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "memory.md"

    def test_set_then_update_supersedes_in_place(self) -> None:
        self.assertEqual(set_fact(self.path, "python", "interpreter", "venv"), "set")
        self.assertEqual(set_fact(self.path, "python", "interpreter", "system"), "updated")
        facts = load_facts(self.path)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].value, "system")

    def test_same_value_is_unchanged(self) -> None:
        set_fact(self.path, "python", "interpreter", "venv")
        self.assertEqual(
            set_fact(self.path, "python", "interpreter", "venv"), "unchanged"
        )

    def test_update_keeps_unrelated_facts_in_order(self) -> None:
        set_fact(self.path, "build", "target", "windows")
        set_fact(self.path, "python", "interpreter", "venv")
        set_fact(self.path, "build", "target", "linux")
        # The superseded fact keeps its original position; the unrelated
        # fact and the file's section order are untouched.
        facts = load_facts(self.path)
        self.assertEqual(
            facts,
            [
                Fact(section="build", key="target", value="linux"),
                Fact(section="python", key="interpreter", value="venv"),
            ],
        )

    def test_remove(self) -> None:
        set_fact(self.path, "python", "interpreter", "venv")
        self.assertTrue(remove_fact(self.path, "python", "interpreter"))
        self.assertFalse(remove_fact(self.path, "python", "interpreter"))
        self.assertEqual(load_facts(self.path), [])


class GatedInjectionTests(unittest.TestCase):
    def test_only_overlapping_facts_are_selected(self) -> None:
        facts = [
            Fact(section="python", key="interpreter", value="use venv"),
            Fact(section="build", key="target", value="windows only"),
        ]
        selected = select_facts_for_objective(facts, "fix the venv bootstrap")
        self.assertEqual(
            selected, [Fact(section="python", key="interpreter", value="use venv")]
        )

    def test_higher_overlap_ranks_first(self) -> None:
        facts = [
            Fact(section="build", key="target", value="windows"),
            Fact(section="python", key="windows", value="python on windows"),
        ]
        # fact1 overlaps on "windows" (1 term); fact2 overlaps on
        # "python" and "windows" (2 terms) and must rank first.
        selected = select_facts_for_objective(facts, "python windows")
        self.assertEqual(selected[0], facts[1])

    def test_empty_objective_injects_nothing(self) -> None:
        facts = [Fact(section="python", key="interpreter", value="venv")]
        self.assertEqual(select_facts_for_objective(facts, ""), [])
        self.assertEqual(select_facts_for_objective(facts, "the of and"), [])

    def test_limit_caps_injected_facts(self) -> None:
        facts = [
            Fact(section="s", key=f"k{index}", value="venv") for index in range(5)
        ]
        selected = select_facts_for_objective(facts, "venv", limit=2)
        self.assertEqual(len(selected), 2)

    def test_block_for_objective_reads_store_and_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = memory_file_path(directory)
            set_fact(path, "python", "interpreter", "use venv")
            set_fact(path, "build", "target", "windows")
            block = facts_block_for_objective(directory, "fix venv bootstrap")
            self.assertIn("interpreter", block)
            self.assertIn("use venv", block)
            self.assertNotIn("windows", block)
            self.assertIn("项目事实", block)

    def test_missing_store_yields_empty_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            block = facts_block_for_objective(Path(tmp), "anything")
            self.assertEqual(block, "")


if __name__ == "__main__":
    unittest.main()
