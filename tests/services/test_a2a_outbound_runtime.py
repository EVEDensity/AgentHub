from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from typing import Any

import httpx

from app.services.a2a_peer_credentials import OriginBoundA2ABearerProvider
from app.services.artifact_store_service import PublishedArtifact
from services.python.runner_service.a2a_peers import LoadedA2ARunnerPeers
from services.python.runner_service.outbound_runtime import (
    compose_a2a_outbound_runtime_candidate,
)
from tests.services.test_a2a_outbound_composition import strict_policy


class _UnusedPublisher:
    async def publish_bytes(self, content: bytes) -> PublishedArtifact:
        raise AssertionError(f"idle runtime published {len(content)} bytes")

    async def publish_file(self, path: Path) -> PublishedArtifact:
        raise AssertionError(f"idle runtime published {path}")


class _IdleControl:
    def __init__(self) -> None:
        self.called = asyncio.Event()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def claim_ready_work_unit(
        self,
        workspace_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append((workspace_id, kwargs))
        self.called.set()
        return {"claimStatus": "idle", "workUnit": None}


class _BlockingControl:
    def __init__(self, peer_http: httpx.AsyncClient) -> None:
        self._peer_http = peer_http
        self.started = asyncio.Event()
        self.cancelled = False
        self.peer_was_open_when_cancelled = False

    async def claim_ready_work_unit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            self.peer_was_open_when_cancelled = not self._peer_http.is_closed
            raise
        raise AssertionError("unreachable")


class _CloseRecorder:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _loaded_peers() -> LoadedA2ARunnerPeers:
    return LoadedA2ARunnerPeers(
        trust_policy=strict_policy(),
        credential_provider=OriginBoundA2ABearerProvider(
            {"https://receiver.example.test": "receiver-issued-token"}
        ),
    )


def _reject_peer_request(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected peer request: {request.url}")


class A2AOutboundRuntimeCandidateTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_poll_uses_exact_binding_and_closes_owned_resources(
        self,
    ) -> None:
        control = _IdleControl()
        control_resource = _CloseRecorder()
        peer_http = httpx.AsyncClient(
            transport=httpx.MockTransport(_reject_peer_request)
        )
        runtime = compose_a2a_outbound_runtime_candidate(
            control,
            publisher=_UnusedPublisher(),
            peer_http=peer_http,
            peers=_loaded_peers(),
            runner_id="runner-1",
            workspace_id="workspace-1",
            assigned_agent_id="outbound-dispatcher",
            source_agent_url="https://sender.example.test",
            owned_closeables=(control_resource,),
            idle_delay_seconds=1,
            shutdown_timeout_seconds=1,
        )

        await runtime.start()
        await asyncio.wait_for(control.called.wait(), timeout=1)
        self.assertTrue(runtime.ready)
        await runtime.stop()

        self.assertEqual(
            control.calls,
            [
                (
                    "workspace-1",
                    {
                        "runner_id": "runner-1",
                        "agent_id": "outbound-dispatcher",
                        "adapter_type": "a2a.outbound",
                        "lease_seconds": 300,
                    },
                )
            ],
        )
        self.assertTrue(control_resource.closed)
        self.assertTrue(peer_http.is_closed)
        self.assertFalse(runtime.healthy)

    async def test_shutdown_deadline_cancels_claim_before_closing_peer_http(
        self,
    ) -> None:
        peer_http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None))
        control = _BlockingControl(peer_http)
        runtime = compose_a2a_outbound_runtime_candidate(
            control,
            publisher=_UnusedPublisher(),
            peer_http=peer_http,
            peers=_loaded_peers(),
            runner_id="runner-1",
            workspace_id="workspace-1",
            assigned_agent_id="outbound-dispatcher",
            source_agent_url="https://sender.example.test",
            shutdown_timeout_seconds=0.01,
        )

        await runtime.start()
        await asyncio.wait_for(control.started.wait(), timeout=1)
        await runtime.stop()

        self.assertTrue(control.cancelled)
        self.assertTrue(control.peer_was_open_when_cancelled)
        self.assertTrue(peer_http.is_closed)
        self.assertFalse(runtime.healthy)

    async def test_rejects_closed_or_duplicate_owned_peer_client(self) -> None:
        peer_http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None))
        await peer_http.aclose()
        with self.assertRaisesRegex(ValueError, "must be open"):
            compose_a2a_outbound_runtime_candidate(
                _IdleControl(),
                publisher=_UnusedPublisher(),
                peer_http=peer_http,
                peers=_loaded_peers(),
                runner_id="runner-1",
                workspace_id="workspace-1",
                assigned_agent_id="outbound-dispatcher",
                source_agent_url="https://sender.example.test",
            )

        open_peer = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None))
        try:
            with self.assertRaisesRegex(ValueError, "must be unique"):
                compose_a2a_outbound_runtime_candidate(
                    _IdleControl(),
                    publisher=_UnusedPublisher(),
                    peer_http=open_peer,
                    peers=_loaded_peers(),
                    runner_id="runner-1",
                    workspace_id="workspace-1",
                    assigned_agent_id="outbound-dispatcher",
                    source_agent_url="https://sender.example.test",
                    owned_closeables=(open_peer,),
                )
        finally:
            await open_peer.aclose()


if __name__ == "__main__":
    unittest.main()
