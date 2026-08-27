"""Vision capability registry — which (provider, model) pairs can see.

Fail-closed by default: unknown models are treated as text-only, and the
chat/tool layers MUST gate image parts on ``supports_vision()`` (or degrade
through :mod:`app.services.tools.multimodal.tools.image_describe`).

Extension surface:
* ``register_vision_model(provider_pattern, model_pattern)`` — in-code
  registration used by plugins and tests.
* ``AGENTHUB_VISION_MODELS`` env override — comma-separated
  ``provider:model`` fnmatch patterns, e.g. ``*:gpt-4o,kimi:moonshot*vision*``.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass

# Providers whose /v1 chat models accept OpenAI-style image_url parts.
_DEFAULT_VISION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("*", "*vision*"),            # moonshot-*-vision-preview etc.
    ("*", "gpt-4o*"),
    ("*", "gpt-4.1*"),
    ("*", "o3"),
    ("*", "glm-4v*"),
    ("*", "qwen-vl*"),
    ("*", "qwen2-vl*"),
    ("*", "llava*"),
)


class VisionUnsupportedError(ValueError):
    """Raised when an image part is sent to a model without vision."""


@dataclass(frozen=True)
class VisionRule:
    provider_pattern: str
    model_pattern: str


_RULES: list[VisionRule] = []


def _load_default_rules() -> None:
    for provider, model in _DEFAULT_VISION_PATTERNS:
        _RULES.append(VisionRule(provider.lower(), model.lower()))


_load_default_rules()


def _env_patterns() -> list[VisionRule]:
    raw = os.getenv("AGENTHUB_VISION_MODELS", "").strip()
    if not raw:
        return []
    patterns: list[VisionRule] = []
    for token in raw.split(","):
        token = token.strip().lower()
        if not token:
            continue
        provider, _, model = token.partition(":")
        patterns.append(VisionRule(provider or "*", model or "*"))
    return patterns


def register_vision_model(provider_pattern: str = "*", model_pattern: str = "*") -> None:
    """Whitelist a (provider, model) pattern as vision-capable."""
    _RULES.append(VisionRule(provider_pattern.lower(), model_pattern.lower()))


def unregister_vision_model(provider_pattern: str = "*", model_pattern: str = "*") -> None:
    rule = VisionRule(provider_pattern.lower(), model_pattern.lower())
    while rule in _RULES:
        _RULES.remove(rule)


def supports_vision(provider: str = "", model: str = "") -> bool:
    """True when the resolved (provider, model) may receive image parts.

    Checking order: registered/default rules first, then the
    ``AGENTHUB_VISION_MODELS`` env override (which can only ADD capability).
    """
    p = (provider or "").lower()
    m = (model or "").lower()
    for rule in [*_RULES, *_env_patterns()]:
        if fnmatch.fnmatchcase(p, rule.provider_pattern) and fnmatch.fnmatchcase(m, rule.model_pattern):
            return True
    return False


def assert_model_supports_images(provider: str, model: str) -> None:
    """Raise :class:`VisionUnsupportedError` with a degrade hint when not vision-capable."""
    if not supports_vision(provider, model):
        raise VisionUnsupportedError(
            f"model {provider or 'default'}:{model} does not support image inputs; "
            "pick a vision-capable model (e.g. moonshot-v1-8k-vision-preview, gpt-4o) "
            "or use the image_describe tool to obtain a text description instead"
        )


def clear_extra_rules_for_tests() -> None:
    """Reset to defaults plus env (used only by the test-suite)."""
    _RULES.clear()
    _load_default_rules()