from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.services.a2a_outbound_transport import A2AVerifiedPeerRoute

_AGENT_CARD_PATH = "/.well-known/agent-card.json"
_MAX_CARD_REDIRECTS = 3
_MAX_COLLECTION_ITEMS = 256
_MAX_SCHEMA_DEPTH = 32
_MAX_SCHEMA_NODES = 10_000


class A2APeerRouteResolutionError(RuntimeError):
    """Raised when a peer cannot produce one trusted, usable A2A route."""


class _DuplicateJSONField(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class A2AAgentCardTrustPolicy:
    """Origin-bound public-key policy with overlap for key rotation."""

    allow_unsigned_cards: bool = False
    require_pinned_keys: bool = False
    trusted_public_keys: Mapping[str, Sequence[str]] = field(default_factory=dict)
    _pins_by_origin: Mapping[str, frozenset[str]] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if type(self.allow_unsigned_cards) is not bool:
            raise TypeError("allow_unsigned_cards must be a boolean")
        if type(self.require_pinned_keys) is not bool:
            raise TypeError("require_pinned_keys must be a boolean")
        if self.allow_unsigned_cards and self.require_pinned_keys:
            raise ValueError(
                "unsigned Agent Cards and required public-key pins are incompatible"
            )
        if not isinstance(self.trusted_public_keys, Mapping):
            raise TypeError("trusted_public_keys must be a mapping")

        normalized: dict[str, frozenset[str]] = {}
        for raw_origin, raw_keys in self.trusted_public_keys.items():
            if not isinstance(raw_origin, str):
                raise TypeError("trusted Agent origins must be strings")
            origin = _normalize_origin_only(raw_origin, "trusted Agent origin")
            if isinstance(raw_keys, (str, bytes)) or not isinstance(raw_keys, Sequence):
                raise TypeError("trusted Agent public keys must be a sequence")
            keys = frozenset(_normalize_public_key(value) for value in raw_keys)
            if not keys:
                raise ValueError("trusted Agent origins must contain a public key")
            normalized[origin] = normalized.get(origin, frozenset()) | keys
        if self.require_pinned_keys and not normalized:
            raise ValueError("required Agent Card pins cannot be empty")
        object.__setattr__(self, "_pins_by_origin", MappingProxyType(normalized))

    def pins_for(self, origin: str) -> frozenset[str]:
        return self._pins_by_origin.get(origin, frozenset())


class A2AAgentCardRouteResolver:
    """Discover and verify one peer route without retaining peer task state."""

    def __init__(
        self,
        *,
        trust_policy: A2AAgentCardTrustPolicy | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 1 << 20,
        max_redirects: int = _MAX_CARD_REDIRECTS,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        if not 0 <= max_redirects <= 10:
            raise ValueError("max_redirects must be between 0 and 10")
        self._trust_policy = trust_policy or A2AAgentCardTrustPolicy()
        self._http_client = http_client
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects

    async def resolve(
        self,
        target_agent_url: str,
        *,
        required_capabilities: Sequence[str],
    ) -> A2AVerifiedPeerRoute:
        try:
            origin = _http_origin(target_agent_url, "target Agent URL")
            requirements = _normalize_required_capabilities(required_capabilities)
            body = await self._fetch_card(origin)
            card = _decode_agent_card(body)
            _verify_card_identity(card, origin)
            _verify_card_trust(card, origin, self._trust_policy)
            _verify_capabilities(card, requirements)
            task_api_url = _required_string(
                _required_object(card, "endpoints"),
                "taskApi",
            ).strip()
            if _http_origin(task_api_url, "Agent Card task endpoint") != origin:
                raise A2APeerRouteResolutionError(
                    "Agent Card task endpoint crossed the requested Agent origin"
                )
            return A2AVerifiedPeerRoute(
                agent_origin=origin,
                task_api_url=task_api_url,
                requires_bearer=_requires_bearer(card),
            )
        except A2APeerRouteResolutionError:
            raise
        except (TypeError, ValueError) as exc:
            raise A2APeerRouteResolutionError(
                "Agent Card route validation failed"
            ) from exc

    async def _fetch_card(self, origin: str) -> bytes:
        if self._http_client is not None:
            return await self._fetch_card_with_client(self._http_client, origin)
        async with httpx.AsyncClient(follow_redirects=False) as client:
            return await self._fetch_card_with_client(client, origin)

    async def _fetch_card_with_client(
        self,
        client: httpx.AsyncClient,
        origin: str,
    ) -> bytes:
        url = f"{origin}{_AGENT_CARD_PATH}"
        for redirect_count in range(self._max_redirects + 1):
            try:
                async with client.stream(
                    "GET",
                    url,
                    headers={"Accept": "application/json"},
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirect_count >= self._max_redirects:
                            raise A2APeerRouteResolutionError(
                                "Agent Card endpoint exceeded the redirect limit"
                            )
                        location = response.headers.get("Location")
                        if not location:
                            raise A2APeerRouteResolutionError(
                                "Agent Card redirect has no Location"
                            )
                        redirected_url = urljoin(url, location)
                        try:
                            redirected_origin = _http_origin(
                                redirected_url,
                                "Agent Card redirect",
                            )
                        except (TypeError, ValueError) as exc:
                            raise A2APeerRouteResolutionError(
                                "Agent Card redirect is invalid"
                            ) from exc
                        if redirected_origin != origin:
                            raise A2APeerRouteResolutionError(
                                "Agent Card redirect crossed the requested Agent origin"
                            )
                        url = redirected_url
                        continue
                    if not 200 <= response.status_code < 300:
                        raise A2APeerRouteResolutionError(
                            f"Agent Card endpoint returned HTTP {response.status_code}"
                        )
                    return await _read_bounded_response(
                        response,
                        max_bytes=self._max_response_bytes,
                    )
            except A2APeerRouteResolutionError:
                raise
            except httpx.HTTPError as exc:
                raise A2APeerRouteResolutionError(
                    f"Agent Card request failed: {type(exc).__name__}"
                ) from exc
        raise A2APeerRouteResolutionError("Agent Card redirect handling failed")


async def _read_bounded_response(
    response: httpx.Response,
    *,
    max_bytes: int,
) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise A2APeerRouteResolutionError(
                    "Agent Card response exceeds the size limit"
                )
        except ValueError as exc:
            raise A2APeerRouteResolutionError(
                "Agent Card response has an invalid Content-Length"
            ) from exc
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            raise A2APeerRouteResolutionError(
                "Agent Card response exceeds the size limit"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_agent_card(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_int=float,
            parse_float=float,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError("non-finite JSON number")
            ),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as exc:
        raise A2APeerRouteResolutionError("Agent Card is not strict JSON") from exc
    if not isinstance(payload, dict):
        raise A2APeerRouteResolutionError("Agent Card must be a JSON object")
    _canonical_agent_card(payload)
    return payload


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONField("duplicate JSON field")
        result[key] = value
    return result


def _verify_card_identity(card: Mapping[str, Any], origin: str) -> None:
    protocol_version = _required_string(card, "protocolVersion").strip()
    if not protocol_version or protocol_version.split(".", 1)[0] != "1":
        raise A2APeerRouteResolutionError(
            "Agent Card uses an unsupported protocol version"
        )
    if _http_origin(_required_string(card, "url"), "Agent Card URL") != origin:
        raise A2APeerRouteResolutionError(
            "Agent Card URL crossed the requested Agent origin"
        )


def _verify_card_trust(
    card: Mapping[str, Any],
    origin: str,
    policy: A2AAgentCardTrustPolicy,
) -> None:
    signature_value = card.get("signature", "")
    if not isinstance(signature_value, str):
        raise A2APeerRouteResolutionError("Agent Card signature must be a string")
    signature_text = signature_value.strip()
    if not signature_text:
        if not policy.allow_unsigned_cards or policy.require_pinned_keys:
            raise A2APeerRouteResolutionError(
                "unsigned Agent Card rejected by trust policy"
            )
        return

    security = _required_object(card, "security")
    algorithm = _optional_string(security, "key_algorithm").strip().casefold()
    if algorithm not in {"", "ed25519"}:
        raise A2APeerRouteResolutionError("Agent Card signing algorithm is unsupported")
    public_key_text = _normalize_public_key(_required_string(security, "public_key"))
    try:
        signature = bytes.fromhex(signature_text)
    except ValueError as exc:
        raise A2APeerRouteResolutionError(
            "Agent Card Ed25519 signature is invalid"
        ) from exc
    if len(signature) != 64:
        raise A2APeerRouteResolutionError("Agent Card Ed25519 signature is invalid")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_text)).verify(
            signature,
            _agent_card_signing_payload(card),
        )
    except (InvalidSignature, ValueError) as exc:
        raise A2APeerRouteResolutionError(
            "Agent Card signature verification failed"
        ) from exc

    pins = policy.pins_for(origin)
    if pins and public_key_text not in pins:
        raise A2APeerRouteResolutionError(
            "Agent Card public key is not trusted for the requested origin"
        )
    if policy.require_pinned_keys and not pins:
        raise A2APeerRouteResolutionError(
            "no Agent Card public key is trusted for the requested origin"
        )


def _verify_capabilities(
    card: Mapping[str, Any],
    required_capabilities: tuple[str, ...],
) -> None:
    available: set[str] = set()
    for skill in _required_list(card, "skills"):
        if not isinstance(skill, Mapping):
            raise A2APeerRouteResolutionError("Agent Card skill must be an object")
        skill_id = _required_string(skill, "id").strip().casefold()
        if skill_id:
            available.add(skill_id)
        for tag in _required_list(skill, "tags"):
            if not isinstance(tag, str):
                raise A2APeerRouteResolutionError(
                    "Agent Card skill tag must be a string"
                )
            normalized = tag.strip().casefold()
            if normalized:
                available.add(normalized)
    if any(
        capability.casefold() not in available for capability in required_capabilities
    ):
        raise A2APeerRouteResolutionError(
            "Agent Card does not provide every required capability"
        )


def _requires_bearer(card: Mapping[str, Any]) -> bool:
    schemes = card.get("authSchemes", [])
    if not isinstance(schemes, list):
        raise A2APeerRouteResolutionError("Agent Card authSchemes must be an array")
    return any(
        isinstance(scheme, Mapping)
        and isinstance(scheme.get("type"), str)
        and scheme["type"].strip().casefold() == "bearer"
        for scheme in schemes
    )


def _normalize_required_capabilities(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("required_capabilities must be a sequence")
    if len(values) > _MAX_COLLECTION_ITEMS:
        raise ValueError("required_capabilities contains too many values")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError("required capabilities must be strings")
        capability = value.strip()
        key = capability.casefold()
        if not capability or len(capability) > 255 or key in seen:
            raise ValueError("required capabilities must be unique bounded strings")
        normalized.append(capability)
        seen.add(key)
    return tuple(normalized)


def _normalize_public_key(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("Agent Card public key must be a string")
    normalized = value.strip().casefold()
    try:
        decoded = bytes.fromhex(normalized)
    except ValueError as exc:
        raise ValueError("Agent Card Ed25519 public key is invalid") from exc
    if len(decoded) != 32 or len(normalized) != 64:
        raise ValueError("Agent Card Ed25519 public key is invalid")
    return normalized


def _http_origin(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(f"{field_name} must be a credential-free HTTP(S) URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} contains an invalid port") from exc
    host = parsed.hostname.casefold()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = f"{host}:{port}" if port is not None else host
    return urlunsplit((parsed.scheme.casefold(), authority, "", "", ""))


def _normalize_origin_only(value: str, field_name: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.path not in {"", "/"} or parsed.query:
        raise ValueError(f"{field_name} must contain only an origin")
    return _http_origin(value, field_name)


def normalize_a2a_origin(value: str) -> str:
    """Return the canonical exact origin used by A2A trust and credentials."""

    return _normalize_origin_only(value, "Agent origin")


def _agent_card_signing_payload(card: Mapping[str, Any]) -> bytes:
    return _encode_go_json(_canonical_agent_card(card)).encode("utf-8")


def _canonical_agent_card(card: Mapping[str, Any]) -> dict[str, Any]:
    _forbid_extra_fields(
        card,
        {
            "protocolVersion",
            "name",
            "description",
            "url",
            "provider",
            "capabilities",
            "skills",
            "endpoints",
            "authSchemes",
            "version",
            "documentation",
            "iconUrl",
            "tenantId",
            "source",
            "status",
            "lastSeenAt",
            "createdAt",
            "tags",
            "security",
            "signature",
        },
        "Agent Card",
    )
    result: dict[str, Any] = {
        "protocolVersion": _required_string(card, "protocolVersion"),
        "name": _required_string(card, "name"),
        "description": _required_string(card, "description"),
        "url": _required_string(card, "url"),
    }
    if "provider" in card and card["provider"] is not None:
        result["provider"] = _canonical_provider(card["provider"])
    result["capabilities"] = _canonical_capabilities(
        _required_object(card, "capabilities")
    )
    result["skills"] = [
        _canonical_skill(value) for value in _bounded_list(card, "skills")
    ]
    result["endpoints"] = _canonical_endpoints(_required_object(card, "endpoints"))
    auth_schemes = card.get("authSchemes", [])
    if not isinstance(auth_schemes, list):
        raise TypeError("Agent Card authSchemes must be an array")
    if len(auth_schemes) > _MAX_COLLECTION_ITEMS:
        raise ValueError("Agent Card authSchemes contains too many values")
    if auth_schemes:
        result["authSchemes"] = [
            _canonical_auth_scheme(value) for value in auth_schemes
        ]
    for name in (
        "version",
        "documentation",
        "iconUrl",
        "tenantId",
        "source",
        "status",
        "lastSeenAt",
        "createdAt",
    ):
        value = _optional_string(card, name)
        if value:
            result[name] = value
    tags = _string_list(card.get("tags", []), "Agent Card tags")
    if tags:
        result["tags"] = tags
    if "security" in card and card["security"] is not None:
        result["security"] = _canonical_security(card["security"])
    signature = card.get("signature", "")
    if not isinstance(signature, str):
        raise TypeError("Agent Card signature must be a string")
    return result


def _canonical_provider(value: Any) -> dict[str, Any]:
    provider = _as_object(value, "Agent Card provider")
    _forbid_extra_fields(provider, {"name", "url", "organization"}, "provider")
    return _optional_string_fields(provider, ("name", "url", "organization"))


def _canonical_capabilities(value: Mapping[str, Any]) -> dict[str, Any]:
    names = {
        "streaming",
        "pushNotifications",
        "stateTransitionHistory",
        "multimodal",
        "codeExecution",
    }
    _forbid_extra_fields(value, names, "capabilities")
    result = {
        "streaming": _required_bool(value, "streaming"),
        "pushNotifications": _required_bool(value, "pushNotifications"),
        "stateTransitionHistory": _required_bool(value, "stateTransitionHistory"),
    }
    for name in ("multimodal", "codeExecution"):
        optional = value.get(name, False)
        if type(optional) is not bool:
            raise ValueError(f"Agent Card {name} must be a boolean")
        if optional:
            result[name] = True
    return result


def _canonical_skill(value: Any) -> dict[str, Any]:
    skill = _as_object(value, "Agent Card skill")
    _forbid_extra_fields(
        skill,
        {
            "id",
            "name",
            "description",
            "tags",
            "examples",
            "inputSchema",
            "outputSchema",
        },
        "skill",
    )
    result: dict[str, Any] = {
        "id": _required_string(skill, "id"),
        "name": _required_string(skill, "name"),
    }
    description = _optional_string(skill, "description")
    if description:
        result["description"] = description
    result["tags"] = _string_list(_required_list(skill, "tags"), "skill tags")
    examples = _string_list(skill.get("examples", []), "skill examples")
    if examples:
        result["examples"] = examples
    for name in ("inputSchema", "outputSchema"):
        schema = skill.get(name, None)
        if schema is not None:
            schema_object = _as_object(schema, f"skill {name}")
            if schema_object:
                counter = [0]
                result[name] = _canonical_json_object(schema_object, 0, counter)
    return result


def _canonical_endpoints(value: Mapping[str, Any]) -> dict[str, Any]:
    _forbid_extra_fields(value, {"taskApi", "streaming", "webhookUrl"}, "endpoints")
    result = {"taskApi": _required_string(value, "taskApi")}
    result.update(_optional_string_fields(value, ("streaming", "webhookUrl")))
    return result


def _canonical_auth_scheme(value: Any) -> dict[str, Any]:
    scheme = _as_object(value, "Agent Card auth scheme")
    _forbid_extra_fields(
        scheme, {"type", "description", "tokenUrl", "scopes"}, "auth scheme"
    )
    result = {"type": _required_string(scheme, "type")}
    result.update(_optional_string_fields(scheme, ("description", "tokenUrl")))
    scopes = _string_list(scheme.get("scopes", []), "auth scheme scopes")
    if scopes:
        result["scopes"] = scopes
    return result


def _canonical_security(value: Any) -> dict[str, Any]:
    security = _as_object(value, "Agent Card security")
    names = ("public_key", "key_algorithm", "key_id", "key_version")
    _forbid_extra_fields(security, set(names), "security")
    return _optional_string_fields(security, names)


def _canonical_json_object(
    value: Mapping[str, Any],
    depth: int,
    counter: list[int],
) -> dict[str, Any]:
    if depth > _MAX_SCHEMA_DEPTH:
        raise ValueError("Agent Card schema is too deeply nested")
    result: dict[str, Any] = {}
    for key in sorted(value):
        if not isinstance(key, str):
            raise TypeError("Agent Card schema keys must be strings")
        result[key] = _canonical_json_value(value[key], depth + 1, counter)
    return result


def _canonical_json_value(value: Any, depth: int, counter: list[int]) -> Any:
    if depth > _MAX_SCHEMA_DEPTH:
        raise ValueError("Agent Card schema is too deeply nested")
    counter[0] += 1
    if counter[0] > _MAX_SCHEMA_NODES:
        raise ValueError("Agent Card schema contains too many values")
    if value is None or type(value) in {bool, str}:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Agent Card schema contains a non-finite number")
        return value
    if isinstance(value, list):
        return [_canonical_json_value(item, depth + 1, counter) for item in value]
    if isinstance(value, Mapping):
        return _canonical_json_object(value, depth, counter)
    raise ValueError("Agent Card schema contains an unsupported JSON value")


def _encode_go_json(value: Any) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if isinstance(value, str):
        return _encode_go_string(value)
    if isinstance(value, float):
        return _encode_go_float(value)
    if isinstance(value, list):
        return "[" + ",".join(_encode_go_json(item) for item in value) + "]"
    if isinstance(value, Mapping):
        return (
            "{"
            + ",".join(
                f"{_encode_go_string(key)}:{_encode_go_json(item)}"
                for key, item in value.items()
            )
            + "}"
        )
    raise TypeError("unsupported Agent Card signing value")


def _encode_go_string(value: str) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return (
        encoded.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _encode_go_float(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("non-finite Agent Card number")
    if value == 0:
        return "-0" if math.copysign(1.0, value) < 0 else "0"
    absolute = abs(value)
    text = repr(value).lower()
    if 1e-6 <= absolute < 1e21:
        fixed = format(Decimal(text), "f")
        return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed
    mantissa, exponent = text.split("e")
    sign = "+" if exponent.startswith("+") else "-" if exponent.startswith("-") else ""
    digits = exponent.lstrip("+-0") or "0"
    return f"{mantissa}e{sign}{digits}"


def _forbid_extra_fields(
    value: Mapping[str, Any], allowed: set[str], field_name: str
) -> None:
    if any(key not in allowed for key in value):
        raise ValueError(f"{field_name} contains unsupported fields")


def _as_object(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be an object")
    return value


def _required_object(value: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return _as_object(value.get(name), f"Agent Card {name}")


def _required_string(value: Mapping[str, Any], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str):
        raise TypeError(f"Agent Card {name} must be a string")
    return result


def _optional_string(value: Mapping[str, Any], name: str) -> str:
    result = value.get(name, "")
    if not isinstance(result, str):
        raise TypeError(f"Agent Card {name} must be a string")
    return result


def _required_bool(value: Mapping[str, Any], name: str) -> bool:
    result = value.get(name)
    if type(result) is not bool:
        raise ValueError(f"Agent Card {name} must be a boolean")
    return result


def _required_list(value: Mapping[str, Any], name: str) -> list[Any]:
    result = value.get(name)
    if not isinstance(result, list):
        raise TypeError(f"Agent Card {name} must be an array")
    return result


def _bounded_list(value: Mapping[str, Any], name: str) -> list[Any]:
    result = _required_list(value, name)
    if len(result) > _MAX_COLLECTION_ITEMS:
        raise ValueError(f"Agent Card {name} contains too many values")
    return result


def _string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or len(value) > _MAX_COLLECTION_ITEMS:
        raise ValueError(f"Agent Card {field_name} must be a bounded array")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"Agent Card {field_name} must contain strings")
    return list(value)


def _optional_string_fields(
    value: Mapping[str, Any], names: Sequence[str]
) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in names:
        item = _optional_string(value, name)
        if item:
            result[name] = item
    return result


__all__ = [
    "A2AAgentCardRouteResolver",
    "A2AAgentCardTrustPolicy",
    "A2APeerRouteResolutionError",
    "normalize_a2a_origin",
]
