"""Opt-in real-provider Mission closed-loop evidence harness.

This runs the production CLI runtime in an isolated temporary repository. A
missing credential is recorded as SKIP, never PASS. Output contains metadata
and event names only; prompts, provider payloads, credentials, and file bytes
are deliberately excluded.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.cli.events import normalize_event
from app.cli.runtime import CliModelSettings, execute_objective
from scripts.production_evidence import new_evidence, write_evidence

REQUIRED_EVENTS = (
    "assistant.delta",
    "tool.started",
    "tool.output",
    "checkpoint.created",
    "verification.started",
    "verification.completed",
    "mission.completed",
)


def main() -> int:
    provider = os.environ.get("AGENTHUB_CLI_PROVIDER", "deepseek").strip()
    model = os.environ.get("AGENTHUB_CLI_MODEL", "deepseek-v4-flash").strip()
    api_key = os.environ.get("AGENTHUB_CLI_MODEL_API_KEY", "").strip()
    base_url = os.environ.get("AGENTHUB_CLI_MODEL_BASE_URL", "").strip()
    output = os.environ.get("AGENTHUB_CLI_PROVIDER_MISSION_OUTPUT", "").strip()
    evidence = _base_evidence(provider, model)
    if not api_key:
        evidence.update(status="SKIP", errorType="missing_credentials")
        _emit(evidence, output)
        return 0

    started = time.perf_counter()
    event_types: list[str] = []
    started_call_ids: list[str] = []

    def record(raw: dict[str, Any]) -> None:
        event = normalize_event(raw)
        if event is None:
            return
        event_types.append(event.event_type)
        if event.event_type == "tool.started":
            call_id = str(
                event.payload.get("callId")
                or event.payload.get("call_id")
                or ""
            )
            if call_id:
                started_call_ids.append(call_id)

    try:
        with tempfile.TemporaryDirectory(prefix="agenthub-provider-") as temporary:
            workspace = Path(temporary) / "workspace"
            state = Path(temporary) / "state"
            workspace.mkdir()
            (workspace / "README.md").write_text(
                "# Provider evidence workspace\n", encoding="utf-8"
            )
            result = execute_objective(
                objective=(
                    "Read README.md, then create result.txt containing READY. "
                    "Use the available file tools and report the created artifact."
                ),
                workspace_root=workspace,
                state_dir=state,
                model=CliModelSettings(
                    provider=provider,
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                ),
                mission_timeout=240,
                runner_timeout_seconds=180,
                tool_permission_mode="accept-edits",
                on_decision_request=lambda _decision: True,
                on_event=record,
            )
        missing = [name for name in REQUIRED_EVENTS if name not in event_types]
        ordered = [event_types.index(name) for name in REQUIRED_EVENTS if name in event_types]
        duplicate_execution = len(started_call_ids) != len(set(started_call_ids))
        passed = (
            result.status == "SUCCEEDED"
            and not missing
            and ordered == sorted(ordered)
            and not duplicate_execution
        )
        evidence.update(
            status="PASS" if passed else "FAIL",
            missionId=result.mission_id,
            missionStatus=result.status,
            durationSeconds=round(time.perf_counter() - started, 3),
            eventTypes=event_types,
            missingEvents=missing,
            eventOrderValid=ordered == sorted(ordered),
            duplicateToolExecution=duplicate_execution,
            artifactCount=len(result.artifacts),
        )
        if not passed:
            evidence["errorType"] = "closed_loop_incomplete"
        _emit(evidence, output)
        return 0 if passed else 1
    except Exception as exc:  # noqa: BLE001 - evidence must classify all failures
        evidence.update(
            status="FAIL",
            errorType=type(exc).__name__,
            durationSeconds=round(time.perf_counter() - started, 3),
        )
        _emit(evidence, output)
        return 1


def _base_evidence(provider: str, model: str) -> dict[str, object]:
    return new_evidence(
        scope="mission-closed-loop",
        evidence_level="real-provider",
        provider=provider,
        model=model,
    )


def _emit(evidence: dict[str, object], output: str) -> None:
    rendered = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    print(rendered)
    write_evidence(evidence, scope="provider", mirror_path=output or None)


if __name__ == "__main__":
    raise SystemExit(main())
