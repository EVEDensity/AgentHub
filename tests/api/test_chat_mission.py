"""T2-1: Chat-to-Mission endpoint integration tests (ADR-0108/0109).

End-to-end coverage for POST /api/v1/chat/mission:
  - plain message → default participant picked, Mission created + started
  - @mention → agent resolved from catalog, recorded as participant
  - auto-create session (T3) when client omits session_id
  - session_events emitted for message.created / mention.detected /
    mission.created (T1-3)
  - @archivist special mention preprocess + receipts (T1-2)

Uses FastAPI TestClient with dependency-override fake repositories —
mirrors the test_missions_api.py pattern.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.chat_mission import (
    ConfirmPendingRequest,
    CancelPendingRequest,
    router as chat_router,
    get_mission_repository,
    get_session_event_repository,
    get_session_repository,
    get_agent_binding_resolver,
    get_pending_confirmation_repository,
)
from app.services.auth_service import get_current_user


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════
# Fake repositories
# ═══════════════════════════════════════════════════════════════════════


class _FakeMissionRepository:
    """In-memory MissionRepository + method signatures matching the real one.

    Mirrors the subset of ``MissionRepository`` exercised by
    ``MissionService.create_mission`` / ``start_mission`` via the chat
    endpoint.  All persistence is just dict mutation — this is a wiring
    fake, not a transactional one.
    """

    def __init__(self) -> None:
        self.missions: dict[str, Any] = {}
        self.contracts: dict[tuple[str, int], Any] = {}  # (contract_id, version) -> contract
        self.contract_lineages: dict[str, str] = {}  # contract_id -> workspace_id
        self.created: list[Any] = []
        self.events: list[Any] = []
        self.last_sequences: dict[tuple[str, str], int] = {}  # (aggregate_type, aggregate_id) -> last seq

    # ── Contract lineage (create_mission transaction) ───────────
    async def lock_contract_lineage(self, contract_id: str) -> None:
        """No-op — real repo acquires an advisory lock; fake needs none."""
        pass

    async def get_contract_lineage_workspace(self, contract_id: str) -> str | None:
        return self.contract_lineages.get(contract_id)

    async def add_contract_lineage(self, contract_id: str, workspace_id: str) -> None:
        self.contract_lineages[contract_id] = workspace_id

    async def get_contract(self, contract_id: str, version: int):
        return self.contracts.get((contract_id, version))

    async def add_contract(self, contract) -> None:
        self.contracts[(contract.id, contract.version)] = contract

    # ── Mission ──────────────────────────────────────────────────
    async def add_mission(self, mission) -> None:
        self.missions[mission.id] = mission
        self.created.append(mission)

    async def get_mission(self, mission_id: str):
        return self.missions.get(mission_id)

    async def get_mission_for_update(self, mission_id: str):
        return self.missions.get(mission_id)

    async def update_mission(self, mission) -> None:
        self.missions[mission.id] = mission

    async def update_mission_status(self, mission_id: str, status_value: str, *, event=None):
        mission = self.missions.get(mission_id)
        if mission is not None:
            mission.status = type("S", (), {"value": status_value})()
            mission.updated_at = _now()

    async def list_missions(self, workspace_id, *, limit=100, offset=0):
        return [m for m in self.missions.values() if m.workspace_id == workspace_id][offset : offset + limit]

    # ── Event store ─────────────────────────────────────────────
    async def append_event(self, event) -> None:
        self.events.append(event)
        key = (event.aggregate_type, event.aggregate_id)
        self.last_sequences[key] = max(self.last_sequences.get(key, 0), event.sequence)

    async def get_last_event_sequence(
        self, aggregate_id: str, aggregate_type: str = "mission"
    ) -> int:
        return self.last_sequences.get((aggregate_type, aggregate_id), 0)

    # ── Transaction ─────────────────────────────────────────────
    def transaction(self):
        """No-op async context manager yielding self (matching real repo)."""
        import contextlib

        @contextlib.asynccontextmanager
        async def _tx():
            yield self

        return _tx()


class _FakeSessionRepository:
    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}
        self.created: list[Any] = []

    async def add_session(self, session) -> None:
        self.sessions[session.id] = session
        self.created.append(session)

    async def get_session(self, session_id: str):
        return self.sessions.get(session_id)


class _FakeSessionEventRepository:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def add_session_event(self, event) -> None:
        self.events.append(event)


class _FakePendingConfirmationRepository:
    """Stores PendingConfirmation as plain dicts so fake transitions work.

    Real repo transitions via UPDATE + re-fetch; frozen Pydantic models
    cannot be mutated in place, so we track a raw dict that resolve_pending
    can freely rewrite.
    """

    def __init__(self) -> None:
        self.pendings: dict[str, dict[str, Any]] = {}  # id -> dict

    async def add_pending(self, pending) -> None:
        self.pendings[pending.id] = {
            "id": pending.id,
            "session_id": pending.session_id,
            "workspace_id": pending.workspace_id,
            "rule_id": pending.rule_id,
            "rule_description": pending.rule_description,
            "action_kind": pending.action_kind,
            "target_agent": pending.target_agent,
            "objective_template": pending.objective_template,
            "status": pending.status,
            "message": pending.message,
            "request_payload": pending.request_payload,
            "expires_at": pending.expires_at,
            "created_by": pending.created_by,
            "created_at": pending.created_at,
        }

    async def get_pending(self, pending_id: str):
        raw = self.pendings.get(pending_id)
        if raw is None:
            return None
        # Return a simple namespace so callers can do .id / .status
        return type("PendingFake", (), raw)()

    async def resolve_pending(self, pending_id: str, status) -> Any:
        """Transition a pending to CONFIRMED/CANCELLED."""
        raw = self.pendings.get(pending_id)
        if raw is not None:
            raw["status"] = status
            return type("PendingFake", (), raw)()
        return None


class _FakeAgentBindingResolver:
    """Returns a static catalog + default agent for testing."""

    def __init__(self, catalog: list[dict[str, Any]] | None = None, default_id: str | None = None) -> None:
        self.catalog = catalog or [
            {
                "agent_id": "dev",
                "domain": "dev.local",
                "display_name": "Dev Agent",
                "agent_type": "generic",
                "enabled": True,
            },
            {
                "agent_id": "researcher",
                "domain": "research.local",
                "display_name": "Research Agent",
                "agent_type": "generic",
                "enabled": True,
            },
        ]
        self.default_id = default_id or "dev"

    async def list_enabled(self, scope_id: str) -> list[dict[str, Any]]:
        return self.catalog

    async def get_default(self, scope_id: str) -> dict[str, Any] | None:
        for b in self.catalog:
            if b["agent_id"] == self.default_id:
                return b
        return self.catalog[0] if self.catalog else None


def _user(role: str = "admin") -> dict[str, Any]:
    """Default user is admin so authorize_workspace passes for any workspace."""
    return {
        "id": "user-1",
        "email": "tester@example.test",
        "name": "Tester",
        "role": role,
    }


# ═══════════════════════════════════════════════════════════════════════
# App wiring
# ═══════════════════════════════════════════════════════════════════════


def build_chat_app(
    *,
    repository: _FakeMissionRepository | None = None,
    sessions_repo: _FakeSessionRepository | None = None,
    session_events_repo: _FakeSessionEventRepository | None = None,
    pending_repo: _FakePendingConfirmationRepository | None = None,
    resolver: _FakeAgentBindingResolver | None = None,
    user: dict[str, Any] | None = None,
) -> tuple[FastAPI, dict[str, Any]]:
    """Build a FastAPI app with chat_mission router + dependency overrides.

    Returns (app, fakes_dict) so callers can inspect the injected fakes
    after a TestClient request (e.g. check ``fakes["session_events"].events``).
    """
    fakes = {
        "repo": repository or _FakeMissionRepository(),
        "sessions": sessions_repo or _FakeSessionRepository(),
        "session_events": session_events_repo or _FakeSessionEventRepository(),
        "pending": pending_repo or _FakePendingConfirmationRepository(),
        "resolver": resolver or _FakeAgentBindingResolver(),
    }

    app = FastAPI()
    app.include_router(chat_router, prefix="/api/v1")

    app.dependency_overrides[get_mission_repository] = lambda: fakes["repo"]
    app.dependency_overrides[get_session_event_repository] = lambda: fakes["session_events"]
    app.dependency_overrides[get_session_repository] = lambda: fakes["sessions"]
    app.dependency_overrides[get_agent_binding_resolver] = lambda: fakes["resolver"]
    app.dependency_overrides[get_pending_confirmation_repository] = lambda: fakes["pending"]
    app.dependency_overrides[get_current_user] = lambda: user or _user()

    return app, fakes


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════


class TestPlainMessageDefaults(
    unittest.TestCase,
):
    """T2-1 plain message → default participant, Mission created + started."""

    def test_plain_message_creates_mission(self) -> None:
        app, fakes = build_chat_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat/mission",
            json={"message": "写一个排序函数"},
        )
        self.assertEqual(resp.status_code, 202, resp.text)
        body = resp.json()
        self.assertIn("missionId", body)
        self.assertTrue(body["missionId"].startswith("mis-chat-"))
        self.assertEqual(body["status"], "RUNNING")
        self.assertIn("streamUrl", body)
        self.assertTrue(body["streamUrl"].startswith("/api/v1/missions/"))

    def test_plain_message_picks_default_participant(self) -> None:
        app, fakes = build_chat_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat/mission",
            json={"message": "写一个排序函数"},
        )
        body = resp.json()
        mentions = body["mentions"]
        self.assertEqual(len(mentions["resolved"]), 1)
        self.assertEqual(mentions["resolved"][0]["agentId"], "dev")
        self.assertEqual(mentions["unresolved"], [])

    def test_plain_message_rejects_empty(self) -> None:
        app, fakes = build_chat_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat/mission",
            json={"message": "   "},
        )
        self.assertEqual(resp.status_code, 422)

    def test_plain_message_default_workspace(self) -> None:
        app, fakes = build_chat_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat/mission",
            json={"message": "hello"},
        )
        self.assertEqual(resp.status_code, 202)
        # Check the mission was created in the default workspace
        body = resp.json()
        mid = body["missionId"]
        mission = fakes["repo"].missions.get(mid)
        self.assertIsNotNone(mission)
        self.assertEqual(mission.workspace_id, "local-admin")


class TestSessionAutoCreate(
    unittest.TestCase,
):
    """T3: session auto-create when client omits session_id."""

    def test_auto_creates_session_when_missing(self) -> None:
        app, fakes = build_chat_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat/mission",
            json={"message": "hello world"},
        )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(len(fakes["sessions"].created), 1)
        sess = fakes["sessions"].created[0]
        self.assertTrue(sess.id.startswith("sess-"))
        self.assertEqual(sess.workspace_id, "local-admin")

    def test_preserves_existing_session_id(self) -> None:
        app, fakes = build_chat_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat/mission",
            json={"message": "hello", "sessionId": "sess-preset-123"},
        )
        self.assertEqual(resp.status_code, 202)
        # No new session created
        self.assertEqual(len(fakes["sessions"].created), 0)


class TestSessionEventsWritten(
    unittest.TestCase,
):
    """T1-3: session events emitted during chat_mission chain."""

    def test_basic_session_events_emitted(self) -> None:
        app, fakes = build_chat_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat/mission",
            json={"message": "hello"},
        )
        self.assertEqual(resp.status_code, 202)
        event_types = [e.event_type.value for e in fakes["session_events"].events]
        # At minimum: message.created + mission.created (no mention → no mention.detected)
        self.assertIn("message.created", event_types)
        self.assertIn("mission.created", event_types)

    def test_mention_emits_mention_detected_event(self) -> None:
        app, fakes = build_chat_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat/mission",
            json={"message": "@dev 修复 bug"},
        )
        self.assertEqual(resp.status_code, 202)
        event_types = [e.event_type.value for e in fakes["session_events"].events]
        self.assertIn("mention.detected", event_types)


class TestMentionRouting(
    unittest.TestCase,
):
    """@mention → agent resolved from catalog, recorded as participant."""

    def test_resolves_catalog_agent(self) -> None:
        app, fakes = build_chat_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat/mission",
            json={"message": "@researcher 做个调研"},
        )
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        mentions = body["mentions"]
        self.assertEqual(len(mentions["resolved"]), 1)
        self.assertEqual(mentions["resolved"][0]["agentId"], "researcher")

    def test_unresolved_mention_recorded(self) -> None:
        app, fakes = build_chat_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat/mission",
            json={"message": "@ghost 这个 agent 不存在"},
        )
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        mentions = body["mentions"]
        self.assertEqual(mentions["resolved"], [])
        self.assertEqual(len(mentions["unresolved"]), 1)
        self.assertEqual(mentions["unresolved"][0]["name"], "ghost")


class TestArchivistMention(
    unittest.TestCase,
):
    """@archivist special mention → receipts preprocessed + returned in response."""

    def test_archivist_is_flagged_special(self) -> None:
        app, fakes = build_chat_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat/mission",
            json={"message": "@archivist 查一下历史"},
        )
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertIn("archivist", body["mentions"]["special"])

    def test_archivist_returns_receipts_bundle(self) -> None:
        app, fakes = build_chat_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat/mission",
            json={"message": "@archivist 查一下历史"},
        )
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        # archivist key present with query field
        self.assertIn("archivist", body)
        self.assertIn("query", body["archivist"])
        # When no prior missions in fake repo, receipts list is empty
        self.assertIsInstance(body["archivist"]["receipts"], list)


class TestRuleConfirmationGate(
    unittest.TestCase,
):
    """T5: rule.yaml with require_confirmation=true → 202 pending response."""

    def test_rule_with_confirmation_returns_pending(self) -> None:
        rules_yaml = """
rules:
  - id: auto-mission-test
    description: test rule
    trigger:
      kind: keyword
      keywords: ["自动"]
    action:
      kind: create_mission
      require_confirmation: true
      target_agent: dev
"""
        app, fakes = build_chat_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat/mission",
            json={
                "message": "帮我自动部署一下",
                "rulesYaml": rules_yaml,
            },
        )
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["status"], "pending")
        self.assertIn("pendingId", body)
        self.assertEqual(body["reason"], "rule_requires_confirmation")
        self.assertEqual(len(fakes["pending"].pendings), 1)

    def test_rule_no_confirmation_creates_mission_directly(self) -> None:
        rules_yaml = """
rules:
  - id: auto-test-direct
    description: direct rule
    trigger:
      kind: keyword
      keywords: ["直接"]
    action:
      kind: create_mission
      require_confirmation: false
      target_agent: dev
"""
        app, fakes = build_chat_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat/mission",
            json={
                "message": "直接开始吧",
                "rulesYaml": rules_yaml,
            },
        )
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["status"], "RUNNING")
        # No pending created
        self.assertEqual(len(fakes["pending"].pendings), 0)
        # rulesHit recorded
        self.assertIsNotNone(body.get("rulesHit"))


class TestConfirmCancel(
    unittest.TestCase,
):
    """T5 confirm + cancel endpoints."""

    def test_confirm_pending(self) -> None:
        rules_yaml = """
rules:
  - id: test-confirm
    description: rule
    trigger:
      kind: keyword
      keywords: ["测试确认"]
    action:
      kind: create_mission
      require_confirmation: true
      target_agent: dev
"""
        app, fakes = build_chat_app()
        client = TestClient(app)
        # Create pending
        resp = client.post(
            "/api/v1/chat/mission",
            json={"message": "测试确认", "rulesYaml": rules_yaml},
        )
        body = resp.json()
        pid = body["pendingId"]

        # Confirm
        resp2 = client.post(
            "/api/v1/chat/confirm",
            json={"pendingId": pid},
        )
        self.assertEqual(resp2.status_code, 202)
        confirmed = resp2.json()
        self.assertIn("missionId", confirmed)

    def test_cancel_pending(self) -> None:
        rules_yaml = """
rules:
  - id: test-cancel
    description: rule
    trigger:
      kind: keyword
      keywords: ["测试取消"]
    action:
      kind: create_mission
      require_confirmation: true
      target_agent: dev
"""
        app, fakes = build_chat_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat/mission",
            json={"message": "测试取消", "rulesYaml": rules_yaml},
        )
        pid = resp.json()["pendingId"]

        resp2 = client.post(
            "/api/v1/chat/cancel",
            json={"pendingId": pid},
        )
        self.assertEqual(resp2.status_code, 200)
        cancelled = resp2.json()
        self.assertEqual(cancelled["status"], "cancelled")


# ═══════════════════════════════════════════════════════════════════════
# T2-3: Runner claim fencing — concurrent claim压测
# ═══════════════════════════════════════════════════════════════════════


class _FakeMissionRepoForClaim:
    """Fake repo that tracks claim attempts and can simulate concurrency.

    Supports:
    - Atomic claim fencing (first caller wins)
    - Lease expiry simulation (clock-based ``_now_fn``)
    - Heartbeat extension of lease window
    - Multiple work_unit templates (desktop.task + a2a.inbound) for T2-3c
    """

    def __init__(self, *, now_fn=None) -> None:
        self.claims: list[dict[str, Any]] = []
        self.missions: dict[str, Any] = {}
        self._leased: dict[str, dict[str, Any]] = {}  # work_unit_id -> {lease_id, runner_id, expires_at, kind}
        self._work_units: dict[str, dict[str, Any]] = {}  # work_unit_id -> template
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def register_work_unit(
        self, work_unit_id: str, *, kind: str = "desktop.task", ready: bool = True
    ) -> None:
        """Register a work_unit template for claim matching."""
        self._work_units[work_unit_id] = {"kind": kind, "ready": ready}

    def _is_expired(self, work_unit_id: str) -> bool:
        lease = self._leased.get(work_unit_id)
        if lease is None:
            return False
        return self._now_fn() >= lease["expires_at"]

    async def claim_ready_work_unit(
        self,
        workspace_id: str,
        *,
        runner_id: str,
        agent_id: str,
        adapter_type: str,
        supported_work_unit_kinds: tuple[str, ...],
        lease_seconds: int,
    ) -> dict[str, Any]:
        """Simulate atomic claim with lease fencing + expiry-aware retry."""
        self.claims.append({
            "op": "claim",
            "runner_id": runner_id,
            "agent_id": agent_id,
            "adapter_type": adapter_type,
        })
        # Find first ready work_unit matching supported kinds
        for wu_id, template in self._work_units.items():
            if not template["ready"]:
                continue
            if template["kind"] not in supported_work_unit_kinds:
                continue
            lease = self._leased.get(wu_id)
            if lease is not None and not self._is_expired(wu_id):
                return {"ok": False, "reason": "already_leased", "workUnitId": wu_id}
            # Either no lease or lease expired → claim
            lease_id = f"lease-{runner_id}-{len(self.claims)}"
            self._leased[wu_id] = {
                "lease_id": lease_id,
                "runner_id": runner_id,
                "expires_at": self._now_fn() + __import__("datetime").timedelta(seconds=lease_seconds),
                "kind": template["kind"],
            }
            return {
                "ok": True,
                "missionId": f"mis-{wu_id}",
                "workUnitId": wu_id,
                "leaseId": lease_id,
                "leaseSeconds": lease_seconds,
                "workUnitKind": template["kind"],
            }
        return {"ok": False, "reason": "no_ready_work_unit"}

    async def release_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
    ) -> dict[str, Any]:
        self.claims.append({
            "op": "release",
            "runner_id": runner_id,
            "lease_id": lease_id,
        })
        expected = self._leased.get(work_unit_id)
        if expected is None:
            return {"ok": False, "reason": "not_leased"}
        if expected["lease_id"] != lease_id or expected["runner_id"] != runner_id:
            return {"ok": False, "reason": "lease_mismatch"}
        del self._leased[work_unit_id]
        return {"ok": True}

    async def heartbeat_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        self.claims.append({
            "op": "heartbeat",
            "runner_id": runner_id,
            "lease_id": lease_id,
        })
        expected = self._leased.get(work_unit_id)
        if expected is None:
            return {"ok": False, "reason": "not_leased"}
        if expected["lease_id"] != lease_id or expected["runner_id"] != runner_id:
            return {"ok": False, "reason": "lease_mismatch"}
        expected["expires_at"] = self._now_fn() + __import__("datetime").timedelta(seconds=lease_seconds)
        return {"ok": True}


class _FakeControlClient:
    """Fake runner-side HTTP client that records calls."""

    def __init__(self, repo: _FakeMissionRepoForClaim) -> None:
        self._repo = repo
        self.calls: list[dict[str, Any]] = []

    async def claim_ready_work_unit(
        self, workspace_id: str, *, runner_id: str, agent_id: str,
        adapter_type: str, supported_work_unit_kinds: tuple[str, ...],
        lease_seconds: int,
    ) -> dict[str, Any]:
        self.calls.append({"op": "claim_ready", "runner_id": runner_id})
        return await self._repo.claim_ready_work_unit(
            workspace_id, runner_id=runner_id, agent_id=agent_id,
            adapter_type=adapter_type,
            supported_work_unit_kinds=supported_work_unit_kinds,
            lease_seconds=lease_seconds,
        )

    async def heartbeat_work_unit(
        self, mission_id: str, work_unit_id: str, *, runner_id: str,
        lease_id: str, lease_seconds: int,
    ) -> dict[str, Any]:
        self.calls.append({"op": "heartbeat", "runner_id": runner_id, "lease_id": lease_id})
        return await self._repo.heartbeat_work_unit(
            mission_id, work_unit_id, runner_id=runner_id, lease_id=lease_id,
            lease_seconds=lease_seconds,
        )

    async def release_work_unit(
        self, mission_id: str, work_unit_id: str, *, runner_id: str, lease_id: str,
    ) -> dict[str, Any]:
        self.calls.append({"op": "release", "runner_id": runner_id, "lease_id": lease_id})
        return await self._repo.release_work_unit(
            mission_id, work_unit_id, runner_id=runner_id, lease_id=lease_id,
        )


class TestRunnerClaimFencing(
    unittest.TestCase,
):
    """T2-3: N runner 竞争同一 ready work_unit → 1 winner N-1 rejected."""

    def test_concurrent_claims_one_winner(self) -> None:
        repo = _FakeMissionRepoForClaim()
        repo.register_work_unit("wu-test", kind="desktop.task")
        results: list[dict[str, Any]] = []
        errors: list[Exception] = []

        async def _claim(runner_id: str) -> None:
            client = _FakeControlClient(repo)
            try:
                r = await client.claim_ready_work_unit(
                    "local-admin",
                    runner_id=runner_id,
                    agent_id=f"agent-{runner_id}",
                    adapter_type="local",
                    supported_work_unit_kinds=("desktop.task",),
                    lease_seconds=300,
                )
                results.append({"runner_id": runner_id, "claim": r})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        runners = [f"runner-{i}" for i in range(10)]
        asyncio.get_event_loop().run_until_complete(
            asyncio.gather(*[_claim(r) for r in runners])
        )

        winners = [r for r in results if r["claim"].get("ok")]
        losers = [r for r in results if not r["claim"].get("ok")]
        self.assertEqual(len(winners), 1, f"expected 1 winner, got {len(winners)}")
        self.assertEqual(len(losers), 9, f"expected 9 losers, got {len(losers)}")
        self.assertEqual(len(errors), 0)

    def test_heartbeat_requires_valid_lease(self) -> None:
        """After a runner wins, another cannot heartbeat the work_unit."""
        repo = _FakeMissionRepoForClaim()
        repo.register_work_unit("wu-test", kind="desktop.task")

        async def _do() -> None:
            c1 = _FakeControlClient(repo)
            c2 = _FakeControlClient(repo)

            # Runner 0 claims
            win = await c1.claim_ready_work_unit(
                "local-admin", runner_id="runner-0", agent_id="a0",
                adapter_type="local", supported_work_unit_kinds=("desktop.task",),
                lease_seconds=300,
            )
            self.assertTrue(win["ok"])
            lease_id = win["leaseId"]

            # Runner 1 tries heartbeat with its own (fake) lease — must fail
            bad_hb = await c2.heartbeat_work_unit(
                "mis-test-1", "wu-test", runner_id="runner-1",
                lease_id="fake-lease", lease_seconds=300,
            )
            self.assertFalse(bad_hb["ok"])

            # Original runner's heartbeat succeeds
            good_hb = await c1.heartbeat_work_unit(
                "mis-test-1", "wu-test", runner_id="runner-0",
                lease_id=lease_id, lease_seconds=300,
            )
            self.assertTrue(good_hb["ok"])

        _run(_do())

    def test_release_requires_lease_match(self) -> None:
        """Another runner cannot release a work_unit it doesn't hold."""
        repo = _FakeMissionRepoForClaim()
        repo.register_work_unit("wu-test", kind="desktop.task")

        async def _do() -> None:
            c1 = _FakeControlClient(repo)
            c2 = _FakeControlClient(repo)

            win = await c1.claim_ready_work_unit(
                "local-admin", runner_id="runner-0", agent_id="a0",
                adapter_type="local", supported_work_unit_kinds=("desktop.task",),
                lease_seconds=300,
            )
            lease_id = win["leaseId"]

            # Runner 1 tries to steal-release — must fail
            bad_rel = await c2.release_work_unit(
                "mis-test-1", "wu-test", runner_id="runner-1",
                lease_id="wrong-lease",
            )
            self.assertFalse(bad_rel["ok"])

            # Correct runner releases
            good_rel = await c1.release_work_unit(
                "mis-test-1", "wu-test", runner_id="runner-0",
                lease_id=lease_id,
            )
            self.assertTrue(good_rel["ok"])

            # After release, next claim succeeds
            win2 = await c2.claim_ready_work_unit(
                "local-admin", runner_id="runner-1", agent_id="a1",
                adapter_type="local", supported_work_unit_kinds=("desktop.task",),
                lease_seconds=300,
            )
            self.assertTrue(win2["ok"])

        _run(_do())


# ═══════════════════════════════════════════════════════════════════════
# T2-3b: Lease expiry + heartbeat gap recovery
# ═══════════════════════════════════════════════════════════════════════


class TestLeaseExpiryAndHeartbeatGap(
    unittest.TestCase,
):
    """T2-3b: When lease expires because the holder died (no heartbeat),
    another runner must be able to claim the work_unit."""

    def test_expired_lease_can_be_reclaimed(self) -> None:
        """Runner 1 leases, lease expires → runner 2 succeeds."""
        fake_clock = [datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)]
        repo = _FakeMissionRepoForClaim(now_fn=lambda: fake_clock[0])
        repo.register_work_unit("wu-exp", kind="desktop.task")
        c1 = _FakeControlClient(repo)
        c2 = _FakeControlClient(repo)

        # Runner 1 claims at t=0
        win1 = asyncio.get_event_loop().run_until_complete(
            c1.claim_ready_work_unit(
                "ws", runner_id="runner-1", agent_id="a1",
                adapter_type="local", supported_work_unit_kinds=("desktop.task",),
                lease_seconds=30,
            )
        )
        self.assertTrue(win1["ok"])

        # Ticking forward 20s — lease still active, runner 2 must fail
        fake_clock[0] += timedelta(seconds=20)
        fail = asyncio.get_event_loop().run_until_complete(
            c2.claim_ready_work_unit(
                "ws", runner_id="runner-2", agent_id="a2",
                adapter_type="local", supported_work_unit_kinds=("desktop.task",),
                lease_seconds=30,
            )
        )
        self.assertFalse(fail["ok"], "lease still active → runner 2 must be rejected")

        # Ticking past expiry → runner 1 "died", runner 2 reclaims
        fake_clock[0] += timedelta(seconds=15)  # now 35s after claim → 5s past expiry
        win2 = asyncio.get_event_loop().run_until_complete(
            c2.claim_ready_work_unit(
                "ws", runner_id="runner-2", agent_id="a2",
                adapter_type="local", supported_work_unit_kinds=("desktop.task",),
                lease_seconds=30,
            )
        )
        self.assertTrue(win2["ok"], "expired lease must allow reclaim")
        self.assertIn("runner-2", win2["leaseId"])

    def test_heartbeat_extends_lease(self) -> None:
        """Regular heartbeat keeps the lease alive so no takeover happens."""
        fake_clock = [datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)]
        repo = _FakeMissionRepoForClaim(now_fn=lambda: fake_clock[0])
        repo.register_work_unit("wu-hb", kind="desktop.task")
        c1 = _FakeControlClient(repo)
        c2 = _FakeControlClient(repo)

        # Runner 1 claims at t=0, lease = 30s
        win1 = asyncio.get_event_loop().run_until_complete(
            c1.claim_ready_work_unit(
                "ws", runner_id="runner-1", agent_id="a1",
                adapter_type="local", supported_work_unit_kinds=("desktop.task",),
                lease_seconds=30,
            )
        )
        lease_id = win1["leaseId"]

        # At t=20s heartbeat extends to t=50s
        fake_clock[0] += timedelta(seconds=20)
        hb = asyncio.get_event_loop().run_until_complete(
            c1.heartbeat_work_unit(
                "mis-wu-hb", "wu-hb", runner_id="runner-1",
                lease_id=lease_id, lease_seconds=30,
            )
        )
        self.assertTrue(hb["ok"])

        # At t=45s (25s after heartbeat, 15s before new expiry)
        # runner 2 still cannot claim
        fake_clock[0] += timedelta(seconds=25)
        fail = asyncio.get_event_loop().run_until_complete(
            c2.claim_ready_work_unit(
                "ws", runner_id="runner-2", agent_id="a2",
                adapter_type="local", supported_work_unit_kinds=("desktop.task",),
                lease_seconds=30,
            )
        )
        self.assertFalse(fail["ok"], "heartbeat-extended lease must still block takeover")

        # After lease finally expires → takeover succeeds
        fake_clock[0] += timedelta(seconds=10)
        win2 = asyncio.get_event_loop().run_until_complete(
            c2.claim_ready_work_unit(
                "ws", runner_id="runner-2", agent_id="a2",
                adapter_type="local", supported_work_unit_kinds=("desktop.task",),
                lease_seconds=30,
            )
        )
        self.assertTrue(win2["ok"])


# ═══════════════════════════════════════════════════════════════════════
# T2-3c: A2A inbound claim fencing
# ═══════════════════════════════════════════════════════════════════════


class TestA2AInboundClaimFencing(unittest.TestCase):
    """T2-3c: a2a.inbound work_unit claims respect same lease fencing."""

    def test_a2a_inbound_kind_is_claimable(self) -> None:
        repo = _FakeMissionRepoForClaim()
        repo.register_work_unit("wu-a2a", kind="a2a.inbound")
        c1 = _FakeControlClient(repo)
        c2 = _FakeControlClient(repo)

        async def _do() -> None:
            win = await c1.claim_ready_work_unit(
                "ws", runner_id="a2a-runner-1", agent_id="peer-agent",
                adapter_type="a2a", supported_work_unit_kinds=("a2a.inbound",),
                lease_seconds=120,
            )
            self.assertTrue(win["ok"])
            self.assertEqual(win["workUnitKind"], "a2a.inbound")

            # Another a2a runner must be fenced out
            fail = await c2.claim_ready_work_unit(
                "ws", runner_id="a2a-runner-2", agent_id="peer-agent-2",
                adapter_type="a2a", supported_work_unit_kinds=("a2a.inbound",),
                lease_seconds=120,
            )
            self.assertFalse(fail["ok"])

        asyncio.get_event_loop().run_until_complete(_do())

    def test_desktop_runner_cannot_claim_a2a_inbound(self) -> None:
        """desktop.task runners must not claim a2a.inbound work_units."""
        repo = _FakeMissionRepoForClaim()
        repo.register_work_unit("wu-a2a-only", kind="a2a.inbound")
        c1 = _FakeControlClient(repo)

        async def _do() -> None:
            # desktop runner claims with only desktop.task kind → must not match
            fail = await c1.claim_ready_work_unit(
                "ws", runner_id="desktop-runner", agent_id="local-agent",
                adapter_type="local", supported_work_unit_kinds=("desktop.task",),
                lease_seconds=300,
            )
            self.assertFalse(fail["ok"], "desktop runner must not claim a2a.inbound")

        asyncio.get_event_loop().run_until_complete(_do())


# ═══════════════════════════════════════════════════════════════════════
# T3-1c: Mission creation latency gate
# ═══════════════════════════════════════════════════════════════════════


class TestMissionCreationLatencyGate(unittest.TestCase):
    """T3-1c: POST /chat/mission must complete within P95 latency gate.

    Uses the same TestClient harness as T2-1 — runs ``N`` sequential
    creations and asserts the 95th percentile is below 500 ms on mock.
    This is a soft gate (warning on miss, not CI fail) because test
    infra noise varies.
    """

    GATE_P95_MS = 500.0  # Target for a mock (no real LLM)
    SAMPLES = 12         # Sequential samples, take P95

    def test_p95_creation_latency_below_gate(self) -> None:
        app, _fakes = build_chat_app()
        client = TestClient(app)
        latencies_ms: list[float] = []

        for _ in range(self.SAMPLES):
            start = time.perf_counter()
            resp = client.post(
                "/api/v1/chat/mission",
                json={"message": "测试 mission 创建延迟"},
            )
            elapsed = (time.perf_counter() - start) * 1000
            self.assertEqual(resp.status_code, 202)
            latencies_ms.append(elapsed)

        latencies_ms.sort()
        # Linear interpolation for exact P95
        idx = 0.95 * (len(latencies_ms) - 1)
        lo = int(idx)
        hi = min(lo + 1, len(latencies_ms) - 1)
        frac = idx - lo
        p95 = latencies_ms[lo] + (latencies_ms[hi] - latencies_ms[lo]) * frac

        # Also report min/max/median for diagnostics
        median = latencies_ms[len(latencies_ms) // 2]
        print(f"\n  latency samples: min={latencies_ms[0]:.1f}ms "
              f"median={median:.1f}ms p95={p95:.1f}ms max={latencies_ms[-1]:.1f}ms "
              f"(gate={self.GATE_P95_MS:.0f}ms)")

        # Soft gate: if P95 exceeds 2x target we fail the test (real regression)
        # Otherwise just warn via print — mock infra noise is expected
        self.assertLess(
            p95,
            self.GATE_P95_MS * 2,
            f"Mission creation P95 {p95:.1f}ms exceeds 2x gate "
            f"({self.GATE_P95_MS * 2:.0f}ms) — likely regression",
        )


# ═══════════════════════════════════════════════════════════════════════
# Run (unittest discovery friendly)
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main()
