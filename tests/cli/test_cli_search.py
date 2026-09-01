"""Unit tests for the receipts search slice (ADR-0108 P0).

Covers the pure keyword/time filtering over mission records, the
verdict summary, and the receipt building — the parts that must never
invent history. Server-backed behavior follows the e2e pattern in
tests/cli/test_cli_e2e.py.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.cli.main import build_parser
from app.cli.runtime import (
    build_receipt,
    filter_missions_by_query,
    summarize_verdicts,
)


def _mission(
    mission_id: str,
    *,
    title: str = "",
    objective: str = "",
    status: str = "SUCCEEDED",
    updated_at: str = "2026-09-01T00:00:00+00:00",
) -> dict:
    return {
        "id": mission_id,
        "title": title,
        "objective": objective,
        "status": status,
        "updated_at": updated_at,
    }


class FilterMissionsByQueryTests(unittest.TestCase):
    def test_single_term_matches_objective_case_insensitive(self) -> None:
        missions = [
            _mission("m1", objective="Fix the login bug"),
            _mission("m2", objective="Update documentation"),
        ]
        matched = filter_missions_by_query(missions, "login")
        self.assertEqual([m["id"] for m in matched], ["m1"])

    def test_all_terms_must_match(self) -> None:
        missions = [
            _mission("m1", title="login", objective="bug"),
            _mission("m2", title="login", objective="docs"),
        ]
        matched = filter_missions_by_query(missions, "login bug")
        self.assertEqual([m["id"] for m in matched], ["m1"])

    def test_title_and_objective_are_both_searched(self) -> None:
        missions = [_mission("m1", title="deploy", objective="other")]
        matched = filter_missions_by_query(missions, "deploy")
        self.assertEqual(len(matched), 1)

    def test_status_filter(self) -> None:
        missions = [
            _mission("m1", objective="login", status="FAILED"),
            _mission("m2", objective="login", status="SUCCEEDED"),
        ]
        matched = filter_missions_by_query(
            missions, "login", status="succeeded"
        )
        self.assertEqual([m["id"] for m in matched], ["m2"])

    def test_days_window_keeps_recent_and_undated(self) -> None:
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        missions = [
            _mission("recent", updated_at="2026-08-31T00:00:00+00:00"),
            _mission("old", updated_at="2026-01-01T00:00:00+00:00"),
            _mission("undated", updated_at=""),
        ]
        matched = filter_missions_by_query(missions, "", days=30, now=now)
        self.assertEqual(
            sorted(m["id"] for m in matched), ["recent", "undated"]
        )

    def test_z_suffix_timestamps_parse(self) -> None:
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        missions = [
            _mission("zulu", updated_at="2026-08-30T12:00:00Z"),
        ]
        matched = filter_missions_by_query(missions, "", days=30, now=now)
        self.assertEqual([m["id"] for m in matched], ["zulu"])

    def test_naive_timestamp_is_treated_as_utc(self) -> None:
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        missions = [
            _mission("naive", updated_at="2026-08-30 12:00:00"),
            _mission("old-naive", updated_at="2025-01-01 00:00:00"),
        ]
        matched = filter_missions_by_query(missions, "", days=30, now=now)
        self.assertEqual([m["id"] for m in matched], ["naive"])

    def test_camel_case_timestamps_are_accepted(self) -> None:
        # The v1 API serializes with camelCase aliases.
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        missions = [
            {
                "id": "recent",
                "title": "",
                "objective": "",
                "status": "SUCCEEDED",
                "updatedAt": "2026-08-31T00:00:00+00:00",
            },
            {
                "id": "old",
                "title": "",
                "objective": "",
                "status": "SUCCEEDED",
                "updatedAt": "2026-01-01T00:00:00+00:00",
            },
        ]
        matched = filter_missions_by_query(missions, "", days=30, now=now)
        self.assertEqual([m["id"] for m in matched], ["recent"])

    def test_future_updates_are_kept(self) -> None:
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        missions = [
            _mission("future", updated_at="2026-10-01T00:00:00+00:00"),
        ]
        matched = filter_missions_by_query(missions, "", days=30, now=now)
        self.assertEqual([m["id"] for m in matched], ["future"])

    def test_no_terms_returns_all(self) -> None:
        missions = [_mission("m1"), _mission("m2")]
        matched = filter_missions_by_query(missions, "")
        self.assertEqual(len(matched), 2)


class SummarizeVerdictsTests(unittest.TestCase):
    def test_empty_evidence_reports_honestly(self) -> None:
        self.assertEqual(summarize_verdicts([]), "NO-EVIDENCE")

    def test_counts_by_verdict(self) -> None:
        evidence = [
            {"verdict": "PASS"},
            {"verdict": "PASS"},
            {"verdict": "FAIL"},
        ]
        self.assertEqual(summarize_verdicts(evidence), "FAILx1 PASSx2")

    def test_missing_verdict_becomes_unknown(self) -> None:
        self.assertEqual(summarize_verdicts([{}]), "UNKNOWNx1")


class BuildReceiptTests(unittest.TestCase):
    def test_receipt_carries_mission_and_evidence(self) -> None:
        mission = _mission(
            "mis-1", title="t", objective="fix login", status="SUCCEEDED"
        )
        evidence = [
            {
                "verdict": "PASS",
                "summary": "VERIFY exit 0",
                "generated_at": "2026-09-01T00:00:00+00:00",
            }
        ]
        receipt = build_receipt(mission, evidence)
        self.assertEqual(receipt["mission_id"], "mis-1")
        self.assertEqual(receipt["status"], "SUCCEEDED")
        self.assertEqual(receipt["objective"], "fix login")
        self.assertEqual(receipt["verdicts"], "PASSx1")
        self.assertEqual(receipt["evidence"][0]["summary"], "VERIFY exit 0")

    def test_receipt_tolerates_camel_case_records(self) -> None:
        mission = {
            "id": "mis-1",
            "title": "t",
            "objective": "fix login",
            "status": "SUCCEEDED",
            "updatedAt": "2026-09-01T00:00:00+00:00",
        }
        evidence = [
            {
                "verdict": "PASS",
                "summary": "ok",
                "generatedAt": "2026-09-01T00:00:00+00:00",
            }
        ]
        receipt = build_receipt(mission, evidence)
        self.assertEqual(receipt["updated_at"], "2026-09-01T00:00:00+00:00")
        self.assertEqual(
            receipt["evidence"][0]["generated_at"],
            "2026-09-01T00:00:00+00:00",
        )


class SearchParserTests(unittest.TestCase):
    def test_search_parses_flags(self) -> None:
        args = build_parser().parse_args(
            [
                "search",
                "login bug",
                "--status",
                "SUCCEEDED",
                "--days",
                "30",
                "--limit",
                "5",
                "--json",
            ]
        )
        self.assertEqual(args.query, "login bug")
        self.assertEqual(args.status, "SUCCEEDED")
        self.assertEqual(args.days, 30)
        self.assertEqual(args.limit, 5)
        self.assertTrue(args.json)

    def test_replay_requires_mission_id(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["replay"])
        args = build_parser().parse_args(["replay", "mis-1"])
        self.assertEqual(args.mission_id, "mis-1")


if __name__ == "__main__":
    unittest.main()
