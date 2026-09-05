"""Provider capability matrix and degraded-state projection for CLI/doctor."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderHealth:
    provider: str
    model: str
    failures: int = 0
    last_error: str | None = None
    capabilities: dict[str, bool] = field(default_factory=lambda: {
        "text_stream": False,
        "tool_call": False,
        "tool_call_stream": False,
        "verification": False,
    })

    @property
    def status(self) -> str:
        return "degraded" if self.failures else "healthy"

    def record(self, *, success: bool, error_kind: str | None = None, **capabilities: bool) -> None:
        if success:
            self.failures = 0
            self.last_error = None
            self.capabilities.update({k: bool(v) for k, v in capabilities.items() if k in self.capabilities})
        else:
            self.failures += 1
            self.last_error = error_kind or "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model, "status": self.status, "failures": self.failures, "lastError": self.last_error, "capabilities": dict(self.capabilities)}


def summarize_matrix(records: list[ProviderHealth]) -> list[dict[str, Any]]:
    return [record.to_dict() for record in records]


__all__ = ["ProviderHealth", "summarize_matrix"]
