"""Stable error envelope shared by API, CLI, tools, and CI projections.

The envelope is deliberately transport agnostic. Callers may render it as
human text, JSON, or JSONL, but classification happens exactly once here.
Sensitive exception text is never copied into ``details`` automatically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


class ErrorCategory(StrEnum):
    CONFIG = "config"
    AUTH = "auth"
    TRANSPORT = "transport"
    TIMEOUT = "timeout"
    PROTOCOL = "protocol"
    PROVIDER = "provider"
    PERMISSION = "permission"
    VALIDATION = "validation"
    CONFLICT = "conflict"
    EXECUTION = "execution"
    INTERNAL = "internal"


class ConfigError(ValueError):
    """Invalid or unreadable user configuration."""

@dataclass(frozen=True)
class ErrorEnvelope:
    error_type: str
    category: str
    retryable: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = {
            "errorType": self.error_type,
            "category": self.category,
            "retryable": self.retryable,
            "message": self.message,
            "details": dict(self.details),
        }
        # Keep the historical shape byte-for-byte when no operation ID is
        # available, while all Transport-backed failures include this field.
        if self.request_id:
            value["requestId"] = self.request_id
        return value


def _status_code(error: BaseException) -> int | None:
    status = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def error_envelope(
    error: BaseException,
    *,
    message: str | None = None,
    request_id: str = "",
    details: dict[str, Any] | None = None,
) -> ErrorEnvelope:
    """Map an exception to the versioned public error contract.

    ``details`` is caller supplied and should contain identifiers or safe
    metadata only. Exception representations are truncated to keep human and
    machine output bounded.
    """
    safe_message = str(message if message is not None else error).strip()
    safe_message = safe_message[:500] or type(error).__name__
    status = _status_code(error)
    extra = dict(details or {})
    if status is not None:
        extra.setdefault("statusCode", status)
    if status == 401:
        return ErrorEnvelope("authentication_required", ErrorCategory.AUTH, False, safe_message, extra, request_id)
    if status == 403:
        return ErrorEnvelope("permission_denied", ErrorCategory.PERMISSION, False, safe_message, extra, request_id)
    if status == 409:
        return ErrorEnvelope("conflict", ErrorCategory.CONFLICT, False, safe_message, extra, request_id)
    if status in {400, 422}:
        return ErrorEnvelope("invalid_request", ErrorCategory.VALIDATION, False, safe_message, extra, request_id)
    if status == 408:
        return ErrorEnvelope("upstream_timeout", ErrorCategory.TIMEOUT, True, safe_message, extra, request_id)
    if status == 429:
        return ErrorEnvelope("provider_rate_limited", ErrorCategory.PROVIDER, True, safe_message, extra, request_id)
    if status is not None and 500 <= status <= 599:
        return ErrorEnvelope("upstream_unavailable", ErrorCategory.TRANSPORT, True, safe_message, extra, request_id)
    if isinstance(error, ConfigError):
        return ErrorEnvelope("invalid_config", ErrorCategory.CONFIG, False, safe_message, extra, request_id)
    if isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return ErrorEnvelope("timeout", ErrorCategory.TIMEOUT, True, safe_message, extra, request_id)
    if isinstance(error, (httpx.ConnectError, httpx.ReadError, httpx.NetworkError)):
        return ErrorEnvelope("transport_error", ErrorCategory.TRANSPORT, True, safe_message, extra, request_id)
    if isinstance(error, (httpx.DecodingError, ValueError, TypeError, KeyError)):
        return ErrorEnvelope("invalid_request", ErrorCategory.VALIDATION, False, safe_message, extra, request_id)
    if isinstance(error, httpx.HTTPError):
        return ErrorEnvelope("transport_error", ErrorCategory.TRANSPORT, True, safe_message, extra, request_id)
    return ErrorEnvelope("internal_error", ErrorCategory.INTERNAL, False, safe_message, extra, request_id)


def provider_error_matrix(
    error: BaseException | None = None,
    *,
    status_code: int | None = None,
    request_id: str = "",
    retry_after: str | None = None,
) -> dict[str, Any]:
    """Return the stable, redacted provider error contract."""
    response = getattr(error, "response", None) if error is not None else None
    if status_code is None and error is not None:
        status_code = _status_code(error)
    if retry_after is None and response is not None:
        retry_after = str((getattr(response, "headers", {}) or {}).get("retry-after", "")) or None
    if not request_id and response is not None:
        headers = getattr(response, "headers", {}) or {}
        request_id = str(headers.get("x-request-id") or headers.get("request-id") or "")
    if status_code == 429:
        error_type, retryable = "rate_limited", True
    elif status_code in {401, 403}:
        error_type, retryable = ("authentication" if status_code == 401 else "permission_denied"), False
    elif status_code in {408, 504} or isinstance(error, (httpx.TimeoutException, TimeoutError)):
        error_type, retryable = "timeout", True
    elif status_code is not None and 500 <= status_code <= 599:
        error_type, retryable = "upstream_unavailable", True
    elif isinstance(error, (httpx.ConnectError, httpx.ReadError, httpx.NetworkError)):
        error_type, retryable = "transport_error", True
    elif status_code in {400, 422}:
        error_type, retryable = "invalid_request", False
    else:
        error_type, retryable = "provider_error", False
    value: dict[str, Any] = {
        "errorType": error_type,
        "retryable": retryable,
        "statusCode": status_code,
        "requestId": request_id if request_id.startswith("req-") else "<redacted>",
    }
    seconds = _retry_after_seconds(retry_after)
    if seconds is not None:
        value["retryAfterSeconds"] = seconds
    return value


def _retry_after_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return max(0, int(float(value.strip())))
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(0, int((target - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return None

__all__ = ["ConfigError", "ErrorCategory", "ErrorEnvelope", "error_envelope", "provider_error_matrix"]
