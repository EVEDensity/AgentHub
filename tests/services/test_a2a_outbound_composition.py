from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

import httpx

from app.services.a2a_outbound_composition import (
    A2AOutboundAttemptError,
    build_a2a_outbound_attempt_runner,
)
from app.services.a2a_outbound_supervisor import A2AOutboundSupervisionOutcome
from app.services.a2a_peer_route_service import A2AAgentCardTrustPolicy
from tests.services.test_a2a_outbound_result import FakePublisher, valid_result_payload
from tests.services.test_a2a_outbound_runner import outbound_claim, outbound_context
from tests.services.test_a2a_peer_route_service import (
    agent_card,
    json_response,
    sign_card,
)

_ORIGIN = "https://receiver.example.test"
_TOKEN = "receiver-issued-token"


class StaticCredentialProvider:
    def __init__(self) -> None:
        self.origins: list[str] = []

    def bearer_for(self, agent_origin: str) -> str | None:
        self.origins.append(agent_origin)
        return _TOKEN if agent_origin == _ORIGIN else None


class FakeControl:
    def __init__(self, context: dict[str, Any]) -> None:
        self.context = context
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.context_gate: asyncio.Event | None = None
        self.failure_updates: dict[str, Any] = {}

    def _active(self, *, status: str, lease: bool) -> dict[str, Any]:
        return {
            "id": "wu-1",
            "missionId": "mis-1",
            "status": status,
            "attempt": 2,
            "lease": (
                {"id": "lease-outbound", "runnerId": "runner-1"} if lease else None
            ),
        }

    async def get_execution_context(
        self,
        _mission_id: str,
        _work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(("context", kwargs))
        if self.context_gate is not None:
            await self.context_gate.wait()
        return {"executionContext": self.context}

    async def start_work_unit(
        self,
        _mission_id: str,
        _work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(("start", kwargs))
        return self._active(status="RUNNING", lease=True)

    async def heartbeat_work_unit(
        self,
        _mission_id: str,
        _work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(("heartbeat", kwargs))
        return self._active(status="RUNNING", lease=True)

    async def register_artifact(
        self,
        mission_id: str,
        work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(("register", kwargs))
        artifact = kwargs["artifact"]
        return {
            "id": kwargs["artifact_id"],
            "missionId": mission_id,
            "workUnitId": work_unit_id,
            "attempt": 2,
            "kind": kwargs["kind"],
            "digest": artifact.digest,
            "contentAddress": artifact.content_address,
            "mediaType": kwargs["media_type"],
            "sizeBytes": artifact.size_bytes,
        }

    async def complete_work_unit(
        self,
        _mission_id: str,
        _work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(("complete", kwargs))
        return self._active(status="VERIFYING", lease=False)

    async def fail_work_unit(
        self,
        _mission_id: str,
        _work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(("fail", kwargs))
        failed = self._active(status="FAILED", lease=False)
        failed.update(self.failure_updates)
        return failed


def strict_policy() -> A2AAgentCardTrustPolicy:
    card = agent_card()
    card["skills"][0]["tags"].append("artifact.write")
    _, public_key = sign_card(card)
    return A2AAgentCardTrustPolicy(
        require_pinned_keys=True,
        trusted_public_keys={_ORIGIN: [public_key]},
    )


def signed_capable_card() -> dict[str, Any]:
    card = agent_card()
    card["skills"][0]["tags"].append("artifact.write")
    signed, _ = sign_card(card)
    return signed


class A2AOutboundCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_factory_runs_complete_strict_attempt_without_owning_client(
        self,
    ) -> None:
        requests: list[httpx.Request] = []
        signed = signed_capable_card()

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, content=json_response(signed))
            envelope = json.loads(request.content)
            if envelope["method"] == "tasks/send":
                result = {"id": "remote-task-1", "status": "completed"}
            else:
                result = valid_result_payload()
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "result": result, "id": envelope["id"]},
            )

        context = outbound_context()
        control = FakeControl(context)
        publisher = FakePublisher()
        credentials = StaticCredentialProvider()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            runner = build_a2a_outbound_attempt_runner(
                control,
                publisher=publisher,
                http_client=client,
                trust_policy=strict_policy(),
                credential_provider=credentials,
                runner_id="runner-1",
                source_agent_url="https://sender.example.test",
                poll_interval_seconds=0.001,
            )

            result = await runner.run_claimed(outbound_claim(context))

            self.assertFalse(client.is_closed)
        finally:
            await client.aclose()

        self.assertEqual(result.outcome, A2AOutboundSupervisionOutcome.LOCAL_VERIFYING)
        self.assertEqual(result.work_unit["status"], "VERIFYING")
        self.assertEqual(len(result.imported_artifacts), 2)
        self.assertEqual(len(publisher.contents), 2)
        self.assertEqual(
            [name for name, _ in control.calls],
            ["context", "start", "register", "register", "complete"],
        )
        self.assertEqual(credentials.origins, [_ORIGIN, _ORIGIN])
        card_requests = [request for request in requests if request.method == "GET"]
        task_requests = [request for request in requests if request.method == "POST"]
        self.assertEqual(
            [request.headers.get("Authorization") for request in card_requests],
            [None, None],
        )
        self.assertTrue(
            all(
                request.url.path == "/.well-known/agent-card.json"
                for request in card_requests
            )
        )
        self.assertEqual(
            [request.headers.get("Authorization") for request in task_requests],
            [f"Bearer {_TOKEN}", f"Bearer {_TOKEN}"],
        )
        self.assertTrue(
            all(request.url.path == "/platform/a2a/inbox" for request in task_requests)
        )

    async def test_factory_requires_strict_trust_and_open_injected_client(
        self,
    ) -> None:
        context = outbound_context()
        control = FakeControl(context)
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None))
        try:
            for policy in (
                A2AAgentCardTrustPolicy(),
                A2AAgentCardTrustPolicy(allow_unsigned_cards=True),
            ):
                with (
                    self.subTest(policy=policy),
                    self.assertRaisesRegex(
                        ValueError,
                        "signed and pinned",
                    ),
                ):
                    build_a2a_outbound_attempt_runner(
                        control,
                        publisher=FakePublisher(),
                        http_client=client,
                        trust_policy=policy,
                        credential_provider=StaticCredentialProvider(),
                        runner_id="runner-1",
                        source_agent_url="https://sender.example.test",
                    )
        finally:
            await client.aclose()

        with self.assertRaisesRegex(ValueError, "must be open"):
            build_a2a_outbound_attempt_runner(
                control,
                publisher=FakePublisher(),
                http_client=client,
                trust_policy=strict_policy(),
                credential_provider=StaticCredentialProvider(),
                runner_id="runner-1",
                source_agent_url="https://sender.example.test",
            )

    async def test_attempt_rejects_invalid_lease_before_control_plane_io(self) -> None:
        context = outbound_context()
        control = FakeControl(context)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: None)
        ) as client:
            runner = build_a2a_outbound_attempt_runner(
                control,
                publisher=FakePublisher(),
                http_client=client,
                trust_policy=strict_policy(),
                credential_provider=StaticCredentialProvider(),
                runner_id="runner-1",
                source_agent_url="https://sender.example.test",
            )

            with self.assertRaisesRegex(ValueError, "lease_seconds"):
                await runner.run_claimed(
                    outbound_claim(context),
                    lease_seconds=0,
                )

        self.assertEqual(control.calls, [])

    async def test_factory_preserves_bounded_component_configuration(self) -> None:
        context = outbound_context()
        control = FakeControl(context)
        cases = (
            ({"runner_id": ""}, "runner_id"),
            ({"source_agent_url": "https://sender.example.test/path"}, "origin"),
            ({"max_command_bytes": 0}, "max_request_bytes"),
            ({"card_timeout_seconds": 0}, "timeout_seconds"),
            ({"transport_max_redirects": 11}, "max_redirects"),
            ({"poll_interval_seconds": 0}, "poll_interval_seconds"),
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: None)
        ) as client:
            for updates, message in cases:
                values: dict[str, Any] = {
                    "runner_id": "runner-1",
                    "source_agent_url": "https://sender.example.test",
                    **updates,
                }
                with (
                    self.subTest(updates=updates),
                    self.assertRaisesRegex(
                        ValueError,
                        message,
                    ),
                ):
                    build_a2a_outbound_attempt_runner(
                        control,
                        publisher=FakePublisher(),
                        http_client=client,
                        trust_policy=strict_policy(),
                        credential_provider=StaticCredentialProvider(),
                        **values,
                    )

        self.assertEqual(control.calls, [])

    async def test_resolution_failure_is_sanitized_and_lease_fenced(self) -> None:
        context = outbound_context()
        context["version"] = 2
        control = FakeControl(context)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: None)
        ) as client:
            runner = build_a2a_outbound_attempt_runner(
                control,
                publisher=FakePublisher(),
                http_client=client,
                trust_policy=strict_policy(),
                credential_provider=StaticCredentialProvider(),
                runner_id="runner-1",
                source_agent_url="https://sender.example.test",
            )
            with self.assertRaisesRegex(
                A2AOutboundAttemptError,
                "claimed context resolution failed",
            ):
                await runner.run_claimed(outbound_claim(context))

        self.assertEqual([name for name, _ in control.calls], ["context", "fail"])
        failure = control.calls[-1][1]
        self.assertEqual(failure["lease_id"], "lease-outbound")
        self.assertEqual(
            failure["reason"],
            "outbound A2A resolution failed: ClaimedWorkResolutionError",
        )
        self.assertNotIn("version", failure["reason"])

    async def test_resolution_cancellation_records_failure_and_propagates(self) -> None:
        context = outbound_context()
        control = FakeControl(context)
        control.context_gate = asyncio.Event()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: None)
        ) as client:
            runner = build_a2a_outbound_attempt_runner(
                control,
                publisher=FakePublisher(),
                http_client=client,
                trust_policy=strict_policy(),
                credential_provider=StaticCredentialProvider(),
                runner_id="runner-1",
                source_agent_url="https://sender.example.test",
            )
            task = asyncio.create_task(runner.run_claimed(outbound_claim(context)))
            while not control.calls:
                await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual([name for name, _ in control.calls], ["context", "fail"])
        self.assertEqual(
            control.calls[-1][1]["reason"],
            "outbound A2A resolution canceled",
        )

    async def test_invalid_resolution_failure_response_is_rejected(self) -> None:
        context = outbound_context()
        context["version"] = 2
        control = FakeControl(context)
        control.failure_updates = {"status": "RUNNING"}
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: None)
        ) as client:
            runner = build_a2a_outbound_attempt_runner(
                control,
                publisher=FakePublisher(),
                http_client=client,
                trust_policy=strict_policy(),
                credential_provider=StaticCredentialProvider(),
                runner_id="runner-1",
                source_agent_url="https://sender.example.test",
            )
            with self.assertRaisesRegex(
                A2AOutboundAttemptError,
                "inconsistent outbound failure response",
            ):
                await runner.run_claimed(outbound_claim(context))


if __name__ == "__main__":
    unittest.main()
