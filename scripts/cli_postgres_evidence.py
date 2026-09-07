"""Real PostgreSQL LISTEN/NOTIFY evidence gate.

Requires ``DATABASE_URL`` and the optional ``asyncpg`` dependency.  The gate
uses only ephemeral NOTIFY payloads and records no database URL or data.
"""
from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.mission_event_bus import MissionEventBus, PostgresMissionEventNotifier
from scripts.production_evidence import new_evidence, write_evidence


def main() -> int:
    output = os.environ.get("AGENTHUB_POSTGRES_EVIDENCE_OUTPUT", "").strip()
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url.startswith(("postgres://", "postgresql://")):
        return _emit(output, status="SKIP", errorType="missing_postgres_database_url")
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        return _emit(output, status="SKIP", errorType="asyncpg_unavailable")
    try:
        result = asyncio.run(_exercise(database_url))
    except Exception as exc:  # noqa: BLE001 - gate must classify external failures
        return _emit(output, status="FAIL", errorType=type(exc).__name__)
    return _emit(output, **result)


async def _exercise(database_url: str) -> dict[str, object]:
    mission_id = "evidence-" + uuid.uuid4().hex
    bus = MissionEventBus()
    notifier = PostgresMissionEventNotifier(
        database_url,
        bus,
        connect_timeout=10.0,
        reconnect_min_seconds=0.1,
        reconnect_max_seconds=1.0,
    )
    received = 0
    reconnect_observed = False
    context = multiprocessing.get_context("spawn")
    first_published = context.Event()
    continue_publishing = context.Event()
    publisher = context.Process(
        target=_publish_process,
        args=(database_url, mission_id, first_published, continue_publishing),
    )
    publisher_started = False
    try:
        await notifier.start()
        async with bus.subscribe(mission_id) as queue:
            publisher.start()
            publisher_started = True
            await asyncio.to_thread(first_published.wait, 10.0)
            await asyncio.wait_for(queue.get(), timeout=10.0)
            received += 1
            first_connection = notifier._connection
            if first_connection is None:
                raise RuntimeError("listener_connection_missing")
            await first_connection.close()
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                current = notifier._connection
                if current is not None and current is not first_connection:
                    reconnect_observed = True
                    break
                await asyncio.sleep(0.1)
            if not reconnect_observed:
                raise TimeoutError("listener_reconnect_timeout")
            continue_publishing.set()
            await asyncio.wait_for(queue.get(), timeout=10.0)
            received += 1
    finally:
        continue_publishing.set()
        if publisher_started and publisher.is_alive():
            publisher.join(timeout=5.0)
            if publisher.is_alive():
                publisher.terminate()
                publisher.join(timeout=2.0)
        await notifier.stop()
    return {
        "status": "PASS" if received == 2 and reconnect_observed else "FAIL",
        "notificationsReceived": received,
        "reconnectObserved": reconnect_observed,
    }


def _publish_process(
    database_url: str,
    mission_id: str,
    first_published: multiprocessing.synchronize.Event,
    continue_publishing: multiprocessing.synchronize.Event,
) -> None:
    """Publish from a separate process to prove the cross-process channel."""

    async def publish() -> None:
        import asyncpg

        connection = await asyncpg.connect(database_url, timeout=10.0)
        try:
            await connection.execute(
                "SELECT pg_notify($1, $2)",
                PostgresMissionEventNotifier.channel,
                mission_id,
            )
            first_published.set()
            await asyncio.to_thread(continue_publishing.wait, 10.0)
            await connection.execute(
                "SELECT pg_notify($1, $2)",
                PostgresMissionEventNotifier.channel,
                mission_id,
            )
        finally:
            await connection.close()

    asyncio.run(publish())


def _emit(output: str, **fields: object) -> int:
    record = new_evidence(
        scope="postgres-listener",
        evidence_level="production",
        **fields,
    )
    rendered = json.dumps(record, ensure_ascii=False, sort_keys=True)
    print(rendered)
    write_evidence(record, scope="postgres", mirror_path=output or None)
    return 0 if record["status"] in {"PASS", "SKIP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
