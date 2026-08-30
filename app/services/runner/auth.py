"""Runner identity resolution for the desktop local runner (split module)."""

from __future__ import annotations

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

    def __init__(self, client_factory: Any = httpx.AsyncClient) -> None:
        self._client_factory = client_factory

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
            response = await client.post(
                f"{settings.base_url}/api/auth/login",
                json={"name": settings.admin_name, "password": settings.admin_password},
            )
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
