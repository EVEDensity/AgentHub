"""Operator-attested limits for one direct, non-streaming provider endpoint."""

from __future__ import annotations

import hashlib
import ipaddress
import math
import re
from dataclasses import asdict, dataclass
from datetime import date

import httpx

from app.domain.mission_budget import checked_amount
from app.services.model_pricing import payload_digest

from .errors import StrictProviderError


def bounded_text(value: object, *, maximum: int = 255) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise StrictProviderError("strict provider requires bounded configuration text")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise StrictProviderError(
            "strict provider configuration contains control characters"
        )
    return value


def _endpoint(value: str) -> str:
    bounded_text(value, maximum=2048)
    invalid = False
    try:
        url = httpx.URL(value)
        loopback = (
            ipaddress.ip_address(url.host).is_loopback
            if url.scheme == "http"
            else False
        )
    except (ValueError, httpx.InvalidURL):
        invalid = True
    if invalid:
        raise StrictProviderError("strict provider endpoint is invalid")
    if (
        url.scheme not in {"https", "http"}
        or not url.host
        or (url.scheme == "http" and not loopback)
    ):
        raise StrictProviderError(
            "strict provider requires HTTPS or a literal loopback HTTP endpoint"
        )
    if (
        url.userinfo
        or url.query
        or url.fragment
        or not url.path.endswith("/chat/completions")
    ):
        raise StrictProviderError(
            "strict provider requires a credential-free chat completions endpoint"
        )
    return str(url)


@dataclass(frozen=True, slots=True, kw_only=True)
class StrictProviderProfile:
    """Explicit server attestation, not a tokenizer or automatic provider proof.

    The input ceiling must bound *all billed input* for this exact model, including
    message/tool framing. The provider must enforce the completion ceiling across
    visible and hidden reasoning tokens. Only that attested scope is supported.
    """

    profile_id: str
    provider: str
    model: str
    endpoint: str
    max_billing_input_tokens: int
    max_completion_tokens: int
    context_ceiling_source: str
    context_ceiling_reviewed_at: str
    operator_verified_context_ceiling: bool
    timeout_seconds: float = 30.0
    max_response_bytes: int = 1_048_576
    max_request_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        for value in (self.profile_id, self.provider, self.model):
            bounded_text(value)
        if self.model.casefold().startswith("mock"):
            raise StrictProviderError(
                "strict provider requires an explicit non-mock model"
            )
        bounded_text(self.context_ceiling_source, maximum=2048)
        if self.operator_verified_context_ceiling is not True:
            raise StrictProviderError(
                "strict provider requires an operator-verified billing input ceiling"
            )
        self._validate_review()
        for value in (
            self.max_billing_input_tokens,
            self.max_completion_tokens,
            self.max_response_bytes,
            self.max_request_bytes,
        ):
            if checked_amount(value) == 0:
                raise StrictProviderError("strict provider limits must be positive")
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (float, int)
        ):
            raise StrictProviderError(
                "strict provider timeout must be finite and positive"
            )
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise StrictProviderError(
                "strict provider timeout must be finite and positive"
            )
        object.__setattr__(self, "endpoint", _endpoint(self.endpoint))

    def _validate_review(self) -> None:
        value = self.context_ceiling_reviewed_at
        invalid = (
            not isinstance(value, str)
            or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None
        )
        if not invalid:
            try:
                date.fromisoformat(value)
            except ValueError:
                invalid = True
        if invalid:
            raise StrictProviderError(
                "strict provider ceiling review requires an ISO calendar date"
            )

    @property
    def endpoint_digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.endpoint.encode()).hexdigest()

    @property
    def digest(self) -> str:
        document = asdict(self)
        document["endpoint"] = self.endpoint_digest
        return payload_digest({"kind": "strict-direct-chat.v1", **document})
