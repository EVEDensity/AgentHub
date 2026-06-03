"""General settings API — persistent JSON file backend.

Mirrors the settings.json pattern described in TECHNICAL_SPECIFICATION.md §4.4.
Settings are stored in DATA_DIR/settings.json with localStorage as a fast
client-side cache layer.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import DATA_DIR
from app.utils.async_file import aexists, aread_json, awrite_json, amkdir

router = APIRouter(prefix="/api", tags=["settings"])

SETTINGS_PATH = DATA_DIR / "settings.json"

DEFAULTS: dict[str, Any] = {
    "theme": "warm",
    "lang": "zh",
    "reply_lang": "default",
    "reasoning": 2,
    "thinking": True,
    "notify": True,
    "zoom": 100,
}


async def _read_settings() -> dict[str, Any]:
    """Read settings from disk, merging with defaults for missing keys."""
    settings: dict[str, Any] = dict(DEFAULTS)
    try:
        if await aexists(SETTINGS_PATH):
            raw = await aread_json(SETTINGS_PATH)
            if isinstance(raw, dict):
                settings.update(raw)
    except (ValueError, OSError):
        pass
    # Prune unknown keys (keep only known defaults)
    return {k: settings.get(k, DEFAULTS[k]) for k in DEFAULTS}


async def _write_settings(settings: dict[str, Any]) -> None:
    """Persist settings to disk."""
    await amkdir(SETTINGS_PATH.parent)
    cleaned = {k: settings[k] for k in DEFAULTS if k in settings}
    await awrite_json(SETTINGS_PATH, cleaned)


@router.get("/settings")
async def get_settings() -> dict[str, Any]:
    """Return all settings (merged with defaults)."""
    return await _read_settings()


class SettingsUpdate(BaseModel):
    theme: str | None = None
    lang: str | None = None
    reply_lang: str | None = None
    reasoning: int | None = None
    thinking: bool | None = None
    notify: bool | None = None
    zoom: int | None = None


@router.post("/settings")
async def update_settings(body: SettingsUpdate) -> dict[str, Any]:
    """Update one or more settings. Omitted fields are left unchanged."""
    current = await _read_settings()
    updates = body.model_dump(exclude_none=True)

    # Validate theme
    if "theme" in updates and updates["theme"] not in ("warm", "light", "dark"):
        updates.pop("theme")
    # Validate lang
    if "lang" in updates and updates["lang"] not in ("zh", "en"):
        updates.pop("lang")
    # Validate reply_lang
    if "reply_lang" in updates and updates["reply_lang"] not in ("default", "english", "chinese", "japanese"):
        updates.pop("reply_lang")
    # Validate reasoning
    if "reasoning" in updates:
        updates["reasoning"] = max(1, min(4, int(updates["reasoning"])))
    # Validate zoom
    if "zoom" in updates:
        updates["zoom"] = max(50, min(200, int(updates["zoom"])))

    current.update(updates)
    await _write_settings(current)
    return current
