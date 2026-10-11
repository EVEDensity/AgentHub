"""Runner identity resolution for the desktop local runner (split module)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.services.runner.settings import (
    DesktopLocalRunnerSettings,
    DesktopRunnerError,
)


@dataclass(frozen=True)
class DesktopRunnerIdentity:
    access_token: str
    user_id: str


class DesktopAuthenticator:
    """Resolve the Runner identity through the existing token mechanisms."""

    def __init__(self, client_factory: Any = None, *, login_timeout_seconds: float = 10.0) -> None:
        if login_timeout_seconds <= 0:
            raise ValueError("login_timeout_seconds must be positive")
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(trust_env=False))
        self._login_timeout_seconds = login_timeout_seconds

    async def resolve(
        self,
        settings: DesktopLocalRunnerSettings,
    ) -> DesktopRunnerIdentity:
        if settings.token_file is not None:
            token = Path(settings.token_file).read_text(encoding="utf-8").strip()
            if not token:
                raise DesktopRunnerError("desktop runner token file is empty")
            assert settings.user_id is not None
            return DesktopRunnerIdentity(
                access_token=token,
                user_id=settings.user_id,
            )
        if settings.token is not None:
            assert settings.user_id is not None
            return DesktopRunnerIdentity(
                access_token=settings.token,
                user_id=settings.user_id,
            )
        return await self._login(settings)

    async def _login(
        self,
        settings: DesktopLocalRunnerSettings,
    ) -> DesktopRunnerIdentity:
        async with self._client_factory() as client:
            response = await self._wait_for_login(client, settings)
        if response.is_error:
            raise DesktopRunnerError(
                f"desktop runner login failed with HTTP {response.status_code}"
            )
        payload = response.json()
        token = payload.get("accessToken") if isinstance(payload, Mapping) else None
        user = payload.get("user") if isinstance(payload, Mapping) else None
        user_id = str(user.get("id", "")) if isinstance(user, Mapping) else ""
        if not isinstance(token, str) or not token or not user_id:
            raise DesktopRunnerError("desktop runner login returned no identity")
        return DesktopRunnerIdentity(access_token=token, user_id=user_id)

    async def _wait_for_login(self, client: Any, settings: DesktopLocalRunnerSettings):
        # A scheduled lifespan task may run before Uvicorn binds its socket.
        # Retry only transport readiness; rejected credentials fail immediately.
        deadline = time.monotonic() + self._login_timeout_seconds
        while True:
            try:
                return await client.post(
                    f"{settings.base_url}/api/auth/login",
                    json={"name": settings.admin_name, "password": settings.admin_password},
                    timeout=max(0.001, deadline - time.monotonic()),
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DesktopRunnerError("desktop runner login server readiness timed out") from exc
                await asyncio.sleep(min(0.1, remaining))
