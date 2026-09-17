"""Shared redacted production-evidence writer for CLI acceptance gates.

The writer deliberately records only execution metadata and caller-provided
summary fields.  It never reads prompts, workspace files, headers, or secret
values.  Real runs should set ``AGENTHUB_PRODUCTION_EVIDENCE_DIR`` to keep
artifacts under ``artifacts/production``; test callers may provide a temporary
mirror path explicitly.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = ROOT / "artifacts" / "production"

_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "headers",
    "prompt",
    "messages",
    "workspace_content",
    "file_content",
    "source_content",
}


def new_evidence(*, scope: str, evidence_level: str, **fields: Any) -> dict[str, Any]:
    """Create a bounded evidence envelope with non-sensitive environment data."""
    run_id = "run-" + uuid.uuid4().hex
    observed_at = datetime.now(timezone.utc).isoformat()
    commit = _commit_sha()
    environment = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "ci": bool(os.environ.get("CI")),
    }
    record: dict[str, Any] = {
        "schemaVersion": 2,
        "evidenceLevel": evidence_level,
        "scope": scope,
        "runId": run_id,
        "commit": commit,
        "environment": environment,
        "observedAt": observed_at,
    }
    record.update(fields)
    # Envelope fields are owned by the evidence writer, never by a benchmark
    # or provider payload copied into the summary.
    record["schemaVersion"] = 2
    record["evidenceLevel"] = evidence_level
    record["scope"] = scope
    record["runId"] = run_id
    record["commit"] = commit
    record["environment"] = environment
    record["observedAt"] = observed_at
    _assert_safe(record)
    return record


def write_evidence(
    record: Mapping[str, Any],
    *,
    scope: str,
    mirror_path: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Write canonical evidence and optionally a caller-requested mirror.

    The canonical file is enabled when ``AGENTHUB_PRODUCTION_EVIDENCE_DIR`` is
    set, or when no mirror path is supplied.  This keeps unit tests isolated
    while making direct production script execution produce an artifact by
    default.  The mirror is useful for CI upload steps and is never allowed to
    alter the canonical record.
    """
    payload = dict(record)
    _assert_safe(payload)
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    configured_root = os.environ.get("AGENTHUB_PRODUCTION_EVIDENCE_DIR", "").strip()
    should_write_canonical = bool(configured_root) or mirror_path is None
    canonical: Path | None = None
    if should_write_canonical:
        root = Path(configured_root).resolve() if configured_root else DEFAULT_ROOT
        allowed_root = DEFAULT_ROOT.resolve()
        try:
            root.relative_to(allowed_root)
        except ValueError as exc:
            raise ValueError("production evidence root must be inside artifacts/production") from exc
        canonical_root = root / scope
        canonical_root.mkdir(parents=True, exist_ok=True)
        canonical = canonical_root / f"{payload['runId']}.json"
        canonical.write_text(rendered, encoding="utf-8")
    if mirror_path:
        mirror = Path(mirror_path)
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_text(rendered, encoding="utf-8")
    return canonical


def _commit_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = result.stdout.strip()
    return value if len(value) == 40 else "unknown"


def _assert_safe(value: Any, *, key: str = "") -> None:
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            normalized = str(child_key).replace("-", "_").lower()
            if normalized in _FORBIDDEN_KEYS:
                raise ValueError(f"forbidden evidence field: {child_key}")
            _assert_safe(child_value, key=normalized)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_safe(child, key=key)
    elif isinstance(value, str):
        if "authorization:" in value.lower() or "bearer " in value.lower():
            raise ValueError("authorization material is not allowed in evidence")
