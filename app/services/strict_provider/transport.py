"""One owned HTTP request: no retry, redirects, proxy inheritance or raw errors."""

from __future__ import annotations

import httpx

from .deadline import ensure_active
from .errors import StrictProviderError
from .profile import StrictProviderProfile
from .wire import PreparedRequest


async def send_once(
    profile: StrictProviderProfile,
    request: PreparedRequest,
    api_key: str,
    *,
    deadline: float,
) -> bytes:
    failure = False
    content = bytearray()
    try:
        transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        async with httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=request.timeout_seconds,
        ) as client:
            ensure_active(deadline)
            async with client.stream(
                "POST",
                profile.endpoint,
                content=request.body,
                headers={
                    "authorization": "Bearer " + api_key,
                    "content-type": "application/json",
                    "accept": "application/json",
                    "accept-encoding": "identity",
                },
            ) as response:
                media_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                encoding = response.headers.get("content-encoding", "").strip().lower()
                if (
                    response.status_code != 200
                    or media_type != "application/json"
                    or encoding not in {"", "identity"}
                ):
                    failure = True
                else:
                    async for chunk in response.aiter_raw(
                        chunk_size=min(65_536, profile.max_response_bytes + 1)
                    ):
                        content.extend(chunk)
                        if len(content) > profile.max_response_bytes:
                            failure = True
                            break
    except httpx.HTTPError:
        failure = True
    # Raise outside the handler so no HTTPX exception/request with Authorization
    # remains reachable through __context__, __cause__ or formatted traceback.
    if failure:
        raise StrictProviderError("strict provider HTTP result is unavailable")
    return bytes(content)
