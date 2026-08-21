from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.services.a2a_outbound_runner import A2A_OUTBOUND_ADAPTER
from app.services.a2a_outbound_supervisor import A2AOutboundSupervisionResult
from app.services.runner_service import (
    MissionControlRunnerPort,
    RunnerControlError,
    assert_claimed_work_unit,
    parse_workspace_claim_status,
)
from app.services.workspace_admission_service import WorkspaceClaimStatus


class A2AOutboundAttemptRunnerPort(Protocol):
    async def run_claimed(
        self,
        claimed_work_unit: Mapping[str, Any],
        *,
        lease_seconds: int = 300,
    ) -> A2AOutboundSupervisionResult: ...


@dataclass(frozen=True, slots=True)
class A2AOutboundWorkspacePollResult:
    """One outbound workspace claim and its native supervision result."""

    claim_status: WorkspaceClaimStatus
    supervision_result: A2AOutboundSupervisionResult | None

    def __post_init__(self) -> None:
        if not isinstance(self.claim_status, WorkspaceClaimStatus):
            raise TypeError("claim_status must be a WorkspaceClaimStatus")
        has_result = self.supervision_result is not None
        if has_result != (self.claim_status == WorkspaceClaimStatus.CLAIMED):
            raise ValueError("claim status and outbound result are inconsistent")
        if has_result and not isinstance(
            self.supervision_result,
            A2AOutboundSupervisionResult,
        ):
            raise TypeError("supervision_result must be an outbound result")


class A2AOutboundWorkspaceRunner:
    """Claim and execute at most one outbound A2A WorkUnit per workspace poll."""

    def __init__(
        self,
        control: MissionControlRunnerPort,
        attempt_runner: A2AOutboundAttemptRunnerPort,
        *,
        runner_id: str,
        assigned_agent_id: str,
    ) -> None:
        for name, value in (
            ("runner_id", runner_id),
            ("assigned_agent_id", assigned_agent_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if not callable(getattr(attempt_runner, "run_claimed", None)):
            raise TypeError("attempt_runner must execute claimed outbound work")
        self._control = control
        self._attempt_runner = attempt_runner
        self._runner_id = runner_id
        self._assigned_agent_id = assigned_agent_id

    async def claim_ready_and_run(
        self,
        workspace_id: str,
        *,
        lease_seconds: int = 300,
    ) -> A2AOutboundWorkspacePollResult:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id must be non-empty")
        if not 1 <= lease_seconds <= 3_600:
            raise ValueError("lease_seconds must be between 1 and 3600")

        claimed_payload = await self._control.claim_ready_work_unit(
            workspace_id,
            runner_id=self._runner_id,
            agent_id=self._assigned_agent_id,
            adapter_type=A2A_OUTBOUND_ADAPTER,
            supported_work_unit_kinds=("a2a.delegate",),
            lease_seconds=lease_seconds,
        )
        claim_status = parse_workspace_claim_status(claimed_payload)
        if claim_status != WorkspaceClaimStatus.CLAIMED:
            return A2AOutboundWorkspacePollResult(
                claim_status=claim_status,
                supervision_result=None,
            )

        work_unit = claimed_payload.get("workUnit")
        if not isinstance(work_unit, Mapping):
            raise RunnerControlError("Mission Control claim response has no WorkUnit")
        mission_id = work_unit.get("missionId")
        if not isinstance(mission_id, str) or not mission_id.strip():
            raise RunnerControlError("Mission Control claim response has no Mission id")
        assert_claimed_work_unit(
            work_unit,
            mission_id=mission_id,
            runner_id=self._runner_id,
            agent_id=self._assigned_agent_id,
            adapter_type=A2A_OUTBOUND_ADAPTER,
        )
        supervision_result = await self._attempt_runner.run_claimed(
            work_unit,
            lease_seconds=lease_seconds,
        )
        return A2AOutboundWorkspacePollResult(
            claim_status=claim_status,
            supervision_result=supervision_result,
        )


__all__ = [
    "A2AOutboundAttemptRunnerPort",
    "A2AOutboundWorkspacePollResult",
    "A2AOutboundWorkspaceRunner",
]
