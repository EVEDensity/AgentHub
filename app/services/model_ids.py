"""Canonical provider model identifiers and user-input aliases."""
from __future__ import annotations

DEEPSEEK_V4_FLASH = "deepseek-v4-flash"
DEEPSEEK_V4_PRO = "deepseek-v4-pro"
DEEPSEEK_LEGACY_CHAT = "deepseek-chat"

_ALIASES = {"v4-flash": DEEPSEEK_V4_FLASH, "flash": DEEPSEEK_V4_FLASH,
            "v4-pro": DEEPSEEK_V4_PRO, "pro": DEEPSEEK_V4_PRO,
            "chat": DEEPSEEK_LEGACY_CHAT, DEEPSEEK_LEGACY_CHAT: DEEPSEEK_LEGACY_CHAT}

def canonical_model_id(value: str, *, provider: str = "") -> str:
    candidate = str(value or "").strip().lower()
    if provider.lower() == "deepseek":
        return _ALIASES.get(candidate, candidate or DEEPSEEK_V4_FLASH)
    return candidate

__all__ = ["DEEPSEEK_V4_FLASH", "DEEPSEEK_V4_PRO", "DEEPSEEK_LEGACY_CHAT", "canonical_model_id"]
