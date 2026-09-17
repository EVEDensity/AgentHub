from __future__ import annotations

import json
import unittest
from collections.abc import Sequence
from typing import Any

import httpx

from app.services.a2a_outbound_runner import (
    A2AOutboundTaskCommand,
    A2ARemoteTaskReference,
    A2ARemoteTaskState,
)
from app.services.a2a_outbound_transport import (
    A2AOutboundTransportError,
    A2ARemoteProtocolError,
    A2AVerifiedPeerRoute,
    StatelessA2AHTTPTransport,
)


class StaticRouteResolver:
    def __init__(self, route: A2AVerifiedPeerRoute) -> None:
        self.route = route
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def resolve(
        self,
        target_agent_url: str,
        *,
        required_capabilities: Sequence[str],
    ) -> A2AVerifiedPeerRoute:
        self.calls.append((target_agent_url, tuple(required_capabilities)))
        return self.route


class StaticCredentialProvider:
    def __init__(self, token: str | None) -> None:
        self.token = token
        self.calls: list[str] = []

    def bearer_for(self, agent_origin: str) -> str | None:
        self.calls.append(agent_origin)
        return self.token


def task_reference() -> A2ARemoteTaskReference:
    return A2ARemoteTaskReference(
        target_agent_url="https://receiver.example.test/a2a",
        source_agent_url="https://sender.example.test",
        workspace_id="workspace-1",
        task_id="remote-task-1",
    )


def peer_route(**updates: Any) -> A2AVerifiedPeerRoute:
    values = {
        "agent_origin": "https://receiver.example.test",
        "task_api_url": "https://receiver.example.test/platform/a2a/inbox",
        "requires_bearer": True,
    }
    values.update(updates)
    return A2AVerifiedPeerRoute(**values)


def response_for(request: httpx.Request, *, status: str = "submitted") -> dict:
    envelope = json.loads(request.content)
    return {
        "jsonrpc": "2.0",
        "result": {"id": "remote-task-1", "status": status},
        "id": envelope["id"],
    }


class StatelessA2AHTTPTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_uses_verified_route_and_receiver_credential(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=response_for(request))

        resolver = StaticRouteResolver(peer_route())
        credentials = StaticCredentialProvider("receiver-token")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = StatelessA2AHTTPTransport(
                resolver,
                credential_provider=credentials,
                http_client=client,
            )
            snapshot = await transport.send(
                A2AOutboundTaskCommand(
                    reference=task_reference(),
                    objective="Build a verified release.",
                    required_capabilities=("code_generation",),
                )
            )

        self.assertEqual(snapshot.task_id, "remote-task-1")
        self.assertEqual(snapshot.state, A2ARemoteTaskState.SUBMITTED)
        self.assertEqual(
            resolver.calls,
            [
                (
                    "https://receiver.example.test/a2a",
                    ("code_generation",),
                )
            ],
        )
        self.assertEqual(credentials.calls, ["https://receiver.example.test"])
        request = requests[0]
        self.assertEqual(
            str(request.url),
            "https://receiver.example.test/platform/a2a/inbox",
        )
        self.assertEqual(request.headers["Authorization"], "Bearer receiver-token")
        envelope = json.loads(request.content)
        self.assertEqual(envelope["jsonrpc"], "2.0")
        self.assertEqual(envelope["method"], "tasks/send")
        self.assertEqual(envelope["params"]["id"], "remote-task-1")
        self.assertNotIn("agentUrl", envelope["params"])
        self.assertNotIn("target", envelope["params"])
        self.assertNotIn("a2a.send", str(envelope))

    async def test_get_and_cancel_are_stateless_content_free_calls(self) -> None:
        methods: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            envelope = json.loads(request.content)
            methods.append(envelope["method"])
            self.assertEqual(
                set(envelope["params"]),
                {"id", "workspaceId", "sourceAgentUrl"},
            )
            return httpx.Response(
                200,
                json=response_for(request, status="working"),
            )

        resolver = StaticRouteResolver(peer_route(requires_bearer=False))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = StatelessA2AHTTPTransport(resolver, http_client=client)
            fetched = await transport.get(task_reference())
            cancelled = await transport.cancel(task_reference())

        self.assertEqual(methods, ["tasks/get", "tasks/cancel"])
        self.assertEqual(fetched.state, A2ARemoteTaskState.WORKING)
        self.assertEqual(cancelled.state, A2ARemoteTaskState.WORKING)
        self.assertEqual(
            resolver.calls,
            [
                ("https://receiver.example.test/a2a", ()),
                ("https://receiver.example.test/a2a", ()),
            ],
        )

    async def test_get_result_requires_completed_exact_task(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            result = response_for(request, status="completed")
            result["result"].update(
                {
                    "artifacts": [{"artifactId": "remote-artifact", "parts": []}],
                    "evidence": [{"evidenceId": "remote-evidence"}],
                }
            )
            return httpx.Response(200, json=result)

        resolver = StaticRouteResolver(peer_route(requires_bearer=False))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = StatelessA2AHTTPTransport(resolver, http_client=client)
            result = await transport.get_result(task_reference())

        self.assertEqual(result["id"], "remote-task-1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["artifacts"][0]["artifactId"], "remote-artifact")

    async def test_get_result_rejects_non_completed_task(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response_for(request, status="working"))

        resolver = StaticRouteResolver(peer_route(requires_bearer=False))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = StatelessA2AHTTPTransport(resolver, http_client=client)
            with self.assertRaisesRegex(
                A2ARemoteProtocolError,
                "requires a completed task",
            ):
                await transport.get_result(task_reference())

    async def test_required_bearer_fails_closed_without_calling_peer(self) -> None:
        peer_called = False

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal peer_called
            peer_called = True
            return httpx.Response(500)

        resolver = StaticRouteResolver(peer_route())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = StatelessA2AHTTPTransport(resolver, http_client=client)
            with self.assertRaisesRegex(
                A2AOutboundTransportError,
                "receiver-issued credential",
            ):
                await transport.get(task_reference())

        self.assertFalse(peer_called)

    async def test_resolver_cannot_change_the_requested_peer_origin(self) -> None:
        resolver = StaticRouteResolver(
            A2AVerifiedPeerRoute(
                agent_origin="https://other.example.test",
                task_api_url="https://other.example.test/inbox",
            )
        )
        transport = StatelessA2AHTTPTransport(resolver)

        with self.assertRaisesRegex(
            A2AOutboundTransportError,
            "does not match the requested Agent origin",
        ):
            await transport.get(task_reference())

    async def test_same_origin_307_is_followed_but_cross_origin_is_rejected(self) -> None:
        requests: list[str] = []

        def same_origin_handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.url.path == "/platform/a2a/inbox":
                return httpx.Response(307, headers={"Location": "/a2a/tasks"})
            return httpx.Response(200, json=response_for(request))

        resolver = StaticRouteResolver(peer_route(requires_bearer=False))
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(same_origin_handler)
        ) as client:
            transport = StatelessA2AHTTPTransport(resolver, http_client=client)
            snapshot = await transport.get(task_reference())

        self.assertEqual(snapshot.state, A2ARemoteTaskState.SUBMITTED)
        self.assertEqual(
            requests,
            [
                "https://receiver.example.test/platform/a2a/inbox",
                "https://receiver.example.test/a2a/tasks",
            ],
        )

        def cross_origin_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                307,
                headers={"Location": "https://attacker.example.test/tasks"},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(cross_origin_handler)
        ) as client:
            transport = StatelessA2AHTTPTransport(resolver, http_client=client)
            with self.assertRaisesRegex(
                A2AOutboundTransportError,
                "crossed the verified Agent origin",
            ):
                await transport.get(task_reference())

        def invalid_location_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(307, headers={"Location": "http://[invalid"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(invalid_location_handler)
        ) as client:
            transport = StatelessA2AHTTPTransport(resolver, http_client=client)
            with self.assertRaisesRegex(
                A2AOutboundTransportError,
                "invalid Location",
            ):
                await transport.get(task_reference())

    async def test_response_contract_rejects_protocol_drift_and_remote_errors(
        self,
    ) -> None:
        cases = (
            (
                "mismatched id",
                lambda request: {
                    **response_for(request),
                    "id": "other-request",
                },
                "response id",
            ),
            (
                "mismatched task",
                lambda request: {
                    **response_for(request),
                    "result": {"id": "other-task", "status": "submitted"},
                },
                "task id",
            ),
            (
                "unknown status",
                lambda request: response_for(request, status="invented"),
                "unsupported status",
            ),
            (
                "remote error",
                lambda request: {
                    "jsonrpc": "2.0",
                    "error": {"code": -32005, "message": "capability denied"},
                    "id": json.loads(request.content)["id"],
                },
                "remote A2A error -32005",
            ),
        )
        for name, response_factory, message in cases:
            with self.subTest(name=name):
                def handler(
                    request: httpx.Request,
                    factory=response_factory,
                ) -> httpx.Response:
                    return httpx.Response(200, json=factory(request))

                resolver = StaticRouteResolver(peer_route(requires_bearer=False))
                async with httpx.AsyncClient(
                    transport=httpx.MockTransport(handler)
                ) as client:
                    transport = StatelessA2AHTTPTransport(
                        resolver,
                        http_client=client,
                    )
                    with self.assertRaisesRegex(A2ARemoteProtocolError, message):
                        await transport.get(task_reference())

    async def test_response_body_is_bounded_before_json_parsing(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"x" * 65)

        resolver = StaticRouteResolver(peer_route(requires_bearer=False))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = StatelessA2AHTTPTransport(
                resolver,
                http_client=client,
                max_response_bytes=64,
            )
            with self.assertRaisesRegex(
                A2AOutboundTransportError,
                "response exceeds",
            ):
                await transport.get(task_reference())

    async def test_duplicate_json_fields_are_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            request_id = json.loads(request.content)["id"]
            body = (
                '{"jsonrpc":"2.0","result":'
                '{"id":"remote-task-1","status":"submitted"},'
                f'"id":"{request_id}","id":"{request_id}"}}'
            )
            return httpx.Response(200, content=body.encode("utf-8"))

        resolver = StaticRouteResolver(peer_route(requires_bearer=False))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = StatelessA2AHTTPTransport(resolver, http_client=client)
            with self.assertRaisesRegex(
                A2ARemoteProtocolError,
                "not valid JSON",
            ):
                await transport.get(task_reference())


if __name__ == "__main__":
    unittest.main()
