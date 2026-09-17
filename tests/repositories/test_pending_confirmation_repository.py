"""Tests for T5 PendingConfirmationRepository — in-memory fake, no live DB."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.domain import (
    ActorRef,
    PendingConfirmation,
    PendingConfirmationStatus,
)
from app.repositories.pending_confirmation_repository import (
    PendingConfirmationRepository,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_pending(
    rule_id: str = "auto-deploy",
    workspace_id: str = "ws-test",
) -> PendingConfirmation:
    now = _now()
    return PendingConfirmation(
        id=f"pc-{rule_id}",
        session_id="sess-test",
        workspace_id=workspace_id,
        rule_id=rule_id,
        rule_description=f"Rule {rule_id}",
        action_kind="create_mission",
        target_agent="devops",
        objective_template=f"Deploy triggered by {{rule.id}}",
        message="please deploy now",
        request_payload={"workspace_id": workspace_id},
        status=PendingConfirmationStatus.PENDING,
        created_by=ActorRef(type="human", id="user-1", display_name="Test"),
        expires_at=now + timedelta(minutes=15),
        created_at=now,
    )


def _make_fake_repo():
    store: dict[str, dict[str, Any]] = {}

    async def _execute(sql, *args):
        if "INSERT INTO pending_confirmations" in sql:
            store[args[0]] = {
                "id": args[0],
                "session_id": args[1],
                "workspace_id": args[2],
                "rule_id": args[3],
                "rule_description": args[4],
                "action_kind": args[5],
                "target_agent": args[6],
                "objective_template": args[7],
                "message": args[8],
                "request_payload": args[9],
                "status": args[10],
                "created_by_type": "human",
                "created_by_id": args[12],
                "created_by_display_name": args[13] or "",
                "expires_at": args[14],
                "created_at": args[15],
                "resolved_at": args[16],
            }
        elif "UPDATE pending_confirmations" in sql:
            pending_id = args[2]
            if pending_id in store:
                store[pending_id]["status"] = args[0]
                store[pending_id]["resolved_at"] = args[1]

    async def _fetch_one(sql, *args):
        if "WHERE id=$1" in sql:
            row = store.get(args[0])
            return dict(row) if row else None
        return None

    async def _fetch_all(sql, *args):
        if "workspace_id=$1" in sql and "ORDER BY" in sql:
            ws_id = args[0]
            rows = [
                dict(r) for r in store.values()
                if r["workspace_id"] == ws_id
            ]
            if len(args) > 1 and args[1] in (
                s.value for s in PendingConfirmationStatus
            ):
                rows = [r for r in rows if r["status"] == args[1]]
            return rows
        return []

    repo = PendingConfirmationRepository(
        execute=_execute, fetch_one=_fetch_one, fetch_all=_fetch_all,
    )
    return repo, store


# ── Repository tests ─────────────────────────────────────────────


class TestPendingConfirmationRepository:
    def test_add_then_get(self):
        repo, store = _make_fake_repo()
        p = _make_pending()
        asyncio.run(repo.add_pending(p))

        assert p.id in store

        got = asyncio.run(repo.get_pending(p.id))
        assert got is not None
        assert got.id == p.id
        assert got.rule_id == "auto-deploy"
        assert got.status == PendingConfirmationStatus.PENDING
        assert got.created_by.id == "user-1"

    def test_get_missing_returns_none(self):
        repo, _ = _make_fake_repo()
        got = asyncio.run(repo.get_pending("pc-nonexistent"))
        assert got is None

    def test_resolve_to_confirmed(self):
        repo, store = _make_fake_repo()
        p = _make_pending()
        asyncio.run(repo.add_pending(p))

        resolved = asyncio.run(repo.resolve_pending(p.id, PendingConfirmationStatus.CONFIRMED))
        assert resolved is not None
        assert resolved.status == PendingConfirmationStatus.CONFIRMED
        assert resolved.resolved_at is not None
        assert store[p.id]["status"] == "CONFIRMED"

    def test_resolve_to_cancelled(self):
        repo, _ = _make_fake_repo()
        p = _make_pending()
        asyncio.run(repo.add_pending(p))

        resolved = asyncio.run(repo.resolve_pending(p.id, PendingConfirmationStatus.CANCELLED))
        assert resolved.status == PendingConfirmationStatus.CANCELLED

    def test_resolve_missing_returns_none(self):
        repo, _ = _make_fake_repo()
        result = asyncio.run(repo.resolve_pending("no-id", PendingConfirmationStatus.CANCELLED))
        assert result is None

    def test_list_by_workspace(self):
        repo, _ = _make_fake_repo()
        asyncio.run(repo.add_pending(_make_pending("rule-a")))
        asyncio.run(repo.add_pending(_make_pending("rule-b")))
        asyncio.run(repo.add_pending(_make_pending("other", workspace_id="ws-other")))

        listed = asyncio.run(repo.list_pending("ws-test"))
        assert len(listed) == 2
        assert {p.rule_id for p in listed} == {"rule-a", "rule-b"}

    def test_list_filtered_by_status(self):
        repo, _ = _make_fake_repo()
        asyncio.run(repo.add_pending(_make_pending("r1")))
        asyncio.run(repo.add_pending(_make_pending("r2")))
        asyncio.run(repo.resolve_pending("pc-r1", PendingConfirmationStatus.CANCELLED))

        pending = asyncio.run(repo.list_pending("ws-test", status=PendingConfirmationStatus.PENDING))
        cancelled = asyncio.run(repo.list_pending("ws-test", status=PendingConfirmationStatus.CANCELLED))
        assert len(pending) == 1
        assert pending[0].rule_id == "r2"
        assert len(cancelled) == 1
        assert cancelled[0].rule_id == "r1"

    def test_list_limit_rejected(self):
        repo, _ = _make_fake_repo()
        with pytest.raises(ValueError):
            asyncio.run(repo.list_pending("ws-test", limit=0))
        with pytest.raises(ValueError):
            asyncio.run(repo.list_pending("ws-test", limit=1000))


# ── Domain model tests ────────────────────────────────────────────


class TestPendingConfirmationModel:
    def test_default_status_is_pending(self):
        p = _make_pending()
        assert p.status == PendingConfirmationStatus.PENDING
        assert p.resolved_at is None
        assert p.request_payload == {"workspace_id": "ws-test"}

    def test_payload_flexible(self):
        now = _now()
        p = PendingConfirmation(
            id="pc-flex",
            workspace_id="ws",
            rule_id="r",
            rule_description="test",
            action_kind="create_mission",
            message="x",
            request_payload={"a": 1, "b": True},
            created_by=ActorRef(type="human", id="u"),
            expires_at=now + timedelta(minutes=5),
            created_at=now,
        )
        assert p.request_payload["a"] == 1
        assert p.request_payload["b"] is True
        assert p.target_agent is None  # optional field default
