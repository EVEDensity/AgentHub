"""CLI configuration boundary.

Production callers resolve environment-backed values through this module so
precedence and secret handling stay consistent.  The resolver returns plain
data and never persists or logs credentials.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ConfigResolver:
    """Resolve CLI settings from explicit values, state, and environment."""

    environment: Mapping[str, str]

    def __repr__(self) -> str:
        return "ConfigResolver(environment=<redacted>)"

    @classmethod
    def from_environment(cls) -> "ConfigResolver":
        return cls(dict(os.environ))

    def get(self, name: str, default: str = "") -> str:
        return str(self.environment.get(name, default) or "")

    def resolve_model(
        self,
        *,
        provider: str | None,
        model: str | None,
        base_url: str | None,
        config: Mapping[str, object] | None,
        default_model: str,
    ) -> dict[str, str]:
        stored = config or {}
        provider_keys = {
            "deepseek": "DEEPSEEK_API_KEY",
            "qwen": "DASHSCOPE_API_KEY",
            "zhipu": "ZHIPUAI_API_KEY",
            "doubao": "DOUBAO_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "minimax": "MINIMAX_API_KEY",
        }
        api_key = self.get("AGENTHUB_CLI_MODEL_API_KEY") or self.get(
            "AGENTHUB_DESKTOP_MODEL_API_KEY"
        )
        resolved_provider = (
            provider
            or str(stored.get("provider") or "")
            or self.get("AGENTHUB_CLI_PROVIDER")
            or ("openai" if api_key else "mock")
        )
        if not api_key and resolved_provider in provider_keys:
            api_key = self.get(provider_keys[resolved_provider])
        return {
            "provider": resolved_provider,
            "model": model or str(stored.get("model") or "") or self.get("AGENTHUB_CLI_MODEL") or default_model,
            "base_url": base_url or str(stored.get("base_url") or "") or self.get("AGENTHUB_CLI_MODEL_BASE_URL"),
            "api_key": api_key,
        }


__all__ = ["ConfigResolver"]
