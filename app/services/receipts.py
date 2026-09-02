"""Receipts search service (ADR-0108 — P1 archivist integration).

Thin service wrapper around the pure query/summary helpers in
``app.cli.receipts``, adapted for in-process use by ``chat_mission``.

The CLI ``search_receipts`` launches a MissionControl subprocess and
queries over HTTP — correct for CLI but too heavy for a chat
endpoint that already holds a live ``MissionRepository``.  This module
reuses the same pure helpers (``filter_missions_by_query``,
``summarize_verdicts``, ``build_receipt``) and feeds them live
``Mission`` / ``Evidence`` domain objects so the search path stays
identical regardless of caller (CLI ↔ chat ↔ future rule engine).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.cli.receipts import (
    build_receipt,
    filter_missions_by_query,
    summarize_verdicts,
)
from app.domain.models import Evidence, Mission
from app.repositories import MissionRepository


def _mission_to_dict(mission: Mission) -> dict[str, Any]:
    """Flatten a ``Mission`` domain object for the pure filter helpers."""
    return {
        "id": mission.id,
        "title": mission.title,
        "objective": mission.objective,
        "status": mission.status.value,
        "updated_at": mission.updated_at.isoformat() if mission.updated_at else "",
    }


def _evidence_to_dict(evidence: Evidence) -> dict[str, Any]:
    """Flatten an ``Evidence`` domain object for ``build_receipt``."""
    return {
        "verdict": evidence.verdict.value,
        "summary": evidence.summary,
        "generated_at": evidence.generated_at.isoformat() if evidence.generated_at else "",
    }


async def search_receipts_inprocess(
    repo: MissionRepository,
    *,
    workspace_id: str,
    query: str,
    limit: int = 10,
    days: int | None = None,
) -> list[dict[str, Any]]:
    """Search local mission history and return receipts with evidence.

    Uses the injected ``MissionRepository`` — zero subprocess, zero
    HTTP overhead.  Reuses ``filter_missions_by_query`` and
    ``build_receipt`` from ``app.cli.receipts`` so CLI and chat share
    the exact same ranking / summary logic.
    """
    all_missions = await repo.list_missions(workspace_id=workspace_id, limit=200)
    mission_dicts = [_mission_to_dict(m) for m in all_missions]

    filtered = filter_missions_by_query(
        mission_dicts,
        query,
        days=days,
        now=datetime.now(timezone.utc),
    )

    receipts: list[dict[str, Any]] = []
    for md in filtered[:max(limit, 0)]:
        mission_id = str(md.get("id") or "")
        try:
            ev_rows = await repo.list_evidence(mission_id, limit=50)
        except Exception:  # noqa: BLE001 - evidence failure is non-fatal
            ev_rows = []
        evidence_dicts = [_evidence_to_dict(e) for e in ev_rows]
        receipts.append(build_receipt(md, evidence_dicts))

    return receipts


def format_receipts_as_context(receipts: list[dict[str, Any]], *, query: str) -> str:
    """Render a receipts list as a context block suitable for Mission objective prefix injection.

    The block is plain markdown, designed to be prepended to the chat
    message so the downstream agent sees the evidence trail before
    answering.  Each entry carries mission_id, verdict summary, and
    the evidence snippets that support the summary — exactly the
    provenance chain ADR-0108 requires.
    """
    if not receipts:
        return (
            f"**Receipts query: «{query}»** — No matching historical Missions.\n"
        )

    lines: list[str] = [f"**Receipts query: «{query}»** — {len(receipts)} result(s) found:"]
    for i, r in enumerate(receipts, 1):
        mid = r.get("mission_id") or "?"
        status = r.get("status") or "?"
        title = (r.get("title") or "")[:120]
        verdicts = r.get("verdicts") or "NO-EVIDENCE"
        lines.append(
            f"{i}. **{title}**  \n"
            f"   mission=`{mid}` · status=`{status}` · verdicts=`{verdicts}`"
        )
        for ev in r.get("evidence", [])[:3]:
            verdict = ev.get("verdict", "")
            summary = (ev.get("summary") or "")[:200]
            if summary:
                lines.append(f"   → `{verdict}` {summary}")
    return "\n".join(lines)
