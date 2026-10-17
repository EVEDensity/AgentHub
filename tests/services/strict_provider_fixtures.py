"""Real socket peer and SQLite ledger for strict-provider protocol tests."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.db.init_db import _create_mission_control_plane_sqlite
from app.db.migrations.mission_budget import upgrade_mission_budget_sqlite
from app.db.sqlite_pool import SQLitePool
from app.repositories import MissionRepository
from app.repositories.mission_budget_repository import MissionBudgetRepository
from app.services.mission_budget_service import MissionBudgetService
from app.services.model_contract import Message, ModelRequest
from app.services.strict_provider import BudgetedModelPort, StrictProviderProfile
from tests.services.mission_budget_fixtures import (
    execution_scope,
    seed_mission,
    server_prices,
)

KEY = "test-only-private-bearer-9e0c"


def profile(endpoint="https://provider.invalid/v1/chat/completions", **updates):
    value = StrictProviderProfile(
        profile_id="strict-fixture-v1",
        provider="provider",
        model="model",
        endpoint=endpoint,
        max_billing_input_tokens=100_000,
        max_completion_tokens=100,
        context_ceiling_source="operator fixture attestation; not external-provider evidence",
        context_ceiling_reviewed_at="2026-10-18",
        operator_verified_context_ceiling=True,
        timeout_seconds=3,
    )
    return replace(value, **updates)


def request(operation="harness:model:1:response", **updates):
    value = ModelRequest(
        messages=(Message("user", "Answer the task"),),
        stream=False,
        metadata={"modelOperationId": operation},
        timeout_seconds=3,
    )
    return replace(value, **updates)


def response(receipt="response-1", **updates):
    value = {
        "id": receipt,
        "model": "model",
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "done"},
            }
        ],
    }
    return {**value, **updates}


class LoopbackPeer:
    def __init__(self):
        self.requests = []
        self.payload = response()
        self.status = 200
        self.headers = {"Content-Type": "application/json"}
        self.raw_body = None
        self.before_response = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                peer.requests.append((self.path, dict(self.headers), body))
                peer.entered.set()
                peer.release.wait(timeout=15)
                if peer.before_response is not None:
                    peer.before_response()
                content = (
                    peer.raw_body
                    if peer.raw_body is not None
                    else json.dumps(peer.payload).encode()
                )
                try:
                    self.send_response(peer.status)
                    for name, value in peer.headers.items():
                        self.send_header(name, value)
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.endpoint = (
            f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions"
        )

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    async def wait_entered(self):
        assert await asyncio.to_thread(self.entered.wait, 10)


class SQLiteBudgetFixture:
    async def start(self, directory: Path):
        self.pool = SQLitePool(directory / "strict-budget.sqlite3")
        await self.pool.initialize()
        async with self.pool.acquire() as connection:
            self.connection = connection
            await _create_mission_control_plane_sqlite(connection)
            await upgrade_mission_budget_sqlite(connection)

        @asynccontextmanager
        async def transaction():
            async with self.pool.acquire() as current, current.transaction():
                yield current

        self.repository = MissionRepository(
            execute=connection.execute,
            fetch_one=connection.fetchrow,
            fetch_all=connection.fetch,
            transaction_factory=transaction,
        )
        self.ledger = MissionBudgetRepository.from_mission_transaction(self.repository)
        self.service = MissionBudgetService(self.repository, server_prices())
        await seed_mission(self.repository)
        self.scope = execution_scope()
        return self

    def port(self, peer, *, limit_usd=None, **profile_changes):
        return BudgetedModelPort(
            self.scope,
            self.service,
            profile(peer.endpoint, **profile_changes),
            KEY,
            limit_usd=limit_usd,
        )

    async def reservation(self, operation="harness:model:1:response"):
        row = await self.connection.fetchrow(
            "SELECT id FROM mission_budget_reservations WHERE operation_id=$1",
            operation,
        )
        return await self.ledger.reservation(row["id"])

    async def account(self):
        return await self.ledger.account("mis-1")

    async def close(self):
        await self.pool.close()
