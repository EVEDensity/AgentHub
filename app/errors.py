"""Stable error envelope shared by API, CLI, tools, and CI projections."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class ErrorEnvelope:
    error_type: str
    category: str
    retryable: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"errorType": self.error_type, "category": self.category,
                "retryable": self.retryable, "message": self.message,
                "details": self.details}

def error_envelope(error: BaseException, *, message: str | None = None) -> ErrorEnvelope:
    status = getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)
    if status in (401, 403):
        return ErrorEnvelope("permission_denied", "permission", False, message or str(error))
    if status == 409:
        return ErrorEnvelope("conflict", "conflict", False, message or str(error))
    if status == 429 or isinstance(status, int) and status >= 500:
        return ErrorEnvelope("provider_unavailable", "provider", True, message or str(error), {"statusCode": status})
    if isinstance(error, (TimeoutError,)):
        return ErrorEnvelope("timeout", "timeout", True, message or str(error))
    if isinstance(error, (ValueError, TypeError, KeyError)):
        return ErrorEnvelope("invalid_request", "validation", False, message or str(error))
    return ErrorEnvelope("internal_error", "execution", False, message or str(error))

__all__ = ["ErrorEnvelope", "error_envelope"]
