import httpx
import pytest

from app.services.runner.auth import DesktopAuthenticator
from app.services.runner.settings import DesktopLocalRunnerSettings, DesktopRunnerError


def authenticator(handler, *, timeout=1):
    return DesktopAuthenticator(
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        login_timeout_seconds=timeout,
    )


@pytest.mark.asyncio
async def test_socket_readiness_retries_then_returns_authenticated_identity():
    attempts = []

    async def handler(request):
        attempts.append(request)
        if len(attempts) < 3:
            raise httpx.ConnectError("listener not bound", request=request)
        return httpx.Response(200, json={"accessToken": "test-token", "user": {"id": "runner-user"}})

    identity = await authenticator(handler).resolve(DesktopLocalRunnerSettings.from_env({}))
    assert identity.user_id == "runner-user"
    assert identity.access_token == "test-token"
    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_http_credential_rejection_is_not_retried():
    attempts = []

    async def handler(request):
        attempts.append(request)
        return httpx.Response(401)

    with pytest.raises(DesktopRunnerError, match="HTTP 401"):
        await authenticator(handler).resolve(DesktopLocalRunnerSettings.from_env({}))
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_socket_never_ready_fails_with_a_bounded_explicit_error():
    async def handler(request):
        raise httpx.ConnectError("listener not bound", request=request)

    with pytest.raises(DesktopRunnerError, match="readiness timed out"):
        await authenticator(handler, timeout=0.01).resolve(DesktopLocalRunnerSettings.from_env({}))


@pytest.mark.asyncio
async def test_malformed_identity_cannot_create_a_runner():
    async def handler(request):
        return httpx.Response(200, json={"accessToken": "", "user": {"id": "runner-user"}})

    with pytest.raises(DesktopRunnerError, match="no identity"):
        await authenticator(handler).resolve(DesktopLocalRunnerSettings.from_env({}))
