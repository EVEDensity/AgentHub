"""Skipped, cancelled or missing required checks must never turn CI green."""

from __future__ import annotations

import unittest

from scripts.ci_gate import CORE_CHECKS, OPTIONAL_CHECKS, failed_checks


def successful_core() -> dict:
    results = {name: {"result": "success"} for name in CORE_CHECKS}
    results["changes"]["outputs"] = {flag: "false" for flag in OPTIONAL_CHECKS.values()}
    results.update({name: {"result": "skipped"} for name in OPTIONAL_CHECKS})
    return results


class CiGateTests(unittest.TestCase):
    def test_unaffected_optional_checks_may_skip(self) -> None:
        self.assertEqual(failed_checks(successful_core()), {})

    def test_required_jobs_cannot_skip_fail_cancel_or_disappear(self) -> None:
        for job in CORE_CHECKS:
            for status in ("skipped", "failure", "cancelled", "missing"):
                with self.subTest(job=job, status=status):
                    results = successful_core()
                    results[job] = {"result": status}
                    self.assertIn(job, failed_checks(results))

    def test_selected_check_must_run_and_pass(self) -> None:
        for job, flag in OPTIONAL_CHECKS.items():
            results = successful_core()
            results["changes"]["outputs"][flag] = "true"
            self.assertIn(job, failed_checks(results))
            results[job] = {"result": "success"}
            self.assertEqual(failed_checks(results), {})

    def test_failed_optional_check_is_never_hidden(self) -> None:
        results = successful_core()
        results["go"] = {"result": "failure"}
        self.assertEqual(failed_checks(results), {"go": "failure"})

    def test_missing_optional_job_is_never_hidden(self) -> None:
        results = successful_core()
        del results["docker"]
        self.assertEqual(failed_checks(results), {"docker": "missing"})

    def test_incomplete_selection_fails_closed(self) -> None:
        results = successful_core()
        del results["changes"]["outputs"]["frontend"]
        self.assertIn("selection:frontend", failed_checks(results))


if __name__ == "__main__":
    unittest.main()
