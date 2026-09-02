"""PendingConfirmationRepository — persistence for T5 rule confirmation gate."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from app.domain import (
    ActorRef,
    PendingConfirmation,
    PendingConfirmationStatus,
)

Execute = Callable[..., Awaitable[None]]
FetchOne = Callable[..., Awaitable[dict[str, Any] | None]]
FetchAll = Callable[..., Awaitable[list[dict[str, Any]]]]


def _encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _decode_json(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(value, Mapping):
        return {}
    return dict(value)


def _decode_actor(row: Mapping[str, Any]) -> ActorRef:
    return ActorRef(
        type=str(row["created_by_type"]),
        id=str(row["created_by_id"]),
        display_name=str(row.get("created_by_display_name") or ""),
    )


def _to_dt(value: Any) -> datetime:
    if isinstance(value, str):
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _pending_from_row(row: Mapping[str, Any]) -> PendingConfirmation:
    expires_at = _to_dt(row["expires_at"])
    created_at = _to_dt(row["created_at"])
    resolved_at_raw = row.get("resolved_at")
    resolved_at = _to_dt(resolved_at_raw) if resolved_at_raw is not None else None

    return PendingConfirmation(
        id=str(row["id"]),
        session_id=row.get("session_id"),
        workspace_id=str(row["workspace_id"]),
        rule_id=str(row["rule_id"]),
        rule_description=str(row.get("rule_description") or ""),
        action_kind=str(row["action_kind"]),
        target_agent=row.get("target_agent"),
        objective_template=row.get("objective_template"),
        message=str(row["message"]),
        request_payload=_decode_json(row.get("request_payload")),
        status=PendingConfirmationStatus(str(row["status"])),
        created_by=_decode_actor(row),
        expires_at=expires_at,
        created_at=created_at,
        resolved_at=resolved_at,
    )


class PendingConfirmationRepository:
    """Persistence adapter for rule-trigger confirmation records."""

    def __init__(
        self,
        *,
        execute: Execute | None = None,
        fetch_one: FetchOne | None = None,
        fetch_all: FetchAll | None = None,
    ) -> None:
        if execute is None or fetch_one is None or fetch_all is None:
            from app.db.session import aexecute, afetch_all, afetch_one

            execute = execute or aexecute
            fetch_one = fetch_one or afetch_one
            fetch_all = fetch_all or afetch_all
        self._execute = execute
        self._fetch_one = fetch_one
        self._fetch_all = fetch_all

    # ── mutations ──────────────────────────────────────────────────

    async def add_pending(self, pending: PendingConfirmation) -> None:
        await self._execute(
            """INSERT INTO pending_confirmations(
                   id, session_id, workspace_id, rule_id, rule_description,
                   action_kind, target_agent, objective_template, message,
                   request_payload, status,
                   created_by_type, created_by_id, created_by_display_name,
                   expires_at, created_at, resolved_at
               ) VALUES($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                        $12, $13, $14, $15, $16, $17)""",
            pending.id,
            pending.session_id,
            pending.workspace_id,
            pending.rule_id,
            pending.rule_description,
            pending.action_kind,
            pending.target_agent,
            pending.objective_template,
            pending.message,
            _encode_json(pending.request_payload),
            pending.status.value,
            pending.created_by.type,
            pending.created_by.id,
            pending.created_by.display_name or "",
            pending.expires_at,
            pending.created_at,
            pending.resolved_at,
        )

    async def resolve_pending(
        self,
        pending_id: str,
        status: PendingConfirmationStatus,
    ) -> PendingConfirmation | None:
        """Transition a pending record to CONFIRMED, CANCELLED, or EXPIRED."""
        row = await self._fetch_one(
            "SELECT * FROM pending_confirmations WHERE id=$1",
            pending_id,
        )
        if row is None:
            return None

        now = datetime.now(timezone.utc)
        await self._execute(
            """UPDATE pending_confirmations
               SET status=$1, resolved_at=$2
               WHERE id=$3""",
            status.value,
            now,
            pending_id,
        )
        row = dict(row)
        row["status"] = status.value
        row["resolved_at"] = now
        return _pending_from_row(row)

    # ── queries ────────────────────────────────────────────────────

    async def get_pending(self, pending_id: str) -> PendingConfirmation | None:
        row = await self._fetch_one(
            "SELECT * FROM pending_confirmations WHERE id=$1",
            pending_id,
        )
        return _pending_from_row(row) if row is not None else None

    async def list_pending(
        self,
        workspace_id: str,
        *,
        status: PendingConfirmationStatus | None = None,
        limit: int = 50,
    ) -> list[PendingConfirmation]:
        """List pending confirmations for a workspace."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if status is None:
            rows = await self._fetch_all(
                """SELECT * FROM pending_confirmations
                   WHERE workspace_id=$1
                   ORDER BY created_at DESC
                   LIMIT $2""",
                workspace_id,
                limit,
            )
        else:
            rows = await self._fetch_all(
                """SELECT * FROM pending_confirmations
                   WHERE workspace_id=$1 AND status=$2
                   ORDER BY created_at DESC
                   LIMIT $3""",
                workspace_id,
                status.value,
                limit,
            )
        return [_pending_from_row(row) for row in rows]
