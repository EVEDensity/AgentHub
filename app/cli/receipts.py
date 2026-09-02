"""Receipts search and replay slice (ADR-0108 P0).

Cross-session mission search over the Mission/Evidence event log: every
conclusion carries the mission link, the verifier verdicts, and the
evidence summaries it came from — answers cite evidence, they do not
free-associate. This module holds the pure query/summary helpers plus
the `agenthub search` / `agenthub replay` command handlers.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.cli.runtime import (
    CliModelSettings,
    EXIT_INFRA_ERROR,
    EXIT_OK,
    MissionControlClient,
    MissionControlProcess,
    _load_config,
    resolve_model_settings,
    state_dir,
)


def _parse_timestamp(value: Any) -> datetime | None:
    """Best-effort ISO-8601 parse of an API timestamp field."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _field(record: dict[str, Any], *names: str) -> Any:
    """Read the first present field name.

    Tolerates snake_case and camelCase spellings — the v1 API serializes
    with camelCase aliases while some call sites pass plain records.
    """
    for name in names:
        if name in record:
            return record[name]
    return None


def filter_missions_by_query(
    missions: list[dict[str, Any]],
    query: str,
    *,
    status: str | None = None,
    days: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Filter mission records for the receipts search (ADR-0108 P0).

    Pure keyword view over the mission list — no invented history:

    * every whitespace-separated query term must appear (case-insensitive
      substring) in the mission's title or objective;
    * ``status`` filters on the exact mission status when given;
    * ``days`` keeps missions updated within the trailing window.
    """
    terms = [term.lower() for term in query.split() if term]
    if status is not None:
        wanted = status.strip().upper()
        missions = [
            m for m in missions if str(m.get("status") or "").upper() == wanted
        ]
    if days is not None and days >= 0:
        reference = now or datetime.now(timezone.utc)
        kept: list[dict[str, Any]] = []
        for mission in missions:
            updated = _parse_timestamp(
                _field(mission, "updated_at", "updatedAt")
            )
            if updated is None:
                # Unreadable timestamps are kept rather than silently
                # dropped — search must not hide records it cannot date.
                kept.append(mission)
                continue
            age = (reference - updated).total_seconds()
            if age <= days * 86400:
                kept.append(mission)
        missions = kept
    if not terms:
        return list(missions)
    matched = []
    for mission in missions:
        haystack = " ".join(
            (
                str(mission.get("title") or ""),
                str(mission.get("objective") or ""),
            )
        ).lower()
        if all(term in haystack for term in terms):
            matched.append(mission)
    return matched


def summarize_verdicts(evidence: list[dict[str, Any]]) -> str:
    """Compact verdict line for one mission's evidence records."""
    if not evidence:
        return "NO-EVIDENCE"
    counts: dict[str, int] = {}
    for item in evidence:
        verdict = str(item.get("verdict") or "UNKNOWN").upper()
        counts[verdict] = counts.get(verdict, 0) + 1
    return " ".join(
        f"{verdict}x{count}" for verdict, count in sorted(counts.items())
    )


def build_receipt(
    mission: dict[str, Any], evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    """One receipts entry: mission record plus its verifier evidence.

    Every conclusion the search returns carries the mission link, the
    verifier verdicts, and the evidence summaries it came from — answers
    cite evidence, they do not free-associate (ADR-0108).
    """
    return {
        "mission_id": mission.get("id") or "",
        "status": mission.get("status") or "",
        "title": mission.get("title") or "",
        "objective": mission.get("objective") or "",
        "updated_at": _field(mission, "updated_at", "updatedAt") or "",
        "verdicts": summarize_verdicts(evidence),
        "evidence": [
            {
                "verdict": _field(item, "verdict") or "",
                "summary": _field(item, "summary") or "",
                "generated_at": _field(
                    item, "generated_at", "generatedAt"
                )
                or "",
            }
            for item in evidence
        ],
    }


def search_receipts(
    *,
    query: str,
    state_dir: Path,
    workspace_root: Path,
    model: CliModelSettings,
    limit: int = 20,
    status: str | None = None,
    days: int | None = None,
) -> list[dict[str, Any]]:
    """Search local mission history and return receipts with evidence."""
    with MissionControlProcess(
        state_dir=state_dir,
        workspace_root=workspace_root,
        model=model,
    ) as process:
        with MissionControlClient(process.base_url) as client:
            client.login()
            missions = filter_missions_by_query(
                client.missions(), query, status=status, days=days
            )
            receipts = []
            for mission in missions[: max(limit, 0)]:
                try:
                    evidence = client.evidence(str(mission.get("id") or ""))
                except httpx.HTTPError:
                    evidence = []
                receipts.append(build_receipt(mission, evidence))
            return receipts


def get_mission_receipt(
    *,
    mission_id: str,
    state_dir: Path,
    workspace_root: Path,
    model: CliModelSettings,
) -> dict[str, Any] | None:
    """Load one mission with its evidence for `agenthub replay`."""
    with MissionControlProcess(
        state_dir=state_dir,
        workspace_root=workspace_root,
        model=model,
    ) as process:
        with MissionControlClient(process.base_url) as client:
            client.login()
            try:
                mission = client.get_mission(mission_id)
            except httpx.HTTPError:
                return None
            try:
                evidence = client.evidence(mission_id)
            except httpx.HTTPError:
                evidence = []
            try:
                artifacts = client.artifacts(mission_id)
            except httpx.HTTPError:
                artifacts = []
            receipt = build_receipt(mission, evidence)
            receipt["artifacts"] = [
                {
                    "id": item.get("id") or "",
                    "content_address": _field(
                        item, "contentAddress", "content_address"
                    )
                    or "",
                }
                for item in artifacts
            ]
            return receipt


def _print_receipt(receipt: dict[str, Any]) -> None:
    mission_id = str(receipt.get("mission_id") or "")[:38]
    status = str(receipt.get("status") or "")[:12]
    verdicts = str(receipt.get("verdicts") or "")[:16]
    objective = str(receipt.get("objective") or "").splitlines()
    first_line = (objective[0] if objective else "")[:60]
    print(f"{mission_id:40} {status:12} {verdicts:16} {first_line}")
    for item in receipt.get("evidence") or []:
        verdict = str(item.get("verdict") or "")[:12]
        summary = " ".join(str(item.get("summary") or "").split())[:100]
        if summary:
            print(f"{'':40} evidence[{verdict}]: {summary}")
    artifacts = receipt.get("artifacts") or []
    if artifacts:
        addresses = [str(a.get("content_address") or "") for a in artifacts]
        print(f"{'':40} artifacts: {', '.join(addresses)}")


# ═══════════════════════════════════════════════════════════════════════
# T1-4: Session messages search (cross-domain search, ADR-0108 P3)
# ═══════════════════════════════════════════════════════════════════════


def filter_messages_by_query(
    messages: list[dict[str, Any]],
    query: str,
    *,
    days: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Filter session message records by keyword (T1-4 cross-domain search).

    Pure keyword view — mirrors ``filter_missions_by_query`` logic so
    mission and session search share identical ranking semantics.
    Every whitespace-separated term must appear (case-insensitive
    substring) in the message's content or sender.
    """
    terms = [term.lower() for term in query.split() if term]
    if days is not None and days >= 0:
        reference = now or datetime.now(timezone.utc)
        kept: list[dict[str, Any]] = []
        for msg in messages:
            created = _parse_timestamp(
                _field(msg, "created_at", "createdAt")
            )
            if created is None:
                kept.append(msg)
                continue
            age = (reference - created).total_seconds()
            if age <= days * 86400:
                kept.append(msg)
        messages = kept
    if not terms:
        return list(messages)
    matched = []
    for msg in messages:
        haystack = " ".join(
            (
                str(msg.get("content") or ""),
                str(msg.get("sender") or ""),
            )
        ).lower()
        if all(term in haystack for term in terms):
            matched.append(msg)
    return matched


def format_message_hit(msg: dict[str, Any], *, include_session: bool = True) -> str:
    """Format one message search hit as a readable string."""
    sender = str(msg.get("sender") or "?")
    content = str(msg.get("content") or "")
    snippet = content[:120] + ("…" if len(content) > 120 else "")
    session_info = ""
    if include_session:
        session_id = str(msg.get("session_id") or msg.get("sessionId") or "")
        session_info = f" session={session_id[:16]}"
    created = str(msg.get("created_at") or msg.get("createdAt") or "")
    return f"[{sender}{session_info} {created}] {snippet}"


def search_messages_sqlite(
    *,
    db_path: Path,
    query: str,
    limit: int = 20,
    days: int | None = None,
) -> list[dict[str, Any]]:
    """Search session messages directly in the local SQLite DB (T1-4).

    Read-only path — zero subprocess, zero HTTP overhead.  Used by
    ``agenthub search --scope session``.  The ``messages`` table already
    exists in the core schema (init_db.py).
    """
    if not db_path.is_file():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "SELECT id, session_id, sender, content, type, created_at FROM messages ORDER BY created_at DESC"
        )
        rows = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

    filtered = filter_messages_by_query(rows, query, days=days)
    return filtered[: max(limit, 0)]


# ═══════════════════════════════════════════════════════════════════════
# T6: Session events search (cross-domain search for event stream)
# ═══════════════════════════════════════════════════════════════════════


def filter_events_by_query(
    events: list[dict[str, Any]],
    query: str,
    *,
    days: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Filter session event rows by keyword (T6 cross-domain search).

    Case-insensitive substring match across payload JSON text + event_type.
    Mirrors ``filter_messages_by_query`` ranking semantics so mission,
    session, and events search behave consistently.
    """
    terms = [term.lower() for term in query.split() if term]
    if days is not None and days >= 0:
        reference = now or datetime.now(timezone.utc)
        kept: list[dict[str, Any]] = []
        for evt in events:
            created = _parse_timestamp(evt.get("created_at"))
            if created is None:
                kept.append(evt)
                continue
            age = (reference - created).total_seconds()
            if age <= days * 86400:
                kept.append(evt)
        events = kept
    if not terms:
        return list(events)
    matched = []
    for evt in events:
        haystack = " ".join(
            (
                str(evt.get("payload") or ""),
                str(evt.get("event_type") or ""),
            )
        ).lower()
        if all(term in haystack for term in terms):
            matched.append(evt)
    return matched


def format_event_hit(evt: dict[str, Any]) -> str:
    """Format one session event search hit as a readable string."""
    event_type = str(evt.get("event_type") or "?")
    session_id = str(evt.get("session_id") or "")
    created = str(evt.get("created_at") or "")
    payload_str = str(evt.get("payload") or "")
    # Try to make the payload more readable
    try:
        payload_obj = json.loads(payload_str) if payload_str.startswith("{") else {}
        snippet = json.dumps(payload_obj, ensure_ascii=False)[:100]
    except (json.JSONDecodeError, TypeError):
        snippet = payload_str[:100]
    return f"[{event_type} session={session_id[:16]} {created}] {snippet}"


def search_events_sqlite(
    *,
    db_path: Path,
    query: str,
    limit: int = 20,
    days: int | None = None,
) -> list[dict[str, Any]]:
    """Search session_events directly in the local SQLite DB (T6).

    Read-only path — zero subprocess, zero HTTP overhead.  Used by
    ``agenthub search --scope events`` and ``--scope both``.
    Falls back to in-Python filtering (LIKE would need FTS5 for
    acceptable performance on large payloads).
    """
    if not db_path.is_file():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "SELECT id, session_id, event_type, actor_type, actor_id, "
            "actor_display_name, payload, created_at "
            "FROM session_events ORDER BY created_at DESC"
        )
        rows = [dict(row) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        # Table may not exist in older DBs
        return []
    finally:
        conn.close()

    filtered = filter_events_by_query(rows, query, days=days)
    return filtered[: max(limit, 0)]


def cmd_search(args: argparse.Namespace, cwd: Path) -> int:
    """Cross-domain search (T1-4 + T6).

    ``--scope mission`` (default) searches Mission/Evidence receipts via
    the MissionControl HTTP path.  ``--scope session`` searches session
    messages directly in SQLite.  ``--scope events`` searches the
    session_events event stream.  ``--scope both`` merges mission + session.
    ``--scope all`` merges all three result sets.
    """
    config = _load_config(cwd)
    settings = resolve_model_settings(
        provider=args.provider,
        model=args.model,
        base_url=args.model_base_url,
        config=config,
    )
    workspace_root = Path(args.workspace).resolve() if args.workspace else cwd
    directory = state_dir(cwd)
    db_path = directory / "db" / "agenthub.db"

    scope = getattr(args, "scope", "mission") or "mission"
    scope = scope.lower()
    if scope not in ("mission", "session", "events", "both", "all"):
        print(
            f"error: unknown scope {scope!r} — use mission, session, "
            f"events, both, or all",
            file=sys.stderr,
        )
        return EXIT_INFRA_ERROR

    # both = mission + session; all = mission + session + events
    scope_mission = scope in ("mission", "both", "all")
    scope_session = scope in ("session", "both", "all")
    scope_events = scope in ("events", "all")

    # ── Mission scope ──────────────────────────────────────────────
    receipts: list[dict[str, Any]] = []
    if scope_mission:
        if not db_path.is_file():
            print("no local missions yet — run `agenthub run` first")
            return EXIT_OK
        try:
            receipts = search_receipts(
                query=args.query,
                state_dir=directory,
                workspace_root=workspace_root,
                model=settings,
                limit=args.limit,
                status=args.status,
                days=args.days,
            )
        except (RuntimeError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_INFRA_ERROR

    # ── Session scope ──────────────────────────────────────────────
    messages: list[dict[str, Any]] = []
    if scope_session and db_path.is_file():
        messages = search_messages_sqlite(
            db_path=db_path,
            query=args.query,
            limit=args.limit,
            days=args.days,
        )

    # ── Events scope (T6) ──────────────────────────────────────────
    events: list[dict[str, Any]] = []
    if scope_events and db_path.is_file():
        events = search_events_sqlite(
            db_path=db_path,
            query=args.query,
            limit=args.limit,
            days=args.days,
        )

    # ── JSON output ────────────────────────────────────────────────
    if args.json:
        output: dict[str, Any] = {}
        if scope_mission:
            output["mission"] = receipts
        if scope_session:
            output["session"] = messages
        if scope_events:
            output["events"] = events
        # Single-scope shortcut: emit the list directly
        if len(output) == 1:
            print(json.dumps(next(iter(output.values())), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(output, ensure_ascii=False, indent=2))
        return EXIT_OK

    # ── Text output ────────────────────────────────────────────────
    has_results = bool(receipts or messages or events)
    if not has_results:
        print(f"no results match: {args.query!r}")
        return EXIT_OK

    if scope_mission and receipts:
        print(f"=== MISSION RECEIPTS ({len(receipts)}) ===")
        print(f"{'MISSION ID':40} {'STATUS':12} {'VERDICTS':16} OBJECTIVE")
        print("-" * 100)
        for receipt in receipts:
            _print_receipt(receipt)
        print()
        print("replay with: agenthub replay <MISSION_ID>")
        print('resume with: agenthub run "<objective>" --resume <MISSION_ID>')
        if scope_session or scope_events:
            print()

    if scope_session and messages:
        print(f"=== SESSION MESSAGES ({len(messages)}) ===")
        print("-" * 80)
        for msg in messages:
            print(format_message_hit(msg))
        if scope_events:
            print()

    if scope_events and events:
        print(f"=== SESSION EVENTS ({len(events)}) ===")
        print("-" * 80)
        for evt in events:
            print(format_event_hit(evt))

    return EXIT_OK


def cmd_replay(args: argparse.Namespace, cwd: Path) -> int:
    config = _load_config(cwd)
    settings = resolve_model_settings(
        provider=args.provider,
        model=args.model,
        base_url=args.model_base_url,
        config=config,
    )
    workspace_root = Path(args.workspace).resolve() if args.workspace else cwd
    directory = state_dir(cwd)
    if not (directory / "db" / "agenthub.db").is_file():
        print("no local missions yet — run `agenthub run` first")
        return EXIT_OK
    try:
        receipt = get_mission_receipt(
            mission_id=args.mission_id,
            state_dir=directory,
            workspace_root=workspace_root,
            model=settings,
        )
    except (RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    if receipt is None:
        print(f"mission not found: {args.mission_id}")
        return EXIT_INFRA_ERROR
    if args.json:
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return EXIT_OK
    print(f"mission : {receipt.get('mission_id')}")
    print(f"status  : {receipt.get('status')}")
    print(f"updated : {receipt.get('updated_at')}")
    print(f"verdicts: {receipt.get('verdicts')}")
    objective = str(receipt.get("objective") or "").strip()
    if objective:
        print("objective:")
        for line in objective.splitlines():
            print(f"  {line}")
    for item in receipt.get("evidence") or []:
        verdict = str(item.get("verdict") or "")[:12]
        summary = " ".join(str(item.get("summary") or "").split())[:200]
        print(f"evidence[{verdict}]: {summary}")
    for artifact in receipt.get("artifacts") or []:
        print(
            f"artifact: {artifact.get('id')} "
            f"{artifact.get('content_address')}"
        )
    print()
    print('resume with: agenthub run "<objective>" '
          f"--resume {args.mission_id}")
    return EXIT_OK
