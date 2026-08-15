from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone
from typing import Any

from app.domain import Budgets, CapabilityGrant, Lease, MissionSource
from app.services.a2a_outbound_runner import (
    A2AOutboundClaimedWorkResolver,
    A2AOutboundTaskCommand,
    A2ARemoteTaskReference,
    A2ARemoteTaskSnapshot,
    A2ARemoteTaskState,
)
from app.services.runner_service import ClaimedWorkResolutionError
from tests.domain.factories import build_contract, build_mission, build_work_unit


class FakeControl:
    def __init__(self, execution_context: dict[str, Any]) -> None:
        self.execution_context = execution_context
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def get_execution_context(
        self,
        mission_id: str,
        work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append((mission_id, work_unit_id, kwargs))
        return {"executionContext": self.execution_context}


def outbound_context() -> dict[str, Any]:
    target = "https://receiver.example.test/a2a"
    mission = build_mission(
        status="RUNNING",
        objective="Build and return a verified release bundle.",
        source=MissionSource(
            type="a2a",
            reference=target,
            external_id="remote-task-1",
        ),
    )
    capabilities = ("a2a.send", "code_generation", "artifact.write")
    contract = build_contract(
        allowed_capabilities=[
            CapabilityGrant(capability=capability, scope={"agentUrl": target})
            for capability in capabilities
        ],
        budgets=Budgets(time_seconds=180, model_cost=1, retries=2),
    )
    work_unit = build_work_unit(
        kind="a2a.delegate",
        status="LEASED",
        attempt=2,
        assigned_agent_id="outbound-dispatcher",
        assigned_adapter="a2a.outbound",
        expected_outputs=[{"kind": "a2a.result", "required": True}],
        required_capabilities=capabilities,
        lease=Lease(
            id="lease-outbound",
            runner_id="runner-1",
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        ),
    )
    return {
        "version": 1,
        "mission": mission.to_public_dict(),
        "contract": contract.to_public_dict(),
        "workUnit": work_unit.to_public_dict(),
    }


def outbound_claim(context: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(context["workUnit"])


class A2AOutboundRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolver_builds_bounded_credential_free_command(self) -> None:
        context = outbound_context()
        control = FakeControl(context)
        resolver = A2AOutboundClaimedWorkResolver(
            control,
            runner_id="runner-1",
            source_agent_url="HTTPS://Sender.Example.Test/",
            max_timeout_seconds=90,
        )

        resolved = await resolver.resolve(outbound_claim(context))

        self.assertEqual(resolved.mission_id, "mis-1")
        self.assertEqual(resolved.work_unit_id, "wu-1")
        self.assertEqual(resolved.attempt, 2)
        self.assertEqual(resolved.lease_id, "lease-outbound")
        self.assertEqual(resolved.timeout_seconds, 90)
        self.assertEqual(
            resolved.command.reference,
            A2ARemoteTaskReference(
                target_agent_url="https://receiver.example.test/a2a",
                source_agent_url="https://sender.example.test",
                workspace_id="workspace-1",
                task_id="remote-task-1",
            ),
        )
        self.assertEqual(
            resolved.command.required_capabilities,
            ("code_generation", "artifact.write"),
        )
        params = resolved.command.to_send_params()
        self.assertEqual(params["id"], "remote-task-1")
        self.assertEqual(params["sourceAgentUrl"], "https://sender.example.test")
        self.assertNotIn("a2a.send", params["requiredCapabilities"])
        self.assertNotIn("lease-outbound", str(params))
        self.assertNotIn("runner-1", str(params))
        self.assertNotIn("modelCost", str(params))
        self.assertEqual(
            control.calls,
            [
                (
                    "mis-1",
                    "wu-1",
                    {"runner_id": "runner-1", "lease_id": "lease-outbound"},
                )
            ],
        )

    async def test_resolver_rejects_claim_and_context_identity_drift(self) -> None:
        cases = (
            (
                "source",
                lambda context, claim: context["mission"]["source"].update(type="api"),
                "source is not outbound",
            ),
            (
                "adapter",
                lambda context, claim: context["workUnit"].update(
                    assignedAdapter="local_codex"
                ),
                "adapter changed",
            ),
            (
                "lease",
                lambda context, claim: context["workUnit"]["lease"].update(
                    id="lease-other"
                ),
                "lease changed",
            ),
            (
                "lease owner",
                lambda context, claim: context["workUnit"]["lease"].update(
                    runnerId="runner-other"
                ),
                "belongs to another runner",
            ),
            (
                "scope",
                lambda context, claim: context["contract"][
                    "allowedCapabilities"
                ][0]["scope"].update(agentUrl="https://other.example.test"),
                "scope does not match",
            ),
            (
                "claim capability",
                lambda context, claim: claim.update(
                    requiredCapabilities=["code_generation"]
                ),
                "lacks a2a.send",
            ),
            (
                "input artifact",
                lambda context, claim: context["workUnit"].update(
                    inputRefs=[
                        {"id": "artifact-1", "digest": "sha256:" + "a" * 64}
                    ]
                ),
                "Artifact inputs are not supported",
            ),
        )
        for name, mutate, message in cases:
            with self.subTest(name=name):
                context = outbound_context()
                claim = outbound_claim(context)
                mutate(context, claim)
                resolver = A2AOutboundClaimedWorkResolver(
                    FakeControl(context),
                    runner_id="runner-1",
                    source_agent_url="https://sender.example.test",
                )
                with self.assertRaisesRegex(ClaimedWorkResolutionError, message):
                    await resolver.resolve(claim)

    async def test_resolver_rejects_command_over_size_limit(self) -> None:
        context = outbound_context()
        resolver = A2AOutboundClaimedWorkResolver(
            FakeControl(context),
            runner_id="runner-1",
            source_agent_url="https://sender.example.test",
            max_request_bytes=100,
        )

        with self.assertRaisesRegex(ClaimedWorkResolutionError, "size limit"):
            await resolver.resolve(outbound_claim(context))

    def test_transport_contracts_reject_unbounded_or_local_authority_data(self) -> None:
        reference = A2ARemoteTaskReference(
            target_agent_url="https://receiver.example.test/a2a",
            source_agent_url="https://sender.example.test",
            workspace_id="workspace-1",
            task_id="task-1",
        )
        with self.assertRaisesRegex(ValueError, "local transport authority"):
            A2AOutboundTaskCommand(
                reference=reference,
                objective="Do the work.",
                required_capabilities=("a2a.send",),
            )
        with self.assertRaisesRegex(ValueError, "status_message"):
            A2ARemoteTaskSnapshot(
                task_id="task-1",
                state=A2ARemoteTaskState.FAILED,
                status_message="x" * 2_001,
            )
        self.assertFalse(A2ARemoteTaskState.WORKING.is_terminal)
        self.assertFalse(A2ARemoteTaskState.INPUT_REQUIRED.is_terminal)
        self.assertTrue(A2ARemoteTaskState.COMPLETED.is_terminal)


if __name__ == "__main__":
    unittest.main()
