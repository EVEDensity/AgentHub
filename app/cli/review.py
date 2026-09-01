"""agenthub review-pr — PR review through the standard mission loop (I-4).

The command is a thin adapter over the same execute_objective() path as
run/exec: the diff is staged into the workspace together with a
review-findings schema validator written by the CLI (never by the
agent), the objective instructs the agent to produce
``review-findings.json``, and the mission's VERIFY gate replays the
validator as the independent verifier. Exit codes reuse the CLI
contract:

* ``0`` — mission SUCCEEDED and no blocking findings (review passes)
* ``1`` — mission FAILED, or blocking findings exist (actionable list
  is printed / emitted in the JSON document)
* ``2`` / ``3`` — mission CANCELLED / wait timeout
* ``4`` — infrastructure error (missing diff, unreadable findings...)
"""

from __future__ import annotations

import json
from pathlib import Path

from .runtime import EXIT_INFRA_ERROR, EXIT_MISSION_FAILED, MissionRunResult

# The verifier is written by the CLI, not by the executing agent: it
# checks the findings document structurally and cross-references every
# reported file against the diff itself, so an agent cannot invent a
# passing review for files the PR does not touch.
_VALIDATOR_SOURCE = '''\
"""Independent validator for agenthub review-pr findings (CLI-written).

Exit 0 = findings document is structurally valid and every referenced
file actually appears in the reviewed diff. Any violation exits 1 with
a specific, actionable message.
"""

import json
import re
import sys
from pathlib import Path

REQUIRED_KEYS = ("blocking", "warnings", "nits")
SEVERITIES = REQUIRED_KEYS
CATEGORIES = {"correctness", "security", "tests", "performance", "style", "docs"}


def fail(message: str) -> None:
    print(f"invalid findings: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    findings_path = Path("review-findings.json")
    if not findings_path.is_file():
        fail("review-findings.json was not created")
    try:
        doc = json.loads(findings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"review-findings.json is not readable JSON ({exc})")
    if not isinstance(doc, dict):
        fail("top level must be a JSON object")
    for key in REQUIRED_KEYS:
        if key not in doc:
            fail(f"missing required key '{key}'")
        if not isinstance(doc[key], list):
            fail(f"'{key}' must be a list")

    diff_files = set()
    diff_text = Path("pr.diff").read_text(encoding="utf-8", errors="replace")
    for match in re.finditer(r"^diff --git a/(.+?) b/(.+?)$", diff_text, re.M):
        diff_files.add(match.group(2))

    for severity in SEVERITIES:
        for index, item in enumerate(doc[severity]):
            if not isinstance(item, dict):
                fail(f"{severity}[{index}] must be an object")
            for field in ("file", "issue", "suggestion"):
                value = item.get(field)
                if not isinstance(value, str) or not value.strip():
                    fail(f"{severity}[{index}].{field} must be a non-empty string")
            if item.get("category") not in CATEGORIES:
                fail(
                    f"{severity}[{index}].category must be one of "
                    + ", ".join(sorted(CATEGORIES))
                )
            if diff_files and item["file"] not in diff_files:
                fail(
                    f"{severity}[{index}].file '{item[\"file\"]}' does not appear "
                    "in the reviewed diff"
                )

    print(
        "findings valid: "
        + ", ".join(f"{s}={len(doc[s])}" for s in SEVERITIES)
    )


if __name__ == "__main__":
    main()
'''

_OBJECTIVE_TEMPLATE = """\
Review the pull-request diff in pr.diff and report findings.

Read pr.diff carefully, then create review-findings.json in this
workspace with exactly this shape:

{{
  "blocking": [
    {{"file": "<path from the diff>", "category": "correctness|security|tests|performance|style|docs",
      "issue": "<what is wrong, specific>", "suggestion": "<concrete fix>"}}
  ],
  "warnings": [],
  "nits": []
}}

Rules:
- blocking: bugs the diff introduces, security regressions, or missing
  tests for changed behavior. Only real, defensible problems — never
  padding.
- warnings: likely problems that need human judgement.
- nits: style/docs polish.
- Every finding must reference a file that appears in pr.diff.
- If the diff is clean, all three arrays are empty.
- Do not modify any file other than review-findings.json.

VERIFY: python _agenthub_review_validate.py
"""


def build_review_objective() -> str:
    return _OBJECTIVE_TEMPLATE


def stage_review_workspace(workspace: Path, diff_text: str) -> None:
    """Stage the diff and the CLI-written verifier into the workspace."""
    (workspace / "pr.diff").write_text(diff_text, encoding="utf-8")
    (workspace / "_agenthub_review_validate.py").write_text(
        _VALIDATOR_SOURCE, encoding="utf-8"
    )


def load_findings(workspace: Path) -> dict | None:
    path = workspace / "review-findings.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def review_exit_code(result: MissionRunResult, findings: dict | None) -> int:
    """Map mission result + findings onto the CLI exit-code contract."""
    if result.exit_code != 0:
        return result.exit_code
    if findings is None:
        # Mission claimed success without a readable findings document —
        # that is an infrastructure-level breach of the review contract.
        return EXIT_INFRA_ERROR
    if findings.get("blocking"):
        return EXIT_MISSION_FAILED
    return 0
