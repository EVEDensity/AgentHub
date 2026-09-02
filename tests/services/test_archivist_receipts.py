"""T1-2: @archivist special mention + receipts preprocessing (ADR-0108)."""

from __future__ import annotations

import asyncio

from app.api.v1.chat_mission import (
    _SPECIAL_MENTIONS,
    _parse_mentions,
    _preprocess_archivist,
)
from app.services.receipts import format_receipts_as_context


def _run(coro):
    """Synchronous wrapper so tests don't need pytest-asyncio."""
    return asyncio.get_event_loop().run_until_complete(coro)


class TestSpecialMentions:
    def test_archivist_is_special(self):
        assert "archivist" in _SPECIAL_MENTIONS

    def test_archivist_parses_as_mention(self):
        assert "archivist" in _parse_mentions("@archivist 查一下")

    def test_archivist_case_insensitive(self):
        assert "Archivist" in _parse_mentions("@Archivist 查一下")

    def test_mixed_mentions(self):
        names = _parse_mentions("@archivist @dev fix this")
        assert "archivist" in names
        assert "dev" in names

    def test_plain_mentions_unchanged(self):
        names = _parse_mentions("@dev 修一下登录 bug")
        assert names == ["dev"]


class TestPreprocessArchivist:
    def test_no_receipts_falls_back_to_original_message(self):
        class _EmptyRepo:
            async def list_missions(self, workspace_id, *, limit=100, offset=0):
                return []

        enriched, receipts = _run(_preprocess_archivist(
            "@archivist 查一下上次的重构", _EmptyRepo(), "local-admin",
        ))
        assert receipts == []
        # When no receipts, enriched IS the original message (unmodified)
        assert enriched == "@archivist 查一下上次的重构"

    def test_receipts_enrich_objective(self):
        from datetime import datetime, timezone

        class _FakeMission:
            def __init__(self, mid, title, objective, status, updated_at):
                self.id = mid
                self.workspace_id = "w1"
                self.title = title
                self.objective = objective
                self.source = {}
                self.contract_id = "c1"
                self.contract_version = 1
                self.status = type("S", (), {"value": status})()
                self.plan_version = 1
                self.created_by = None
                self.created_at = None
                self.updated_at = updated_at

        class _FakeEvidence:
            def __init__(self, verdict, summary):
                self.verdict = type("V", (), {"value": verdict})()
                self.summary = summary
                self.generated_at = datetime.now(timezone.utc)

        now = datetime.now(timezone.utc)

        class _FakeRepo:
            def __init__(self):
                self._missions = [
                    _FakeMission("mis-001", "Fix login", "fix login bug", "COMPLETED", now),
                    _FakeMission("mis-002", "Refactor auth", "refactor auth flow", "FAILED", now),
                ]
                self._evidence = {
                    "mis-001": [_FakeEvidence("PASS", "All 42 tests green")],
                    "mis-002": [_FakeEvidence("FAIL", "Integration timeout")],
                }
            async def list_missions(self, workspace_id, *, limit=100, offset=0):
                return self._missions
            async def list_evidence(self, mission_id, *, limit=200, offset=0):
                return self._evidence.get(mission_id, [])

        enriched, receipts = _run(_preprocess_archivist(
            "@archivist login", _FakeRepo(), "w1",
        ))
        # "login" matches both missions (Fix login / fix login bug has "login"; Refactor auth has no "login")
        # Only mis-001 should match
        assert len(receipts) == 1
        # Enriched objective should contain receipts context + original message
        assert "mis-001" in enriched
        assert "@archivist login" in enriched


class TestFormatReceiptsAsContext:
    def test_renders_evidence_trail(self):
        receipts = [
            {
                "mission_id": "mis-abc",
                "status": "COMPLETED",
                "title": "Fix login",
                "verdicts": "PASSx1",
                "evidence": [
                    {"verdict": "PASS", "summary": "All 42 tests green"},
                ],
            }
        ]
        output = format_receipts_as_context(receipts, query="login")
        assert "mis-abc" in output
        assert "PASSx1" in output
        assert "All 42 tests green" in output
        assert "Fix login" in output

    def test_empty_receipts_shows_no_match(self):
        output = format_receipts_as_context([], query="xyz")
        assert "No matching" in output
        assert "xyz" in output

    def test_limits_evidence_per_receipt(self):
        receipts = [
            {
                "mission_id": "m1",
                "status": "SUCCESS",
                "title": "T",
                "verdicts": "PASSx5",
                "evidence": [{"verdict": "PASS", "summary": f"e{i}"} for i in range(10)],
            }
        ]
        output = format_receipts_as_context(receipts, query="t")
        # Max 3 evidence bullets per receipt
        assert output.count("→ `PASS`") == 3

    def test_preserves_all_items_caller_gives(self):
        """format_receipts_as_context renders exactly what search_receipts_inprocess feeds it.

        The 10-item limit lives in search_receipts_inprocess (limit=10 default).
        """
        receipts = [
            {
                "mission_id": f"m{i}",
                "status": "SUCCESS",
                "title": f"Mission {i}",
                "verdicts": "PASSx1",
                "evidence": [],
            }
            for i in range(5)
        ]
        output = format_receipts_as_context(receipts, query="all")
        # All 5 numbered entries present
        for i in range(1, 6):
            assert f"{i}." in output
