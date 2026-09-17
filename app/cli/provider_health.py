"""Provider capability matrix and degraded-state projection for CLI/doctor."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from pathlib import Path
import json

SUPPORTED_PROVIDER_MATRIX: dict[str, tuple[str, ...]] = {
    "mock": ("text_stream", "tool_call", "tool_call_stream", "verification"),
    "deepseek": ("text_stream", "tool_call", "tool_call_stream", "verification"),
    "openai": ("text_stream", "tool_call", "tool_call_stream", "verification"),
    "anthropic": ("text_stream", "tool_call", "tool_call_stream", "verification"),
    "ollama": ("text_stream", "tool_call", "verification"),
    "minimax": ("text_stream", "tool_call", "verification"),
    "zhipu": ("text_stream", "tool_call", "verification"),
    "qwen": ("text_stream", "tool_call", "verification"),
    "doubao": ("text_stream", "tool_call", "verification"),
    "kimi": ("text_stream", "tool_call", "verification"),
}


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
    executed_call_ids: set[str] = field(default_factory=set, repr=False)
    consecutive_failures: int = 0

    @property
    def status(self) -> str:
        return "degraded" if self.failures else "healthy"

    @property
    def alert(self) -> bool:
        return self.consecutive_failures >= 3

    def record(self, *, success: bool, error_kind: str | None = None, **capabilities: bool) -> None:
        if success:
            self.failures = 0
            self.consecutive_failures = 0
            self.last_error = None
            self.capabilities.update({k: bool(v) for k, v in capabilities.items() if k in self.capabilities})
        else:
            self.failures += 1
            self.consecutive_failures += 1
            self.last_error = error_kind or "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model, "status": self.status, "failures": self.failures, "consecutiveFailures": self.consecutive_failures, "alert": self.alert, "lastError": self.last_error, "capabilities": dict(self.capabilities), "toolCalls": len(self.executed_call_ids)}

    def accept_call(self, call_id: str) -> bool:
        """Return false for duplicate call IDs (idempotent tool execution gate)."""
        normalized = str(call_id).strip()
        if not normalized or normalized in self.executed_call_ids:
            return False
        self.executed_call_ids.add(normalized)
        return True


class ProviderHealthRegistry:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], ProviderHealth] = {}

    def get(self, provider: str, model: str) -> ProviderHealth:
        key = (provider, model)
        return self._records.setdefault(key, ProviderHealth(provider, model))

    def snapshot(self) -> list[dict[str, Any]]:
        return summarize_matrix(list(self._records.values()))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schemaVersion": 1, "records": self.snapshot()}, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "ProviderHealthRegistry":
        registry = cls()
        if not path.is_file():
            return registry
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schemaVersion") != 1:
            raise ValueError("unsupported provider health schema")
        for item in payload.get("records", []):
            health = registry.get(str(item["provider"]), str(item["model"]))
            health.failures = int(item.get("failures", 0))
            health.consecutive_failures = int(item.get("consecutiveFailures", health.failures))
            health.last_error = item.get("lastError")
            health.capabilities.update({k: bool(v) for k, v in dict(item.get("capabilities", {})).items() if k in health.capabilities})
        return registry


def summarize_matrix(records: list[ProviderHealth]) -> list[dict[str, Any]]:
    return [record.to_dict() for record in records]


__all__ = ["ProviderHealth", "ProviderHealthRegistry", "SUPPORTED_PROVIDER_MATRIX", "summarize_matrix"]
