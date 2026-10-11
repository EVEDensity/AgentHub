"""PendingConfirmationRepository — persistence for T5 rule confirmation gate."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.domain import (
    ActorRef,
    PendingConfirmation,
    PendingConfirmationStatus,
)

Execute = Callable[..., Awaitable[None]]
FetchOne = Callable[..., Awaitable[dict[str, Any] | None]]
FetchAll = Callable[..., Awaitable[list[dict[str, Any]]]]
TransactionFactory = Callable[..., Any]


@dataclass(frozen=True)
class ConfirmationTransaction:
    """Companion repositories using exactly one already-open transaction."""

    pendings: PendingConfirmationRepository
    missions: Any
    session_events: Any
    sessions: Any


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
        value = value.replace(tzinfo=UTC)
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
        transaction_factory: TransactionFactory | None = None,
    ) -> None:
        if execute is None or fetch_one is None or fetch_all is None:
            from app.db.session import aexecute, afetch_all, afetch_one

            execute = execute or aexecute
            fetch_one = fetch_one or afetch_one
            fetch_all = fetch_all or afetch_all
        self._execute = execute
        self._fetch_one = fetch_one
        self._fetch_all = fetch_all
        self._transaction_factory = transaction_factory

    @classmethod
    def from_connection(cls, connection: Any) -> PendingConfirmationRepository:
        return cls(execute=connection.execute, fetch_one=connection.fetchrow, fetch_all=connection.fetch)

    @asynccontextmanager
    async def transaction(self):
        """Hold consumption, Mission admission, and receipts in one transaction."""
        from app.repositories.mission_repository import MissionRepository
        from app.repositories.session_event_repository import SessionEventRepository
        from app.repositories.session_repository import SessionRepository

        factory = self._transaction_factory
        if factory is None:
            from app.db.session import atransaction
            factory = atransaction
        async with factory() as connection:
            @asynccontextmanager
            async def same_connection():
                # Mission services open their own scoped repository contexts.
                # Reuse this outer transaction without independent commits.
                yield connection

            arguments = {"execute": connection.execute, "fetch_one": connection.fetchrow, "fetch_all": connection.fetch}
            yield ConfirmationTransaction(
                pendings=self.from_connection(connection),
                missions=MissionRepository(**arguments, transaction_factory=same_connection),
                session_events=SessionEventRepository(**arguments),
                sessions=SessionRepository(**arguments),
            )

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
        """Consume PENDING once; expired records cannot confirm or cancel.

        The returned row proves this command won the database compare-and-set.
        None means missing, already consumed, or outside the expiry window.
        Mission dispatch must run in :meth:`transaction` after a winning CAS.
        """
        if status == PendingConfirmationStatus.PENDING:
            raise ValueError("resolution must be a terminal confirmation status")
        now = datetime.now(UTC)
        expiry = "expires_at <= $2" if status == PendingConfirmationStatus.EXPIRED else "expires_at > $2"
        row = await self._fetch_one(
            "UPDATE pending_confirmations SET status=$1, resolved_at=$2 "
            f"WHERE id=$3 AND status='PENDING' AND {expiry} RETURNING *",
            status.value,
            now,
            pending_id,
        )
        return _pending_from_row(row) if row is not None else None

    # ── queries ────────────────────────────────────────────────────

    async def get_pending(self, pending_id: str) -> PendingConfirmation | None:
        row = await self._fetch_one(
            "SELECT * FROM pending_confirmations WHERE id=$1",
            pending_id,
        )
        return _pending_from_row(row) if row is not None else None

    async def get_pending_for_update(self, pending_id: str) -> PendingConfirmation | None:
        """Lock the durable record before authorizing and consuming it."""
        row = await self._fetch_one("SELECT * FROM pending_confirmations WHERE id=$1 FOR UPDATE", pending_id)
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
