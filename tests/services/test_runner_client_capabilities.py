"""Capability advertisements are explicit and preserve legacy claim requests."""
from __future__ import annotations

import json

import httpx
import pytest

from app.services.runner_client import MissionControlRunnerClient


@pytest.mark.asyncio
@pytest.mark.parametrize("capabilities", [None, (), ("file.read", "file.write")])
async def test_optional_capabilities_are_transmitted_only_when_explicit(capabilities):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"claimStatus": "idle", "workUnit": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        control = MissionControlRunnerClient(
            "http://mission-control.test", access_token="runner-token", http_client=client,
        )
        arguments = {
            "runner_id": "client-cannot-select-the-authenticated-identity",
            "agent_id": "executor", "adapter_type": "function-calling",
            "supported_work_unit_kinds": ("desktop.task",), "lease_seconds": 60,
        }
        if capabilities is not None:
            arguments["supported_capabilities"] = capabilities
        result = await control.claim_ready_work_unit("workspace", **arguments)

    assert result == {"claimStatus": "idle", "workUnit": None}
    request = requests[0]
    assert request.url.path == "/api/v1/missions/work-unit-claims"
    assert request.headers["Authorization"] == "Bearer runner-token"
    expected = {
        "workspaceId": "workspace", "agentId": "executor", "adapterType": "function-calling",
        "supportedWorkUnitKinds": ["desktop.task"], "leaseSeconds": 60,
    }
    if capabilities:
        expected["supportedCapabilities"] = list(capabilities)
    assert json.loads(request.content) == expected
