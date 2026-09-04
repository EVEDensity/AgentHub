"""Stable error taxonomy shared by CLI transport and renderers."""

from __future__ import annotations

import httpx
from enum import StrEnum


class CliErrorKind(StrEnum):
    AUTH = "auth"
    TRANSPORT = "transport"
    TIMEOUT = "timeout"
    PROTOCOL = "protocol"
    PROVIDER = "provider"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


def classify_error(error: BaseException) -> CliErrorKind:
    if isinstance(error, (httpx.ConnectError, httpx.ReadError, httpx.NetworkError)):
        return CliErrorKind.TRANSPORT
    if isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return CliErrorKind.TIMEOUT
    if isinstance(error, (ValueError, TypeError)):
        return CliErrorKind.PROTOCOL
    status = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    if status in {401, 403}:
        return CliErrorKind.AUTH
    if status == 409:
        return CliErrorKind.CONFLICT
    if status == 429:
        return CliErrorKind.PROVIDER
    if isinstance(status, int) and 500 <= status <= 599:
        return CliErrorKind.TRANSPORT
    return CliErrorKind.UNKNOWN


__all__ = ["CliErrorKind", "classify_error"]
