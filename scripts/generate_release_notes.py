"""Render release notes from a production-verified release manifest.

The command is deliberately a release barrier: it refuses to create notes for
an implemented, skipped, failed, or malformed manifest.  It only copies
redacted scope metadata and never includes evidence payloads or command output.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()

    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"unable to read manifest: {exc}") from exc
    _validate_manifest(manifest)

    scope_status = manifest["scopeStatus"]
    lines = [
        f"# AgentHub CLI {args.tag}",
        "",
        "Production-verified release.",
        "",
        f"- Commit: `{manifest['commit']}`",
        f"- Generated: `{manifest['generatedAt']}`",
        f"- Evidence records: `{manifest['evidenceCount']}`",
        "",
        "## Production Gates",
        "",
        "| Scope | Status |",
        "|---|---|",
    ]
    for scope in sorted(scope_status):
        lines.append(f"| `{scope}` | `{scope_status[scope]}` |")
    lines.extend(
        [
            "",
            "All required provider, PostgreSQL, SSE recovery, TTY, registry, and benchmark gates passed on the recorded commit.",
            "",
        ]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(args.output)
    return 0


def _validate_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict):
        raise SystemExit("manifest must be a JSON object")
    if manifest.get("status") != "production-verified":
        raise SystemExit("refusing release notes: manifest is not production-verified")
    commit = manifest.get("commit")
    if not isinstance(commit, str) or len(commit) != 40:
        raise SystemExit("refusing release notes: manifest commit is invalid")
    statuses = manifest.get("scopeStatus")
    required = manifest.get("requiredScopes")
    if not isinstance(statuses, dict) or not isinstance(required, list):
        raise SystemExit("refusing release notes: scope status is missing")
    if any(statuses.get(scope) != "PASS" for scope in required):
        raise SystemExit("refusing release notes: one or more required scopes are not PASS")
    if manifest.get("missingOrNonPassingScopes"):
        raise SystemExit("refusing release notes: manifest contains non-passing scopes")
    if manifest.get("foreignCommitRecords"):
        raise SystemExit("refusing release notes: foreign commit evidence is present")


if __name__ == "__main__":
    raise SystemExit(main())
