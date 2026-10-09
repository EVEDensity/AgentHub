"""Exercise the quality ratchet with real base commits and working-tree changes."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from benchmarks.quality_delta import _functions, check_quality, run_quality


def assignments(count: int) -> str:
    return "".join(f"value_{index} = {index}\n" for index in range(count))


def function_source(decisions: int, *, name: str = "work") -> str:
    return f"def {name}(value):\n" + "".join(
        f"    if value == {index}:\n        return {index}\n"
        for index in range(decisions)
    ) + "    return -1\n"


def lambda_source(decisions: int, *, name: str = "handler") -> str:
    return f"{name} = lambda value: " + "".join(
        f"{index} if value == {index} else " for index in range(decisions)
    ) + "-1\n"


class QualityDeltaTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="agenthub-quality-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Quality Test")
        self.git("config", "user.email", "quality-test@agenthub.local")
        self.write("app/seed.py", "SEED = True\n")
        self.commit()

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True,
            text=True, encoding="utf-8", check=True,
        )
        return result.stdout.strip()

    def write(self, path: str, source: str) -> None:
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(source, encoding="utf-8")

    def commit(self) -> None:
        self.git("add", ".")
        self.git("commit", "-qm", "quality baseline")
        self.base = self.git("rev-parse", "HEAD")

    def report(self) -> dict:
        return check_quality(self.root, self.base)

    def test_new_file_at_limit_passes_and_over_limit_fails(self) -> None:
        self.write("app/new.py", assignments(500))
        self.assertTrue(self.report()["passed"])
        self.write("app/new.py", assignments(501))
        report = self.report()
        self.assertFalse(report["passed"])
        self.assertIn("app/new.py", report["checked"])
        self.assertTrue(any("501 > 500" in issue for issue in report["issues"]))

    def test_existing_oversized_file_may_stay_same_or_shrink(self) -> None:
        self.write("app/legacy.py", assignments(850))
        self.commit()
        self.write("app/legacy.py", assignments(850) + "# reviewed\n")
        self.assertTrue(self.report()["passed"])
        self.write("app/legacy.py", assignments(820))
        self.assertTrue(self.report()["passed"])

    def test_existing_oversized_file_cannot_grow(self) -> None:
        self.write("app/legacy.py", assignments(850))
        self.commit()
        self.write("app/legacy.py", assignments(851))
        self.assertFalse(self.report()["passed"])

    def test_existing_small_file_obeys_standard_limit(self) -> None:
        self.write("app/existing.py", assignments(20))
        self.commit()
        self.write("app/existing.py", assignments(800))
        self.assertTrue(self.report()["passed"])
        self.write("app/existing.py", assignments(801))
        self.assertFalse(self.report()["passed"])

    def test_new_function_cc_15_passes_and_cc_16_fails(self) -> None:
        self.write("app/new.py", function_source(14))
        self.assertTrue(self.report()["passed"])
        self.write("app/new.py", function_source(15))
        self.assertFalse(self.report()["passed"])

    def test_new_function_in_existing_file_still_obeys_cc_15(self) -> None:
        self.write("app/seed.py", "SEED = True\n" + function_source(15))
        self.assertFalse(self.report()["passed"])

    def test_existing_high_complexity_function_may_stay_same_or_shrink(self) -> None:
        self.write("app/legacy.py", function_source(23))
        self.commit()
        self.write("app/legacy.py", function_source(23) + "# reviewed\n")
        self.assertTrue(self.report()["passed"])
        self.write("app/legacy.py", function_source(22))
        self.assertTrue(self.report()["passed"])

    def test_existing_high_complexity_function_cannot_grow(self) -> None:
        self.write("app/legacy.py", function_source(23))
        self.commit()
        self.write("app/legacy.py", function_source(24))
        self.assertFalse(self.report()["passed"])

    def test_invalid_ref_returns_failure_report(self) -> None:
        output = self.root / "quality.json"
        with redirect_stdout(io.StringIO()):
            code = run_quality(self.root, "missing-base-ref", str(output))
        self.assertEqual(code, 1)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        self.assertTrue(report["issues"])

    def test_invalid_syntax_fails_without_crashing_the_driver(self) -> None:
        self.write("app/broken.py", "def broken(:\n")
        report = self.report()
        self.assertFalse(report["passed"])
        self.assertTrue(any("syntax error" in issue for issue in report["issues"]))

    def test_deleted_legacy_debt_is_not_a_failure(self) -> None:
        self.write("app/legacy.py", assignments(850))
        self.commit()
        (self.root / "app/legacy.py").unlink()
        self.assertTrue(self.report()["passed"])

    def test_copy_does_not_inherit_legacy_exemption(self) -> None:
        self.write("app/legacy.py", assignments(850))
        self.commit()
        self.write("app/copy.py", assignments(850))
        self.assertFalse(self.report()["passed"])

    def test_rename_currently_counts_as_new_code(self) -> None:
        self.write("app/legacy.py", assignments(850))
        self.commit()
        self.git("mv", "app/legacy.py", "app/renamed.py")
        self.assertFalse(self.report()["passed"])

    def test_unicode_and_space_paths_cannot_bypass_validation(self) -> None:
        for name in ("space name.py", "中文模块.py"):
            with self.subTest(name=name):
                path = f"app/{name}"
                self.write(path, assignments(501))
                report = self.report()
                self.assertIn(path, report["checked"])
                self.assertFalse(report["passed"])
                (self.root / path).unlink()

    def test_conditionally_defined_function_is_checked(self) -> None:
        source = "if True:\n" + "".join(
            "    " + line + "\n" for line in function_source(15).splitlines()
        )
        self.write("app/conditional.py", source)
        self.assertFalse(self.report()["passed"])

    def test_nested_conditionally_defined_function_is_checked(self) -> None:
        source = "def outer():\n    if True:\n" + "".join(
            "        " + line + "\n" for line in function_source(15).splitlines()
        )
        self.write("app/nested.py", source)
        report = self.report()
        self.assertFalse(report["passed"])
        self.assertTrue(any("outer.work" in issue for issue in report["issues"]))

    def test_nested_function_complexity_does_not_inflate_its_parent(self) -> None:
        import ast

        source = "def outer():\n" + "".join(
            "    " + line + "\n" for line in function_source(14).splitlines()
        )
        values = _functions(ast.parse(source))
        self.assertEqual(values["outer"], 1)
        self.assertEqual(values["outer.work"], 15)

    def test_match_cases_contribute_to_function_complexity(self) -> None:
        source = "def work(value):\n    match value:\n" + "".join(
            f"        case {index}:\n            return {index}\n"
            for index in range(15)
        )
        self.write("app/pattern.py", source)
        self.assertFalse(self.report()["passed"])

    def test_functions_inside_match_cases_are_checked(self) -> None:
        source = "match True:\n    case True:\n" + "".join(
            "        " + line + "\n" for line in function_source(15).splitlines()
        )
        self.write("app/conditional_pattern.py", source)
        self.assertFalse(self.report()["passed"])

    def test_tracked_unicode_and_space_paths_keep_the_actual_baseline(self) -> None:
        path = "app/中文 baseline.py"
        self.write(path, assignments(850))
        self.commit()
        self.write(path, assignments(849))
        report = self.report()
        self.assertTrue(report["passed"])
        self.assertIn(path, report["checked"])
        self.write(path, assignments(851))
        self.assertFalse(self.report()["passed"])

    def test_new_lambda_cc_15_passes_and_cc_16_fails(self) -> None:
        self.write("app/new.py", lambda_source(14))
        self.assertTrue(self.report()["passed"])
        self.write("app/new.py", lambda_source(15))
        report = self.report()
        self.assertFalse(report["passed"])
        self.assertTrue(any("handler.<lambda#1>: CC=16 > 15" in issue for issue in report["issues"]))

    def test_existing_lambda_cannot_grow_but_comments_do_not_reset_its_identity(self) -> None:
        self.write("app/legacy.py", lambda_source(23))
        self.commit()
        self.write("app/legacy.py", "# moved down one line\n" + lambda_source(23))
        self.assertTrue(self.report()["passed"])
        self.write("app/legacy.py", lambda_source(22))
        self.assertTrue(self.report()["passed"])
        self.write("app/legacy.py", lambda_source(24))
        self.assertFalse(self.report()["passed"])

    def test_new_lambda_in_existing_module_cannot_inherit_another_binding_limit(self) -> None:
        self.write("app/legacy.py", lambda_source(23))
        self.commit()
        self.write("app/legacy.py", lambda_source(23) + lambda_source(15, name="new_handler"))
        self.assertFalse(self.report()["passed"])

    def test_annotated_and_walrus_bound_lambdas_are_checked(self) -> None:
        expression = lambda_source(15).split(" = ", 1)[1].strip()
        for source in (f"handler: object = {expression}\n", f"(handler := {expression})\n"):
            with self.subTest(source=source):
                self.write("app/new.py", source)
                self.assertFalse(self.report()["passed"])

    def test_nested_lambda_is_separate_from_its_enclosing_function(self) -> None:
        import ast

        expression = lambda_source(15).split(" = ", 1)[1].strip()
        source = f"def outer():\n    return ({expression})(1)\n"
        values = _functions(ast.parse(source))
        self.assertEqual(values["outer"], 1)
        self.assertEqual(values["outer.<lambda#1>"], 16)
        self.write("app/new.py", source)
        self.assertFalse(self.report()["passed"])

    def test_full_historical_audit_also_lists_lambda_functions(self) -> None:
        import ast

        from benchmarks.gates import _iter_functions, cyclomatic_complexity

        nodes = dict(_iter_functions(ast.parse(lambda_source(20))))
        self.assertIn("handler.<lambda#1>", nodes)
        self.assertEqual(cyclomatic_complexity(nodes["handler.<lambda#1>"]), 21)


if __name__ == "__main__":
    unittest.main()
