from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.services.a2a_peer_route_service import normalize_a2a_origin

_MAX_BEARER_TOKEN_CHARS = 8_192


class OriginBoundA2ABearerProvider:
    """Keep receiver-issued bearer tokens behind an exact-origin lookup."""

    __slots__ = ("_bearer_by_origin",)

    def __init__(self, bearer_by_origin: Mapping[str, str] | None = None) -> None:
        if bearer_by_origin is None:
            bearer_by_origin = {}
        if not isinstance(bearer_by_origin, Mapping):
            raise TypeError("bearer_by_origin must be a mapping")

        normalized: dict[str, str] = {}
        for raw_origin, token in bearer_by_origin.items():
            if not isinstance(raw_origin, str):
                raise TypeError("A2A credential origins must be strings")
            origin = normalize_a2a_origin(raw_origin)
            if origin in normalized:
                raise ValueError("A2A credentials contain a duplicate Agent origin")
            normalized[origin] = _validated_bearer_token(token)
        self._bearer_by_origin = MappingProxyType(normalized)

    @property
    def count(self) -> int:
        return len(self._bearer_by_origin)

    def bearer_for(self, agent_origin: str) -> str | None:
        """Return a token only for the caller's exact canonical Agent origin."""

        origin = normalize_a2a_origin(agent_origin)
        return self._bearer_by_origin.get(origin)


def _validated_bearer_token(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("A2A bearer tokens must be strings")
    if (
        not value
        or len(value) > _MAX_BEARER_TOKEN_CHARS
        or value != value.strip()
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
    ):
        raise ValueError("A2A bearer token is invalid")
    return value


__all__ = ["OriginBoundA2ABearerProvider"]
