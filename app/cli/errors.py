"""CLI error taxonomy and projections for human, JSON, and CI callers."""

from __future__ import annotations

import httpx

from app.errors import ErrorCategory, ErrorEnvelope, error_envelope, provider_error_matrix
from enum import StrEnum


class CliErrorKind(StrEnum):
    CONFIG = "config"
    AUTH = "auth"
    TRANSPORT = "transport"
    TIMEOUT = "timeout"
    PROTOCOL = "protocol"
    PROVIDER = "provider"
    CONFLICT = "conflict"
    PERMISSION = "permission"
    VALIDATION = "validation"
    EXECUTION = "execution"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


def classify_error(error: BaseException) -> CliErrorKind:
    if isinstance(error, (httpx.ConnectError, httpx.ReadError, httpx.NetworkError)):
        return CliErrorKind.TRANSPORT
    if isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return CliErrorKind.TIMEOUT
    if isinstance(error, (ValueError, TypeError)):
        # Preserve the historical CLI kind for callers that use it as a
        # protocol/JSON parsing signal; ``to_error_envelope`` exposes the
        # normative validation category.
        return CliErrorKind.PROTOCOL
    status = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    if status == 401:
        return CliErrorKind.AUTH
    if status == 403:
        return CliErrorKind.PERMISSION
    if status == 409:
        return CliErrorKind.CONFLICT
    if status in {400, 422}:
        return CliErrorKind.VALIDATION
    if status == 408:
        return CliErrorKind.TIMEOUT
    if status == 429:
        return CliErrorKind.PROVIDER
    if isinstance(status, int) and 500 <= status <= 599:
        return CliErrorKind.TRANSPORT
    return CliErrorKind.UNKNOWN


def to_error_envelope(
    error: BaseException,
    *,
    request_id: str = "",
    message: str | None = None,
    details: dict[str, object] | None = None,
) -> ErrorEnvelope:
    """Project any CLI exception through the canonical application mapper."""
    return error_envelope(
        error,
        request_id=request_id,
        message=message,
        details=details,
    )


# The Mission result codes remain backward-compatible. Error exits follow the
# production CLI specification and are only used for classified failures.
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_PERMISSION = 3
EXIT_TIMEOUT = 4
EXIT_INFRASTRUCTURE = 70


def error_exit_code(envelope: ErrorEnvelope) -> int:
    """Map a classified envelope to the stable production CLI exit code."""
    category = str(envelope.category)
    if category == ErrorCategory.PERMISSION:
        return EXIT_PERMISSION
    if category == ErrorCategory.TIMEOUT:
        return EXIT_TIMEOUT
    if category in {ErrorCategory.VALIDATION, ErrorCategory.CONFIG}:
        return EXIT_USAGE
    if category in {ErrorCategory.PROVIDER, ErrorCategory.EXECUTION}:
        return EXIT_FAILURE
    return EXIT_INFRASTRUCTURE


__all__ = [
    "CliErrorKind", "ErrorCategory", "ErrorEnvelope", "classify_error",
    "to_error_envelope", "error_exit_code", "EXIT_SUCCESS", "EXIT_FAILURE",
    "EXIT_USAGE", "EXIT_PERMISSION", "EXIT_TIMEOUT", "EXIT_INFRASTRUCTURE",
    "provider_error_matrix",
]
