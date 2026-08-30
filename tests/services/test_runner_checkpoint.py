from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any

from app.services.harness_checkpoint import (
    HarnessCheckpoint,
    HarnessError,
    HarnessEvent,
    HarnessEventType,
    HarnessExecutionContext,
)
from app.services.harness_service import ModelUsage
from app.services.runner_checkpoint import MissionControlHarnessCheckpointPort


def _checkpoint(*, execution: HarnessExecutionContext) -> HarnessCheckpoint:
    return HarnessCheckpoint(
        sequence=1,
        phase=HarnessEventType.EXECUTION_STARTED,
        execution=execution,
        iteration=0,
        tool_calls=0,
        usage=ModelUsage(),
        tool_results=(),
    )


def _event(*, execution: HarnessExecutionContext) -> HarnessEvent:
    return HarnessEvent(
        sequence=1,
        event_type=HarnessEventType.EXECUTION_STARTED,
        execution=execution,
        duration_ms=1,
        iteration=0,
        tool_calls=0,
        usage=ModelUsage(),
    )


class FakeControl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def record_execution_checkpoint(
        self,
        mission_id: str,
        work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append((mission_id, work_unit_id, kwargs))
        return {
            "id": kwargs["checkpoint_id"],
            "missionId": mission_id,
            "workUnitId": work_unit_id,
            "attempt": 2,
            "sequence": kwargs["sequence"],
            "phase": kwargs["phase"],
            "iteration": kwargs["iteration"],
            "toolCalls": kwargs["tool_calls"],
            "promptTokens": kwargs["prompt_tokens"],
            "completionTokens": kwargs["completion_tokens"],
            "modelCost": kwargs["model_cost"],
            "terminal": kwargs["terminal"],
            "stateDigest": "sha256:" + "a" * 64,
            "createdBy": {"id": "runner-1", "type": "service"},
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }


class DriftingControl(FakeControl):
    async def record_execution_checkpoint(
        self,
        mission_id: str,
        work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = await super().record_execution_checkpoint(
            mission_id,
            work_unit_id,
            **kwargs,
        )
        payload["attempt"] = 3
        return payload


class RunnerCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_only_durable_fields_and_uses_stable_identity(self) -> None:
        execution = HarnessExecutionContext("mis-1", "wu-1", 2)
        control = FakeControl()
        port = MissionControlHarnessCheckpointPort(
            control,
            execution=execution,
            runner_id="runner-1",
            lease_id="lease-1",
        )

        checkpoint = _checkpoint(execution=execution)
        await port.record(checkpoint, _event(execution=execution))

        call = control.calls[0][2]
        self.assertEqual(call["runner_id"], "runner-1")
        self.assertEqual(call["lease_id"], "lease-1")
        self.assertEqual(call["sequence"], 1)
        self.assertNotIn("tool_results", call)
        self.assertTrue(call["checkpoint_id"].startswith("chk-"))

        second = FakeControl()
        second_port = MissionControlHarnessCheckpointPort(
            second,
            execution=execution,
            runner_id="runner-1",
            lease_id="lease-1",
        )
        await second_port.record(checkpoint, _event(execution=execution))
        self.assertEqual(
            call["checkpoint_id"],
            second.calls[0][2]["checkpoint_id"],
        )

    async def test_rejects_execution_context_drift_before_control_call(self) -> None:
        execution = HarnessExecutionContext("mis-1", "wu-1", 2)
        control = FakeControl()
        port = MissionControlHarnessCheckpointPort(
            control,
            execution=execution,
            runner_id="runner-1",
            lease_id="lease-1",
        )

        with self.assertRaisesRegex(HarnessError, "context drifted"):
            await port.record(
                _checkpoint(execution=HarnessExecutionContext("mis-2", "wu-1", 2)),
                _event(execution=execution),
            )
        self.assertEqual(control.calls, [])

    async def test_rejects_control_response_identity_drift(self) -> None:
        execution = HarnessExecutionContext("mis-1", "wu-1", 2)
        control = DriftingControl()
        port = MissionControlHarnessCheckpointPort(
            control,
            execution=execution,
            runner_id="runner-1",
            lease_id="lease-1",
        )

        with self.assertRaisesRegex(HarnessError, "identity drifted"):
            await port.record(
                _checkpoint(execution=execution), _event(execution=execution)
            )

    # ── P3-4b: checkpoint slimming (same state digest skips the upload) ──

    async def test_same_state_digest_skips_upload_and_change_uploads(self) -> None:
        execution = HarnessExecutionContext("mis-1", "wu-1", 2)
        control = FakeControl()
        port = MissionControlHarnessCheckpointPort(
            control,
            execution=execution,
            runner_id="runner-1",
            lease_id="lease-1",
        )

        first = _checkpoint(execution=execution)
        await port.record(first, _event(execution=execution))
        self.assertEqual(len(control.calls), 1)

        # Same state (different harness phase/sequence): skipped entirely.
        second = HarnessCheckpoint(
            sequence=2,
            phase=HarnessEventType.MODEL_STARTED,
            execution=execution,
            iteration=0,
            tool_calls=0,
            usage=ModelUsage(),
            tool_results=(),
        )
        second_event = HarnessEvent(
            sequence=2,
            event_type=HarnessEventType.MODEL_STARTED,
            execution=execution,
            duration_ms=2,
            iteration=0,
            tool_calls=0,
            usage=ModelUsage(),
        )
        await port.record(second, second_event)
        self.assertEqual(len(control.calls), 1)

        # Changed state (usage grew): uploaded again with a contiguous
        # server-side sequence.
        third = HarnessCheckpoint(
            sequence=3,
            phase=HarnessEventType.MODEL_COMPLETED,
            execution=execution,
            iteration=1,
            tool_calls=0,
            usage=ModelUsage(prompt_tokens=10, completion_tokens=5),
            tool_results=(),
        )
        third_event = HarnessEvent(
            sequence=3,
            event_type=HarnessEventType.MODEL_COMPLETED,
            execution=execution,
            duration_ms=3,
            iteration=1,
            tool_calls=0,
            usage=ModelUsage(prompt_tokens=10, completion_tokens=5),
        )
        await port.record(third, third_event)
        self.assertEqual(len(control.calls), 2)
        self.assertEqual(control.calls[1][2]["sequence"], 2)
        self.assertEqual(control.calls[1][2]["iteration"], 1)

    async def test_terminal_checkpoint_is_always_uploaded(self) -> None:
        execution = HarnessExecutionContext("mis-1", "wu-1", 2)
        control = FakeControl()
        port = MissionControlHarnessCheckpointPort(
            control,
            execution=execution,
            runner_id="runner-1",
            lease_id="lease-1",
        )

        started = _checkpoint(execution=execution)
        await port.record(started, _event(execution=execution))

        terminal = HarnessCheckpoint(
            sequence=2,
            phase=HarnessEventType.EXECUTION_COMPLETED,
            execution=execution,
            iteration=0,
            tool_calls=0,
            usage=ModelUsage(),
            tool_results=(),
            terminal=True,
        )
        terminal_event = HarnessEvent(
            sequence=2,
            event_type=HarnessEventType.EXECUTION_COMPLETED,
            execution=execution,
            duration_ms=2,
            iteration=0,
            tool_calls=0,
            usage=ModelUsage(),
        )
        await port.record(terminal, terminal_event)

        self.assertEqual(len(control.calls), 2)
        self.assertTrue(control.calls[1][2]["terminal"])

    async def test_server_sequence_stays_contiguous_after_skips(self) -> None:
        execution = HarnessExecutionContext("mis-1", "wu-1", 2)
        control = FakeControl()
        port = MissionControlHarnessCheckpointPort(
            control,
            execution=execution,
            runner_id="runner-1",
            lease_id="lease-1",
        )

        for sequence in (1, 2, 3):
            event_type = (
                HarnessEventType.EXECUTION_STARTED
                if sequence == 1
                else HarnessEventType.MODEL_STARTED
            )
            await port.record(
                HarnessCheckpoint(
                    sequence=sequence,
                    phase=event_type,
                    execution=execution,
                    iteration=0,
                    tool_calls=0,
                    usage=ModelUsage(),
                    tool_results=(),
                ),
                HarnessEvent(
                    sequence=sequence,
                    event_type=event_type,
                    execution=execution,
                    duration_ms=sequence,
                    iteration=0,
                    tool_calls=0,
                    usage=ModelUsage(),
                ),
            )

        sequences = [call[2]["sequence"] for call in control.calls]
        self.assertEqual(sequences, [1])


if __name__ == "__main__":
    unittest.main()
