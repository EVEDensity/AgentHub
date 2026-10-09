"""Expiry-bounded operational observations of authenticated Runner polling."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

Execute = Callable[..., Awaitable[Any]]
FetchOne = Callable[..., Awaitable[dict | None]]
FetchAll = Callable[..., Awaitable[list[dict]]]
PRESENCE_SECONDS = 30


class RunnerPresenceRepository:
    """Presence describes matching process contact, never execution success."""

    def __init__(self, *, execute: Execute | None = None, fetch_one: FetchOne | None = None, fetch_all: FetchAll | None = None):
        if execute is None or fetch_one is None or fetch_all is None:
            from app.db.session import aexecute, afetch_all, afetch_one
            execute = execute or aexecute
            fetch_one = fetch_one or afetch_one
            fetch_all = fetch_all or afetch_all
        self._execute = execute
        self._fetch_one = fetch_one
        self._fetch_all = fetch_all

    async def observe_poll(
        self, workspace_id: str, *, runner_id: str, agent_id: str,
        adapter_type: str, supported_work_unit_kinds: tuple[str, ...],
        supported_capabilities: tuple[str, ...] = (),
        observed_at: datetime | None = None,
    ) -> None:
        now = observed_at or datetime.now(UTC)
        if not supported_work_unit_kinds or len(supported_work_unit_kinds) > 32:
            raise ValueError("Runner presence requires explicit bounded WorkUnit kinds")
        if any(not value or len(value) > 255 for value in (
            workspace_id, runner_id, agent_id, adapter_type, *supported_work_unit_kinds,
        )):
            raise ValueError("Runner presence requires bounded identity")
        if len(supported_capabilities) > 256 or any(not cap or len(cap) > 255 for cap in supported_capabilities):
            raise ValueError("Runner capabilities must be bounded")
        capabilities = json.dumps(sorted(set(supported_capabilities)))
        for kind in supported_work_unit_kinds:
            await self._execute(
                """INSERT INTO runner_presence(
                    workspace_id, runner_id, agent_id, adapter_type, work_unit_kind,
                    last_seen_at, expires_at, supported_capabilities
                ) VALUES($1,$2,$3,$4,$5,$6,$7,$8)
                ON CONFLICT(workspace_id,runner_id,agent_id,adapter_type,work_unit_kind)
                DO UPDATE SET last_seen_at=EXCLUDED.last_seen_at, expires_at=EXCLUDED.expires_at,
                    supported_capabilities=EXCLUDED.supported_capabilities""",
                workspace_id, runner_id, agent_id, adapter_type, kind,
                now, now + timedelta(seconds=PRESENCE_SECONDS), capabilities,
            )

    async def matching_observation(
        self, workspace_id: str, *, agent_id: str | None, adapter_type: str | None,
        work_unit_kind: str, now: datetime,
        required_capabilities: tuple[str, ...] = (),
    ) -> dict | None:
        if agent_id is None or adapter_type is None:
            return None
        rows = await self._fetch_all(
            """SELECT last_seen_at, expires_at, supported_capabilities FROM runner_presence
                WHERE workspace_id=$1 AND agent_id=$2 AND adapter_type=$3
                AND work_unit_kind=$4
                ORDER BY last_seen_at DESC, runner_id ASC""",
            workspace_id, agent_id, adapter_type, work_unit_kind,
        )
        for row in rows:
            try:
                seen = _date(row["last_seen_at"])
                expires = _date(row["expires_at"])
                caps = _capabilities(row["supported_capabilities"])
            except (ValueError, TypeError, KeyError):
                continue
            if seen <= now < expires <= seen + timedelta(seconds=PRESENCE_SECONDS) and set(required_capabilities) <= caps:
                return {"last_seen_at": seen, "expires_at": expires, "source": "poll"}
        return await self._matching_lease(
            workspace_id, agent_id, adapter_type, work_unit_kind, required_capabilities, now,
        )

    async def _matching_lease(self, workspace_id, agent_id, adapter_type, kind, required_capabilities, now):
        rows = await self._fetch_all(
            """SELECT w.lease, w.required_capabilities FROM work_units w
                JOIN missions m ON m.id=w.mission_id
                WHERE m.workspace_id=$1 AND m.status='RUNNING'
                AND w.assigned_agent_id=$2 AND w.assigned_adapter=$3 AND w.kind=$4
                AND w.status IN ('LEASED','RUNNING') AND w.lease IS NOT NULL""",
            workspace_id, agent_id, adapter_type, kind,
        )
        for row in rows:
            try:
                lease = json.loads(row["lease"]) if isinstance(row["lease"], str) else row["lease"]
                expires = _date(lease["expiresAt"])
                capabilities = _capabilities(row["required_capabilities"])
                if not lease["id"] or not lease["runnerId"]:
                    continue
            except (ValueError, TypeError, KeyError):
                continue
            if now < expires <= now + timedelta(seconds=3600) and set(required_capabilities) <= capabilities:
                return {"last_seen_at": None, "expires_at": expires, "source": "lease"}
        return None


def _date(value) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Runner observation must have an aware timestamp")
    return value


def _capabilities(value) -> set[str]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or len(value) > 256 or any(not isinstance(cap, str) or not cap or len(cap) > 255 for cap in value):
        raise ValueError("Runner observation capabilities are invalid")
    if len(value) != len(set(value)):
        raise ValueError("Runner capabilities must be unique")
    return set(value)
