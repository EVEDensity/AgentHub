# ─────────────────────────────────────────────────────────────────────
# AuditPlugin — records tool invocations to the audit log (builtin)
# ─────────────────────────────────────────────────────────────────────
# Hooks: post_tool_use
# Behavior: after every tool executes, logs an audit record with the
# tool name, caller identity (from context), success flag, and a snippet
# of the result. Uses the existing auth_service.write_audit when a
# running event loop is available; falls back to Python logging
# otherwise (e.g. in unit tests without an event loop).
# ─────────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from typing import Any

from ..plugin_spec import hookimpl

logger = logging.getLogger("agenthub.plugins.audit")

# Truncate result snippets in the audit log to keep records compact.
_MAX_SNIPPET_CHARS = 200


class AuditPlugin:
    """Builtin audit plugin — logs every tool invocation.

    Registered as ``builtin.audit``. Implements ``post_tool_use`` so the
    audit record is written after the tool finishes (success or failure).
    The plugin never blocks execution and never modifies the result.
    """

    @hookimpl
    def post_tool_use(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Record the tool call. Returns None (no result modification)."""
        user_id = str(context.get("user_id", "") or "")
        tenant_id = str(context.get("tenant_id", "") or "")
        session_id = str(context.get("session_id", "") or "")
        success = bool(result.get("success", True))
        # Build a compact snippet for the audit payload
        snippet = self._build_snippet(result)
        payload = {
            "tool_name": tool_name,
            "tenant_id": tenant_id,
            "session_id": session_id,
            "success": success,
            "arguments_keys": list(arguments.keys()),
            "result_snippet": snippet,
        }

        # Try the structured audit service first (writes to DB). It
        # schedules an async task via the running event loop, so it only
        # works inside an async context.
        written = False
        try:
            from app.services.auth.service import write_audit  # type: ignore
            write_audit(
                user_id=user_id or "unknown",
                agent_id=str(context.get("agent_id", "") or "tool"),
                action=f"tool:{tool_name}",
                risk_level=str(context.get("risk_level", "normal")),
                decision="allow" if success else "deny",
                payload=payload,
            )
            written = True
        except Exception:  # noqa: BLE001 — audit is best-effort
            pass

        # Always also emit a structured log line so audits are captured
        # even when the DB write fails or no event loop is running.
        logger.info(
            "tool_audit tool=%s user=%s tenant=%s success=%s written=%s",
            tool_name, user_id, tenant_id, success, written,
        )
        return None

    @staticmethod
    def _build_snippet(result: dict[str, Any]) -> str:
        """Extract a short text snippet from the result for the audit log."""
        for key in ("stdout", "output", "content", "message"):
            val = result.get(key)
            if isinstance(val, str) and val:
                return val[:_MAX_SNIPPET_CHARS]
        return ""

    @hookimpl
    def tool_categories(self) -> list[str] | None:
        """Audit cares about every tool category."""
        return None
