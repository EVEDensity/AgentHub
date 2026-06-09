"""MCP System Configuration — manage system_config key-value store.

Endpoints:
  GET    /mcp/config              List all config entries
  GET    /mcp/config/{key}        Get a single config value
  PUT    /mcp/config/{key}        Set/update a config value
  DELETE /mcp/config/{key}        Delete a config entry
  POST   /mcp/config/import       Batch import config (JSON)
  GET    /mcp/config/export       Export all config as JSON
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, HTTPException

from app.db.session import afetch_all, afetch_one, aexecute
from app.services.auth_service import get_current_user, require_admin, write_audit

router = APIRouter(prefix="/config", tags=["admin-mcp-config"])


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


# Config keys that require a server restart to take effect
_RESTART_REQUIRED = frozenset({
    "DATABASE_URL", "datasource", "server_port", "server_host",
})

# Config keys that carry sensitive values (display masked)
_SENSITIVE_KEYS = frozenset({
    "api_key", "api_secret", "password", "token", "secret",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "BING_API_KEY",
    "SERPAPI_API_KEY", "GOOGLE_API_KEY", "TAVILY_API_KEY", "BRAVE_API_KEY",
    "DATABASE_URL",
})


def _is_sensitive(key: str) -> bool:
    return key in _SENSITIVE_KEYS or any(sk in key.lower() for sk in _SENSITIVE_KEYS)


def _mask_value(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return value[:3] + "***" + value[-3:]


@router.get("")
async def list_config(user: dict = Depends(get_current_user)) -> list[dict]:
    """Return all system_config entries (sensitive values masked)."""
    require_admin(user)

    rows = await afetch_all(
        "SELECT key, value, updated_at AS \"updatedAt\" FROM system_config ORDER BY key"
    )
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        if _is_sensitive(item["key"]):
            item["value"] = _mask_value(item["value"])
            item["sensitive"] = True
        else:
            item["sensitive"] = False
        # Try to detect value type for better frontend editing
        val = item["value"]
        if val in ("true", "false"):
            item["type"] = "bool"
        elif val.isdigit() or (val.startswith("-") and val[1:].isdigit()):
            item["type"] = "int"
        elif val.startswith("{") or val.startswith("["):
            item["type"] = "json"
        else:
            item["type"] = "string"
        item["requiresRestart"] = item["key"] in _RESTART_REQUIRED
        result.append(item)
    return result


@router.get("/{key:path}")
async def get_config(key: str, user: dict = Depends(get_current_user)) -> dict:
    """Get a single config value. Sensitive values are masked when ?unmask=1 is NOT set."""
    require_admin(user)

    row = await afetch_one(
        "SELECT key, value, updated_at AS \"updatedAt\" FROM system_config WHERE key = $1",
        key,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Config key not found: {key}")

    item = dict(row)
    item["sensitive"] = _is_sensitive(key)
    item["requiresRestart"] = key in _RESTART_REQUIRED
    if item["sensitive"]:
        item["value"] = _mask_value(item["value"])
    return item


@router.put("/{key:path}")
async def set_config(
    key: str,
    body: dict,
    user: dict = Depends(get_current_user),
) -> dict:
    """Set/update a config value. Also applies runtime env variables when applicable."""
    require_admin(user)

    value = str(body.get("value", ""))
    if not value and value != "":
        raise HTTPException(status_code=400, detail="value is required")

    previous = await afetch_one(
        "SELECT value FROM system_config WHERE key = $1", key,
    )
    previous_value = previous["value"] if previous else ""

    await aexecute(
        "INSERT INTO system_config (key, value, updated_at) VALUES ($1,$2,$3) "
        "ON CONFLICT(key) DO UPDATE SET value = $2, updated_at = $3",
        key, value, _now(),
    )

    # Apply to os.environ for runtime feature flags
    if key.startswith("AGENTHUB_"):
        os.environ[key] = value

    # Strip sensitive value from audit payload
    audit_value = _mask_value(value) if _is_sensitive(key) else value
    audit_prev = _mask_value(previous_value) if _is_sensitive(key) else previous_value

    write_audit(
        user["id"], f"config/{key}", "config_set",
        "L1" if not _is_sensitive(key) else "L2", "approve",
        {"key": key, "previousValue": audit_prev, "newValue": audit_value},
    )

    return {
        "status": "success",
        "key": key,
        "value": _mask_value(value) if _is_sensitive(key) else value,
        "sensitive": _is_sensitive(key),
        "requiresRestart": key in _RESTART_REQUIRED,
    }


@router.delete("/{key:path}")
async def delete_config(key: str, user: dict = Depends(get_current_user)) -> dict:
    """Delete a config entry."""
    require_admin(user)

    row = await afetch_one("SELECT key FROM system_config WHERE key = $1", key)
    if not row:
        raise HTTPException(status_code=404, detail=f"Config key not found: {key}")

    await aexecute("DELETE FROM system_config WHERE key = $1", key)

    write_audit(
        user["id"], f"config/{key}", "config_delete",
        "L2", "approve",
        {"key": key},
    )

    return {"status": "success", "deleted": key}


@router.post("/import")
async def import_config(body: dict, user: dict = Depends(get_current_user)) -> dict:
    """Batch import config entries from a JSON object."""
    require_admin(user)

    entries = body.get("config", {})
    if not isinstance(entries, dict):
        raise HTTPException(status_code=400, detail="config must be a JSON object")

    imported = 0
    for key, value in entries.items():
        value_str = str(value) if not isinstance(value, str) else value
        await aexecute(
            "INSERT INTO system_config (key, value, updated_at) VALUES ($1,$2,$3) "
            "ON CONFLICT(key) DO UPDATE SET value = $2, updated_at = $3",
            key, value_str, _now(),
        )
        imported += 1

    write_audit(
        user["id"], "config", "config_import",
        "L2", "approve",
        {"importedKeys": list(entries.keys()), "count": imported},
    )

    return {"status": "success", "imported": imported}


@router.get("/export/data")
async def export_config(user: dict = Depends(get_current_user)) -> dict:
    """Export all config as a JSON object (sensitive values masked)."""
    require_admin(user)

    rows = await afetch_all(
        "SELECT key, value FROM system_config ORDER BY key"
    )
    config: dict[str, str] = {}
    for row in rows:
        key = row["key"]
        val = row["value"]
        if _is_sensitive(key):
            val = _mask_value(val)
        config[key] = val

    write_audit(
        user["id"], "config", "config_export",
        "L1", "approve",
        {},
    )

    return {"config": config, "exportedAt": _now()}
