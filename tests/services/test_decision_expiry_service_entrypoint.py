from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.services.decision_expiry_supervisor import (
    DecisionExpiryPollStatus,
    DecisionExpirySupervisor,
    DecisionExpirySupervisorSnapshot,
)
from services.python.decision_expiry_service.config import (
    DecisionExpiryServiceSettings,
    read_database_url_file,
)
from services.python.decision_expiry_service.main import create_app
from services.python.decision_expiry_service.runtime import (
    DecisionExpiryDatabase,
    DecisionExpiryServiceRuntime,
    build_decision_expiry_runtime,
)


def _settings(**updates: Any) -> DecisionExpiryServiceSettings:
    values: dict[str, Any] = {
        "database_url_file": Path.cwd().resolve() / "database-url.secret",
    }
    values.update(updates)
    return DecisionExpiryServiceSettings(**values)


class FakeSupervisor:
    def __init__(self, *, stop_on_request: bool = True) -> None:
        self._snapshot = DecisionExpirySupervisorSnapshot()
        self._release = asyncio.Event()
        self.stop_on_request = stop_on_request
        self.stop_requests = 0
        self.cancelled = False

    @property
    def snapshot(self) -> DecisionExpirySupervisorSnapshot:
        return self._snapshot

    async def run(self) -> None:
        self._snapshot = replace(self._snapshot, running=True, ready=True)
        try:
            await self._release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self._snapshot = replace(self._snapshot, running=False, ready=False)

    def request_stop(self) -> None:
        self.stop_requests += 1
        self._snapshot = replace(self._snapshot, stop_requested=True)
        if self.stop_on_request:
            self._release.set()


class LifecycleRecorder:
    def __init__(self) -> None:
        self.initialized = 0
        self.closed = 0

    async def initialize(self) -> object:
        self.initialized += 1
        return object()

    async def close(self) -> None:
        self.closed += 1


class FailingLifecycleRecorder(LifecycleRecorder):
    async def initialize(self) -> object:
        self.initialized += 1
        raise RuntimeError("database unavailable")


class RecordingTransaction:
    def __init__(self, connection: RecordingConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> RecordingConnection:
        self.connection.transaction_enters += 1
        return self.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self.connection.transaction_exits += 1


class RecordingConnection:
    def __init__(self) -> None:
        self.transaction_enters = 0
        self.transaction_exits = 0

    def transaction(self) -> RecordingTransaction:
        return RecordingTransaction(self)


class RecordingPool:
    def __init__(self) -> None:
        self.initialized_urls: list[str] = []
        self.closed = 0
        self.connection = RecordingConnection()

    async def initialize(self, database_url: str) -> None:
        self.initialized_urls.append(database_url)

    @asynccontextmanager
    async def acquire(self):
        yield self.connection

    async def close(self) -> None:
        self.closed += 1


class FailingSupervisor(FakeSupervisor):
    async def run(self) -> None:
        raise RuntimeError("startup failed")


class DecisionExpiryServiceConfigurationTests(unittest.TestCase):
    def test_rejects_invalid_poll_and_shutdown_configuration(self) -> None:
        invalid = (
            {"port": 0},
            {"idle_delay_seconds": 0},
            {"idle_delay_seconds": 2, "max_delay_seconds": 1},
            {"shutdown_timeout_seconds": 0},
        )
        for values in invalid:
            values = {"database_url_file": Path.cwd().resolve() / "db", **values}
            with self.subTest(values=values), self.assertRaises(ValidationError):
                DecisionExpiryServiceSettings(**values)

    def test_requires_an_absolute_database_url_file(self) -> None:
        with self.assertRaises(ValidationError):
            DecisionExpiryServiceSettings(database_url_file=Path("database.url"))

    def test_plaintext_database_url_cannot_replace_the_secret_file(self) -> None:
        with self.assertRaises(ValidationError):
            DecisionExpiryServiceSettings(  # type: ignore[call-arg]
                database_url="postgresql://user:secret@database/agenthub"
            )

    def test_database_url_file_is_bounded_single_line_and_postgresql(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            valid = root / "valid.secret"
            valid.write_text(
                "postgresql://user:secret@database/agenthub?sslmode=require\n",
                encoding="utf-8",
            )
            self.assertEqual(
                read_database_url_file(valid),
                "postgresql://user:secret@database/agenthub?sslmode=require",
            )

            multiline = root / "multiline.secret"
            multiline.write_text("first\nsecond", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "one non-empty value"):
                read_database_url_file(multiline)

            invalid_urls = (
                "https://database/agenthub",
                "postgresql:///agenthub",
                "postgresql://database",
                "postgresql://database/agenthub#fragment",
                "postgresql://database:not-a-port/agenthub",
                "postgresql://database:0/agenthub",
                "postgresql://database/agent hub",
            )
            for index, value in enumerate(invalid_urls):
                path = root / f"invalid-{index}.secret"
                path.write_text(value, encoding="utf-8")
                with self.subTest(value=value), self.assertRaises(ValueError):
                    read_database_url_file(path)

    def test_composition_uses_mission_service_without_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory).resolve() / "database.secret"
            secret.write_text(
                "postgresql://user:secret@database/agenthub",
                encoding="utf-8",
            )
            pool = RecordingPool()
            runtime = build_decision_expiry_runtime(
                _settings(database_url_file=secret),
                pool=pool,
            )

        self.assertIsInstance(runtime.worker, DecisionExpirySupervisor)
        self.assertIsNotNone(runtime.initialize_database)
        self.assertIsNotNone(runtime.close_database)


class DecisionExpiryDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_one_pool_connection_for_the_real_transaction_scope(self) -> None:
        pool = RecordingPool()
        database = DecisionExpiryDatabase(
            "postgresql://user:secret@database/agenthub",
            pool=pool,
        )

        await database.initialize()
        async with database.transaction() as connection:
            self.assertIs(connection, pool.connection)
            self.assertEqual(pool.connection.transaction_enters, 1)
            self.assertEqual(pool.connection.transaction_exits, 0)
        await database.close()

        self.assertEqual(
            pool.initialized_urls,
            ["postgresql://user:secret@database/agenthub"],
        )
        self.assertEqual(pool.connection.transaction_exits, 1)
        self.assertEqual(pool.closed, 1)


class DecisionExpiryServiceRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_checks_database_and_graceful_stop_closes_it(self) -> None:
        worker = FakeSupervisor()
        lifecycle = LifecycleRecorder()
        runtime = DecisionExpiryServiceRuntime(
            worker=worker,
            shutdown_timeout_seconds=1,
            initialize_database=lifecycle.initialize,
            close_database=lifecycle.close,
        )

        await runtime.start()
        self.assertTrue(runtime.healthy)
        self.assertTrue(runtime.ready)
        self.assertEqual(lifecycle.initialized, 1)

        await runtime.stop()
        self.assertEqual(worker.stop_requests, 1)
        self.assertFalse(worker.cancelled)
        self.assertEqual(lifecycle.closed, 1)
        self.assertFalse(runtime.healthy)

    async def test_shutdown_deadline_cancels_stuck_command(self) -> None:
        worker = FakeSupervisor(stop_on_request=False)
        lifecycle = LifecycleRecorder()
        runtime = DecisionExpiryServiceRuntime(
            worker=worker,
            shutdown_timeout_seconds=0.01,
            initialize_database=lifecycle.initialize,
            close_database=lifecycle.close,
        )

        await runtime.start()
        await runtime.stop()

        self.assertTrue(worker.cancelled)
        self.assertEqual(lifecycle.closed, 1)

    async def test_failed_worker_startup_closes_initialized_database_once(self) -> None:
        lifecycle = LifecycleRecorder()
        runtime = DecisionExpiryServiceRuntime(
            worker=FailingSupervisor(),
            shutdown_timeout_seconds=1,
            initialize_database=lifecycle.initialize,
            close_database=lifecycle.close,
        )

        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            await runtime.start()
        await runtime.stop()

        self.assertEqual(lifecycle.initialized, 1)
        self.assertEqual(lifecycle.closed, 1)

    async def test_failed_database_initialization_still_closes_resources(self) -> None:
        lifecycle = FailingLifecycleRecorder()
        runtime = DecisionExpiryServiceRuntime(
            worker=FakeSupervisor(),
            shutdown_timeout_seconds=1,
            initialize_database=lifecycle.initialize,
            close_database=lifecycle.close,
        )

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            await runtime.start()
        await runtime.stop()

        self.assertEqual(lifecycle.initialized, 1)
        self.assertEqual(lifecycle.closed, 1)


class DecisionExpiryServiceEndpointTests(unittest.TestCase):
    def test_health_and_readiness_expose_only_sanitized_counters(self) -> None:
        worker = FakeSupervisor()
        worker._snapshot = replace(
            worker.snapshot,
            polls=5,
            expired=3,
            idle_polls=1,
            failed_polls=1,
            consecutive_failures=0,
            current_delay_seconds=0.5,
            last_poll_status=DecisionExpiryPollStatus.EXPIRED,
            last_success_at=datetime(2026, 8, 16, tzinfo=timezone.utc).isoformat(),
        )
        runtime = DecisionExpiryServiceRuntime(
            worker=worker,
            shutdown_timeout_seconds=1,
        )
        application = create_app(
            _settings(),
            runtime_factory=lambda _: runtime,
        )

        with TestClient(application) as client:
            health = client.get("/healthz")
            ready = client.get("/readyz")
            metrics = client.get("/metrics")

            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["status"], "ok")
            self.assertEqual(ready.status_code, 200)
            self.assertEqual(ready.json()["status"], "ready")
            self.assertEqual(ready.json()["worker"]["expired"], 3)
            self.assertEqual(
                ready.json()["worker"]["lastPollStatus"],
                "expired",
            )
            self.assertEqual(metrics.status_code, 200)
            self.assertTrue(
                metrics.headers["content-type"].startswith(
                    "text/plain; version=0.0.4"
                )
            )
            self.assertIn("agenthub_decision_expiry_process_healthy 1", metrics.text)
            self.assertIn("agenthub_decision_expiry_ready 1", metrics.text)
            self.assertIn("agenthub_decision_expiry_polls_total 5", metrics.text)
            self.assertIn(
                "agenthub_decision_expiry_decisions_expired_total 3",
                metrics.text,
            )
            self.assertIn(
                "agenthub_decision_expiry_last_success_timestamp_seconds "
                "1786838400.0",
                metrics.text,
            )
            rendered = ready.text + metrics.text
            self.assertNotIn("mission-", rendered)
            self.assertNotIn("decision-sensitive-id", rendered)
            self.assertNotIn("postgresql://", rendered)

        self.assertEqual(worker.stop_requests, 1)


if __name__ == "__main__":
    unittest.main()
