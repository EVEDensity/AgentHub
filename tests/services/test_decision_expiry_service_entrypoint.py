from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.services.decision_expiry_supervisor import (
    DecisionExpiryPollStatus,
    DecisionExpirySupervisor,
    DecisionExpirySupervisorSnapshot,
)
from services.python.decision_expiry_service.config import (
    DecisionExpiryServiceSettings,
)
from services.python.decision_expiry_service.main import create_app
from services.python.decision_expiry_service.runtime import (
    DecisionExpiryServiceRuntime,
    build_decision_expiry_runtime,
)


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
            with self.subTest(values=values), self.assertRaises(ValidationError):
                DecisionExpiryServiceSettings(**values)

    def test_composition_requires_the_control_plane_database(self) -> None:
        settings = DecisionExpiryServiceSettings()
        with patch(
            "services.python.decision_expiry_service.runtime.DATABASE_URL",
            "",
        ), self.assertRaisesRegex(ValueError, "DATABASE_URL"):
            build_decision_expiry_runtime(settings)

    def test_composition_uses_mission_service_without_local_state(self) -> None:
        settings = DecisionExpiryServiceSettings()
        with patch(
            "services.python.decision_expiry_service.runtime.DATABASE_URL",
            "postgresql://configured",
        ):
            runtime = build_decision_expiry_runtime(settings)

        self.assertIsInstance(runtime.worker, DecisionExpirySupervisor)
        self.assertIsNotNone(runtime.initialize_database)
        self.assertIsNotNone(runtime.close_database)


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


class DecisionExpiryServiceEndpointTests(unittest.TestCase):
    def test_health_and_readiness_expose_only_sanitized_counters(self) -> None:
        worker = FakeSupervisor()
        worker._snapshot = replace(
            worker.snapshot,
            expired=3,
            last_poll_status=DecisionExpiryPollStatus.EXPIRED,
        )
        runtime = DecisionExpiryServiceRuntime(
            worker=worker,
            shutdown_timeout_seconds=1,
        )
        application = create_app(
            DecisionExpiryServiceSettings(),
            runtime_factory=lambda _: runtime,
        )

        with TestClient(application) as client:
            health = client.get("/healthz")
            ready = client.get("/readyz")

            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["status"], "ok")
            self.assertEqual(ready.status_code, 200)
            self.assertEqual(ready.json()["status"], "ready")
            self.assertEqual(ready.json()["worker"]["expired"], 3)
            self.assertEqual(
                ready.json()["worker"]["lastPollStatus"],
                "expired",
            )
            rendered = ready.text
            self.assertNotIn("mission-", rendered)
            self.assertNotIn("decision-sensitive-id", rendered)
            self.assertNotIn("postgresql://", rendered)

        self.assertEqual(worker.stop_requests, 1)


if __name__ == "__main__":
    unittest.main()
