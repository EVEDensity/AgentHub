"""Fail the stable merge gate when a required or selected check does not pass."""

from __future__ import annotations

import json
import os
import sys

CORE_CHECKS = {"changes", "quality", "python", "postgres"}
OPTIONAL_CHECKS = {
    "windows-cli": "windows_cli", "frontend": "frontend", "go": "go",
    "rust": "rust", "docker": "docker", "smoke": "smoke",
}


def failed_checks(results: dict) -> dict[str, str]:
    required = set(CORE_CHECKS)
    failures = {}
    selection = results.get("changes", {})
    if selection.get("result") == "success":
        outputs = selection.get("outputs", {})
        for job, flag in OPTIONAL_CHECKS.items():
            if outputs.get(flag) == "true":
                required.add(job)
            elif outputs.get(flag) != "false":
                failures[f"selection:{flag}"] = "missing or invalid output"
    for name in CORE_CHECKS | OPTIONAL_CHECKS.keys():
        result = results.get(name, {}).get("result", "missing")
        if result != "success" and not (name not in required and result == "skipped"):
            failures[name] = result
    return failures


def main() -> int:
    results = json.loads(os.environ["RESULTS"])
    failures = failed_checks(results)
    for name, job in results.items():
        print(f"{name}: {job.get('result', 'missing')}")
    if failures:
        print(f"CI failed: {failures}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
