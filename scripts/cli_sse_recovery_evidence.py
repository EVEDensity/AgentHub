"""Real Mission SSE disconnect/recovery evidence gate.

The target service must be fronted by a test proxy that deliberately closes
the first stream after a durable event and sets
``AGENTHUB_CLI_SSE_FAULT_INJECTED=1``.  The script records event metadata only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.cli.sse_client import SseClient
from app.cli.transport import HttpTransport
from scripts.production_evidence import new_evidence, write_evidence


def main() -> int:
    output = os.environ.get("AGENTHUB_SSE_EVIDENCE_OUTPUT", "").strip()
    base_url = os.environ.get("AGENTHUB_CLI_BASE_URL", "").strip()
    token = os.environ.get("AGENTHUB_CLI_AUTH_TOKEN", "").strip()
    mission_id = os.environ.get("AGENTHUB_CLI_SSE_MISSION_ID", "").strip()
    fault_injected = os.environ.get("AGENTHUB_CLI_SSE_FAULT_INJECTED", "").lower() in {"1", "true", "yes"}
    if not base_url or not token or not mission_id:
        return _emit(output, status="SKIP", errorType="missing_sse_configuration")
    if not fault_injected:
        return _emit(output, status="SKIP", errorType="fault_injection_not_declared")
    transport = HttpTransport(base_url, timeout=15.0, retries=0)
    transport.set_token(token)
    client = SseClient(transport)
    try:
        first = list(client.stream_events(mission_id, after_sequence=0, max_seconds=15.0))
        reconnect = next((event for event in first if event.get("type") == "sse.reconnecting"), None)
        if reconnect is None:
            return _emit(output, status="FAIL", errorType="disconnect_not_observed")
        cursor = _cursor(first)
        second = list(
            client.stream_events(
                mission_id,
                after_sequence=cursor[0],
                after_event_id=cursor[1] or None,
                max_seconds=15.0,
            )
        )
        events = [event for event in first + second if event.get("type") not in {"sse.connected", "sse.reconnecting"}]
        event_ids = [str(event.get("eventId") or "") for event in events if event.get("eventId")]
        duplicate_ids = len(event_ids) != len(set(event_ids))
        completed = any(event.get("type") == "mission.completed" for event in events)
        return _emit(
            output,
            status="PASS" if completed and not duplicate_ids else "FAIL",
            reconnectObserved=True,
            afterSequence=cursor[0],
            afterEventId=cursor[1],
            duplicateEventIds=duplicate_ids,
            missionCompleted=completed,
            eventCount=len(events),
        )
    except Exception as exc:  # noqa: BLE001 - external gate must classify failures
        return _emit(output, status="FAIL", errorType=type(exc).__name__)
    finally:
        transport.close()


def _cursor(events: list[dict[str, object]]) -> tuple[int, str]:
    sequence = 0
    event_id = ""
    for event in events:
        try:
            candidate = int(event.get("sequence") or event.get("payload", {}).get("sequence") or 0)  # type: ignore[union-attr]
        except (TypeError, ValueError, AttributeError):
            candidate = 0
        if candidate >= sequence:
            sequence = candidate
            event_id = str(event.get("eventId") or event_id)
    return sequence, event_id


def _emit(output: str, **fields: object) -> int:
    record = new_evidence(scope="sse-recovery", evidence_level="production", **fields)
    rendered = json.dumps(record, ensure_ascii=False, sort_keys=True)
    print(rendered)
    write_evidence(record, scope="sse-recovery", mirror_path=output or None)
    return 0 if record["status"] in {"PASS", "SKIP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
