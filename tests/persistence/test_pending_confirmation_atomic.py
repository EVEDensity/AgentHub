"""Real SQLite consumption and Mission admission use one durable transaction."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from app.api.v1.chat_mission import _build_chat_contract
from app.db.init_db import _ainit_sqlite
from app.db.sqlite_pool import SQLitePool
from app.domain import (
    ActorRef,
    MissionSource,
    PendingConfirmation,
    PendingConfirmationStatus,
    Session,
    SessionEvent,
    SessionEventType,
)
from app.repositories import (
    MissionRepository,
    PendingConfirmationRepository,
    SessionRepository,
)
from app.services.agent_binding_service import DatabaseAgentBindingResolver
from app.services.mission_service import MissionService


class PendingConfirmationAtomicTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.pool = SQLitePool(Path(temporary.name) / "confirmations.sqlite3")
        await self.pool.initialize()
        self.addAsyncCleanup(self.pool.close)
        patcher = mock.patch("app.db.session.aget_pool", new=mock.AsyncMock(return_value=self.pool))
        patcher.start()
        self.addCleanup(patcher.stop)
        await _ainit_sqlite()
        async with self.pool.acquire() as connection:
            self.connection = connection
        self.pending = PendingConfirmationRepository()
        self.actor = ActorRef(type="human", id="alice")
        now = datetime.now(UTC)
        await SessionRepository().add_session(Session(
            id="session-alice", workspace_id="alice", title="Confirmations",
            created_by=self.actor, created_at=now, updated_at=now,
        ))
        await self.connection.execute(
            """INSERT INTO agent_catalog_bindings(scope_id,agent_id,adapter_type,capabilities,enabled)
                VALUES('alice','executor','function-calling','[]',TRUE)"""
        )
        await self._add_pending()

    async def _add_pending(self, identifier="pending-1", *, expired=False):
        now = datetime.now(UTC)
        await self.pending.add_pending(PendingConfirmation(
            id=identifier, session_id="session-alice", workspace_id="alice",
            rule_id="test-rule", rule_description="Requires human approval",
            action_kind="create_mission", target_agent="executor", message="Run approved work",
            created_by=self.actor, created_at=now,
            expires_at=now + timedelta(minutes=-1 if expired else 15),
        ))

    async def _confirm(self, identifier="pending-1", *, hold=None):
        """Compose the real service commands with transaction-bound companions."""
        async with self.pending.transaction() as transaction:
            pending = await transaction.pendings.get_pending_for_update(identifier)
            if pending.status != PendingConfirmationStatus.PENDING:
                return None
            if pending.expires_at <= datetime.now(UTC):
                await transaction.pendings.resolve_pending(identifier, PendingConfirmationStatus.EXPIRED)
                return None
            winner = await transaction.pendings.resolve_pending(identifier, PendingConfirmationStatus.CONFIRMED)
            if winner is None:
                return None
            if hold:
                hold.set()
                await asyncio.sleep(0.05)
            service = MissionService(transaction.missions, agent_binding_resolver=DatabaseAgentBindingResolver())
            mission_id = f"mis-confirm-{identifier}"
            source = MissionSource(type="chat", session_id=pending.session_id, metadata={
                "participants": [{"agentId": "executor", "adapterType": "function-calling", "capabilities": []}],
                "unresolved_mentions": [], "rule_confirm": {"pending_id": identifier},
            })
            mission = await service.create_mission(
                mission_id=mission_id, workspace_id=pending.workspace_id, title="Confirmed work",
                objective=pending.message, source=source, contract=_build_chat_contract(f"contract-{identifier}"),
                actor=self.actor,
            )
            mission = await service.start_mission(mission.id, actor=self.actor)
            await service.create_chat_work_unit(mission.id, workspace_id=pending.workspace_id)
            for event_type in (SessionEventType.DECISION_RECORDED, SessionEventType.MISSION_CREATED):
                await transaction.session_events.add_session_event(SessionEvent(
                    id=f"{event_type.value}-{identifier}", session_id=pending.session_id,
                    event_type=event_type, actor=self.actor,
                    payload={"pending_id": identifier, "mission_id": mission.id}, created_at=datetime.now(UTC),
                ))
            return mission

    async def _cancel(self, identifier="pending-1", *, hold=None):
        async with self.pending.transaction() as transaction:
            pending = await transaction.pendings.get_pending_for_update(identifier)
            if pending.status != PendingConfirmationStatus.PENDING:
                return None
            resolved = await transaction.pendings.resolve_pending(identifier, PendingConfirmationStatus.CANCELLED)
            if hold:
                hold.set()
                await asyncio.sleep(0.05)
            return resolved

    async def _counts(self):
        return {table: await self.connection.fetchval(f"SELECT COUNT(*) FROM {table}")
                for table in ("missions", "work_units", "mission_events", "session_events", "mission_contracts")}

    async def test_concurrent_double_confirm_creates_one_committed_execution(self):
        first_consumed = asyncio.Event()
        first = asyncio.create_task(self._confirm(hold=first_consumed))
        await first_consumed.wait()
        second = asyncio.create_task(self._confirm())
        results = await asyncio.gather(first, second)
        self.assertIsNotNone(results[0])
        self.assertIsNone(results[1])
        self.assertEqual((await self.pending.get_pending("pending-1")).status, PendingConfirmationStatus.CONFIRMED)
        self.assertEqual(await self._counts(), {"missions": 1, "work_units": 1, "mission_events": 3, "session_events": 2, "mission_contracts": 1})

    async def test_dispatch_failure_rolls_back_consumption_and_is_retryable(self):
        append = MissionRepository.append_event

        async def fail_on_work_unit(repository, event):
            if event.aggregate_type == "work_unit":
                raise RuntimeError("work unit event storage unavailable")
            await append(repository, event)

        with (
            mock.patch.object(MissionRepository, "append_event", fail_on_work_unit),
            self.assertRaisesRegex(RuntimeError, "work unit event storage unavailable"),
        ):
            await self._confirm()
        self.assertEqual((await self.pending.get_pending("pending-1")).status, PendingConfirmationStatus.PENDING)
        self.assertEqual(await self._counts(), {table: 0 for table in await self._counts()})
        self.assertIsNotNone(await self._confirm())
        self.assertEqual((await self._counts())["missions"], 1)

    async def test_receipt_failure_rolls_back_mission_work_unit_and_confirmation(self):
        from app.repositories import SessionEventRepository
        with (
            mock.patch.object(SessionEventRepository, "add_session_event", side_effect=RuntimeError("receipt unavailable")),
            self.assertRaisesRegex(RuntimeError, "receipt unavailable"),
        ):
            await self._confirm()
        self.assertEqual((await self.pending.get_pending("pending-1")).status, PendingConfirmationStatus.PENDING)
        self.assertTrue(all(count == 0 for count in (await self._counts()).values()))

    async def test_confirm_wins_cancel_race_without_cancel_overwriting_it(self):
        consumed = asyncio.Event()
        confirm = asyncio.create_task(self._confirm(hold=consumed))
        await consumed.wait()
        cancelled = asyncio.create_task(self._cancel())
        mission, cancellation = await asyncio.gather(confirm, cancelled)
        self.assertIsNotNone(mission)
        self.assertIsNone(cancellation)
        self.assertEqual((await self.pending.get_pending("pending-1")).status, PendingConfirmationStatus.CONFIRMED)
        self.assertEqual((await self._counts())["missions"], 1)

    async def test_cancel_wins_confirm_race_without_creating_a_mission(self):
        consumed = asyncio.Event()
        cancel = asyncio.create_task(self._cancel(hold=consumed))
        await consumed.wait()
        confirmed = asyncio.create_task(self._confirm())
        cancellation, mission = await asyncio.gather(cancel, confirmed)
        self.assertEqual(cancellation.status, PendingConfirmationStatus.CANCELLED)
        self.assertIsNone(mission)
        self.assertTrue(all(count == 0 for count in (await self._counts()).values()))

    async def test_expired_confirmation_is_never_dispatched(self):
        await self._add_pending("expired", expired=True)
        self.assertIsNone(await self.pending.resolve_pending("expired", PendingConfirmationStatus.CONFIRMED))
        self.assertIsNone(await self.pending.resolve_pending("expired", PendingConfirmationStatus.CANCELLED))
        self.assertIsNone(await self._confirm("expired"))
        self.assertEqual((await self.pending.get_pending("expired")).status, PendingConfirmationStatus.EXPIRED)
        self.assertTrue(all(count == 0 for count in (await self._counts()).values()))

    async def test_failure_for_different_pending_does_not_poison_success(self):
        await self._add_pending("pending-2")
        consumed = asyncio.Event()

        async def fail_first():
            async with self.pending.transaction() as transaction:
                await transaction.pendings.resolve_pending("pending-1", PendingConfirmationStatus.CONFIRMED)
                consumed.set()
                await asyncio.sleep(0.05)
                raise RuntimeError("first failed")

        failed = asyncio.create_task(fail_first())
        await consumed.wait()
        success = asyncio.create_task(self._confirm("pending-2"))
        results = await asyncio.gather(failed, success, return_exceptions=True)
        self.assertIsInstance(results[0], RuntimeError)
        self.assertIsNotNone(results[1])
        self.assertEqual((await self.pending.get_pending("pending-1")).status, PendingConfirmationStatus.PENDING)
        self.assertEqual((await self.pending.get_pending("pending-2")).status, PendingConfirmationStatus.CONFIRMED)
        self.assertEqual((await self._counts())["missions"], 1)
