from __future__ import annotations

import json
import math
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.services.a2a_outbound_runner import (
    A2AOutboundTaskCommand,
    A2ARemoteTaskReference,
    A2ARemoteTaskSnapshot,
    A2ARemoteTaskState,
)

_JSON_RPC_VERSION = "2.0"
_MAX_BEARER_TOKEN_CHARS = 8_192


class A2AOutboundTransportError(RuntimeError):
    """Base error for a stateless outbound A2A protocol call."""


class A2ARemoteProtocolError(A2AOutboundTransportError):
    """Raised when a peer response violates the bounded A2A contract."""


@dataclass(frozen=True, slots=True)
class A2AVerifiedPeerRoute:
    """Agent Card output after trust and capability verification."""

    agent_origin: str
    task_api_url: str
    requires_bearer: bool = False

    def __post_init__(self) -> None:
        normalized_origin = _normalize_http_origin(
            self.agent_origin,
            "agent_origin",
        )
        task_origin = _http_origin(self.task_api_url, "task_api_url")
        if task_origin != normalized_origin:
            raise ValueError("task_api_url must use the verified Agent origin")
        if type(self.requires_bearer) is not bool:
            raise TypeError("requires_bearer must be a boolean")
        object.__setattr__(self, "agent_origin", normalized_origin)


class A2APeerRouteResolverPort(Protocol):
    """Resolve a trust-checked Agent Card route without exposing its internals."""

    async def resolve(
        self,
        target_agent_url: str,
        *,
        required_capabilities: Sequence[str],
    ) -> A2AVerifiedPeerRoute: ...


class A2APeerCredentialProviderPort(Protocol):
    """Return only the receiver-issued token bound to one exact peer origin."""

    def bearer_for(self, agent_origin: str) -> str | None: ...


class StatelessA2AHTTPTransport:
    """Perform bounded A2A calls without retaining remote task state."""

    def __init__(
        self,
        route_resolver: A2APeerRouteResolverPort,
        *,
        credential_provider: A2APeerCredentialProviderPort | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
        max_request_bytes: int = 64 * 1_024,
        max_response_bytes: int = 1 << 20,
        max_redirects: int = 3,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if max_request_bytes < 1:
            raise ValueError("max_request_bytes must be positive")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        if not 0 <= max_redirects <= 10:
            raise ValueError("max_redirects must be between 0 and 10")
        self._route_resolver = route_resolver
        self._credential_provider = credential_provider
        self._http_client = http_client
        self._timeout_seconds = timeout_seconds
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects

    async def send(
        self,
        command: A2AOutboundTaskCommand,
    ) -> A2ARemoteTaskSnapshot:
        if not isinstance(command, A2AOutboundTaskCommand):
            raise TypeError("command must be an A2AOutboundTaskCommand")
        return await self._invoke(
            "tasks/send",
            command.reference,
            params=command.to_send_params(),
            required_capabilities=command.required_capabilities,
        )

    async def get(
        self,
        reference: A2ARemoteTaskReference,
    ) -> A2ARemoteTaskSnapshot:
        return await self._invoke_reference("tasks/get", reference)

    async def cancel(
        self,
        reference: A2ARemoteTaskReference,
    ) -> A2ARemoteTaskSnapshot:
        return await self._invoke_reference("tasks/cancel", reference)

    async def _invoke_reference(
        self,
        method: str,
        reference: A2ARemoteTaskReference,
    ) -> A2ARemoteTaskSnapshot:
        if not isinstance(reference, A2ARemoteTaskReference):
            raise TypeError("reference must be an A2ARemoteTaskReference")
        return await self._invoke(
            method,
            reference,
            params={
                "id": reference.task_id,
                "workspaceId": reference.workspace_id,
                "sourceAgentUrl": reference.source_agent_url,
            },
            required_capabilities=(),
        )

    async def _invoke(
        self,
        method: str,
        reference: A2ARemoteTaskReference,
        *,
        params: Mapping[str, Any],
        required_capabilities: Sequence[str],
    ) -> A2ARemoteTaskSnapshot:
        try:
            route = await self._route_resolver.resolve(
                reference.target_agent_url,
                required_capabilities=required_capabilities,
            )
        except Exception as exc:
            raise A2AOutboundTransportError(
                f"A2A peer route resolution failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(route, A2AVerifiedPeerRoute):
            raise A2AOutboundTransportError(
                "A2A peer route resolver returned an invalid route"
            )
        expected_origin = _http_origin(
            reference.target_agent_url,
            "target_agent_url",
        )
        if route.agent_origin != expected_origin:
            raise A2AOutboundTransportError(
                "verified A2A peer route does not match the requested Agent origin"
            )

        headers = {"Content-Type": "application/json"}
        if route.requires_bearer:
            token = self._resolve_bearer(route.agent_origin)
            headers["Authorization"] = f"Bearer {token}"

        request_id = secrets.token_hex(16)
        envelope = {
            "jsonrpc": _JSON_RPC_VERSION,
            "method": method,
            "params": dict(params),
            "id": request_id,
        }
        body = json.dumps(
            envelope,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(body) > self._max_request_bytes:
            raise A2AOutboundTransportError("A2A request exceeds the size limit")

        status_code, response_body = await self._post(
            route,
            headers=headers,
            body=body,
        )
        payload = _parse_response(response_body, request_id=request_id)
        if not 200 <= status_code < 300:
            raise A2ARemoteProtocolError(
                f"remote A2A endpoint returned HTTP {status_code} without an error"
            )
        return _parse_task_snapshot(payload, expected_task_id=reference.task_id)

    def _resolve_bearer(self, agent_origin: str) -> str:
        if self._credential_provider is None:
            raise A2AOutboundTransportError(
                "verified A2A peer requires a receiver-issued credential"
            )
        try:
            token = self._credential_provider.bearer_for(agent_origin)
        except Exception as exc:
            raise A2AOutboundTransportError(
                f"A2A peer credential resolution failed: {type(exc).__name__}"
            ) from exc
        if (
            not isinstance(token, str)
            or not token
            or len(token) > _MAX_BEARER_TOKEN_CHARS
            or token != token.strip()
            or any(not 0x21 <= ord(character) <= 0x7E for character in token)
        ):
            raise A2AOutboundTransportError(
                "verified A2A peer has no valid receiver-issued credential"
            )
        return token

    async def _post(
        self,
        route: A2AVerifiedPeerRoute,
        *,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[int, bytes]:
        if self._http_client is not None:
            return await self._post_with_client(
                self._http_client,
                route,
                headers=headers,
                body=body,
            )
        async with httpx.AsyncClient() as client:
            return await self._post_with_client(
                client,
                route,
                headers=headers,
                body=body,
            )

    async def _post_with_client(
        self,
        client: httpx.AsyncClient,
        route: A2AVerifiedPeerRoute,
        *,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[int, bytes]:
        url = route.task_api_url
        for redirect_count in range(self._max_redirects + 1):
            try:
                async with client.stream(
                    "POST",
                    url,
                    headers=headers,
                    content=body,
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                ) as response:
                    if response.status_code in {307, 308}:
                        if redirect_count >= self._max_redirects:
                            raise A2AOutboundTransportError(
                                "remote A2A endpoint exceeded the redirect limit"
                            )
                        location = response.headers.get("Location")
                        if not location:
                            raise A2AOutboundTransportError(
                                "remote A2A redirect has no Location"
                            )
                        try:
                            redirected_url = urljoin(url, location)
                            redirect_origin = _http_origin(
                                redirected_url,
                                "redirect_url",
                            )
                        except ValueError as exc:
                            raise A2AOutboundTransportError(
                                "remote A2A redirect has an invalid Location"
                            ) from exc
                        if redirect_origin != route.agent_origin:
                            raise A2AOutboundTransportError(
                                "remote A2A redirect crossed the verified Agent origin"
                            )
                        url = redirected_url
                        continue
                    if 300 <= response.status_code < 400:
                        raise A2AOutboundTransportError(
                            "remote A2A endpoint returned an unsafe redirect status"
                        )
                    response_body = await _read_bounded_response(
                        response,
                        max_bytes=self._max_response_bytes,
                    )
                    return response.status_code, response_body
            except A2AOutboundTransportError:
                raise
            except httpx.HTTPError as exc:
                raise A2AOutboundTransportError(
                    f"remote A2A request failed: {type(exc).__name__}"
                ) from exc
        raise A2AOutboundTransportError("remote A2A redirect handling failed")


async def _read_bounded_response(
    response: httpx.Response,
    *,
    max_bytes: int,
) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise A2AOutboundTransportError("remote A2A response exceeds the size limit")
    return bytes(body)


def _parse_response(body: bytes, *, request_id: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(body, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError) as exc:
        raise A2ARemoteProtocolError("remote A2A response is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise A2ARemoteProtocolError("remote A2A response must be a JSON object")
    if set(payload) - {"jsonrpc", "result", "error", "id"}:
        raise A2ARemoteProtocolError("remote A2A response has unknown envelope fields")
    if payload.get("jsonrpc") != _JSON_RPC_VERSION:
        raise A2ARemoteProtocolError("remote A2A response has unsupported JSON-RPC version")
    if payload.get("id") != request_id:
        raise A2ARemoteProtocolError("remote A2A response id does not match the request")
    has_result = "result" in payload and payload["result"] is not None
    has_error = "error" in payload and payload["error"] is not None
    if has_result == has_error:
        raise A2ARemoteProtocolError(
            "remote A2A response must contain exactly one result or error"
        )
    if has_error:
        error = payload["error"]
        if not isinstance(error, Mapping):
            raise A2ARemoteProtocolError("remote A2A error must be an object")
        code = error.get("code")
        message = error.get("message")
        if type(code) is not int or not isinstance(message, str) or not message.strip():
            raise A2ARemoteProtocolError("remote A2A error has invalid code or message")
        if len(message) > 2_000:
            raise A2ARemoteProtocolError("remote A2A error message exceeds the size limit")
        raise A2ARemoteProtocolError(f"remote A2A error {code}: {message}")
    result = payload["result"]
    if not isinstance(result, Mapping):
        raise A2ARemoteProtocolError("remote A2A result must be an object")
    return result


def _parse_task_snapshot(
    result: Mapping[str, Any],
    *,
    expected_task_id: str,
) -> A2ARemoteTaskSnapshot:
    task_id = result.get("id")
    if task_id != expected_task_id:
        raise A2ARemoteProtocolError("remote A2A task id does not match the request")
    try:
        state = A2ARemoteTaskState(result.get("status"))
        return A2ARemoteTaskSnapshot(task_id=task_id, state=state)
    except (TypeError, ValueError) as exc:
        raise A2ARemoteProtocolError(
            "remote A2A task has an unsupported status"
        ) from exc


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _http_origin(value: str, field: str) -> str:
    parsed = _validated_http_url(value, field)
    host = parsed.hostname
    assert host is not None
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = host.lower()
    if parsed.port is not None:
        authority = f"{authority}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), authority, "", "", ""))


def _normalize_http_origin(value: str, field: str) -> str:
    parsed = _validated_http_url(value, field)
    if parsed.path not in {"", "/"} or parsed.query:
        raise ValueError(f"{field} must contain only an HTTP(S) origin")
    return _http_origin(value, field)


def _validated_http_url(value: str, field: str):
    if not isinstance(value, str) or not value.strip() or len(value) > 2_048:
        raise ValueError(f"{field} must contain a bounded absolute HTTP(S) URL")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field} has an invalid port") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65_535)
    ):
        raise ValueError(f"{field} must be a credential-free HTTP(S) URL")
    return parsed


__all__ = [
    "A2AOutboundTransportError",
    "A2APeerCredentialProviderPort",
    "A2APeerRouteResolverPort",
    "A2ARemoteProtocolError",
    "A2AVerifiedPeerRoute",
    "StatelessA2AHTTPTransport",
]
