"""Aggregate production evidence into a release manifest.

The command is intentionally conservative: the release is ``production-
verified`` only when every required evidence scope has a PASS record and the
benchmark has no threshold failures.  SKIP, FAIL, missing, malformed, or
stale records keep the manifest at ``implemented``.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOCAL_PROJECT_REQUIRED_SCOPES = {
    "provider",
    "tty",
    "github-release",
    "benchmark",
}

DISTRIBUTED_REQUIRED_SCOPES = LOCAL_PROJECT_REQUIRED_SCOPES | {
    "postgres",
    "sse-recovery",
}
RELEASE_PROFILES = {
    "local-project": LOCAL_PROJECT_REQUIRED_SCOPES,
    "distributed": DISTRIBUTED_REQUIRED_SCOPES,
}

# Compatibility for callers importing the default profile's scope set.
REQUIRED_SCOPES = LOCAL_PROJECT_REQUIRED_SCOPES

SCOPE_ALIASES = {
    "provider-protocol": "provider",
    "mission-closed-loop": "provider",
    "postgres-listener": "postgres",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, default=Path("artifacts/production"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/production/release-manifest.json"))
    parser.add_argument("--report", type=Path, default=Path("PRODUCTION_VERIFICATION.md"))
    parser.add_argument("--expected-commit", default=_commit_sha())
    parser.add_argument(
        "--profile",
        choices=sorted(RELEASE_PROFILES),
        default="local-project",
        help="Evidence policy; local-project does not require PostgreSQL.",
    )
    args = parser.parse_args()
    required_scopes = RELEASE_PROFILES[args.profile]
    records = [
        record
        for record in _load_records(args.evidence_root)
        if SCOPE_ALIASES.get(str(record.get("scope") or ""), str(record.get("scope") or ""))
        in required_scopes
    ]
    foreign_commits = sorted({
        str(record.get("commit"))
        for record in records
        if record.get("commit") and record.get("commit") != args.expected_commit
    })
    by_scope: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        raw_scope = str(record.get("scope") or "")
        scope = SCOPE_ALIASES.get(raw_scope, raw_scope)
        by_scope.setdefault(scope, []).append(record)
    scope_status = {
        scope: _latest_status(by_scope.get(scope, []))
        for scope in sorted(required_scopes)
    }
    missing = sorted(scope for scope, status in scope_status.items() if status != "PASS")
    provider_kind_status = {
        kind: _latest_status(
            [record for record in records if str(record.get("scope") or "") == kind]
        )
        for kind in ("provider-protocol", "mission-closed-loop")
    }
    if any(status != "PASS" for status in provider_kind_status.values()):
        if "provider" not in missing:
            missing.append("provider")
            missing.sort()
    verified = not missing and not foreign_commits and all(
        record.get("status") == "PASS" and not record.get("thresholdFailures")
        for record in records
        if record.get("scope") == "benchmark"
    )
    manifest = {
        "schemaVersion": 1,
        "releaseProfile": args.profile,
        "status": "production-verified" if verified else "implemented",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "commit": _commit_sha(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "requiredScopes": sorted(required_scopes),
        "scopeStatus": scope_status,
        "missingOrNonPassingScopes": missing,
        "foreignCommitRecords": foreign_commits,
        "evidenceCount": len(records),
        "evidence": [
            {
                "runId": record.get("runId"),
                "scope": record.get("scope"),
                "status": record.get("status"),
                "evidenceLevel": record.get("evidenceLevel"),
                "commit": record.get("commit"),
            }
            for record in records
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report.write_text(_render_report(manifest), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0 if verified else 1


def _render_report(manifest: dict[str, Any]) -> str:
    status = str(manifest["status"])
    lines = [
        "# Production Verification",
        "",
        f"> Status: {status}",
        f"> Release profile: {manifest['releaseProfile']}",
        f"> Commit: {manifest['commit']}",
        f"> Generated: {manifest['generatedAt']}",
        "",
        "This report is generated from redacted JSON evidence under `artifacts/production/`.",
        "A scope is accepted only when its latest evidence record is `PASS`.",
        "",
        "| Scope | Latest status |",
        "|---|---|",
    ]
    for scope, scope_status in manifest["scopeStatus"].items():
        lines.append(f"| `{scope}` | `{scope_status}` |")
    lines.extend(["", "## Decision", ""])
    if status == "production-verified":
        lines.append("All required production evidence scopes passed.")
    else:
        missing = ", ".join(f"`{item}`" for item in manifest["missingOrNonPassingScopes"])
        lines.append(f"Project remains `implemented`; missing or non-passing scopes: {missing}.")
    lines.append("")
    return "\n".join(lines)


def _load_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not root.exists():
        return records
    for path in root.rglob("*.json"):
        if path.name == "release-manifest.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("runId") and payload.get("scope"):
            records.append(payload)
    return records


def _latest_status(records: list[dict[str, Any]]) -> str:
    if not records:
        return "MISSING"
    records = sorted(records, key=lambda item: str(item.get("observedAt") or ""))
    return str(records[-1].get("status") or "UNKNOWN")


def _commit_sha() -> str:
    try:
        value = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return value if len(value) == 40 else "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
