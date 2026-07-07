# ─────────────────────────────────────────────────────────────────────
# PermissionPlugin — RBAC scope check before tool execution (builtin)
# ─────────────────────────────────────────────────────────────────────
# Hooks: pre_tool_use
# Behavior: inspects context["roles"] / context["scopes"] and the tool
# risk level (mirrors iam.BuiltinToolRisk from Go side). Blocks the
# call when the principal lacks the required scope for the tool's risk.
#
# Risk matrix (mirrors services/go/shared/iam/abac.go):
#   high     → requires tool:execute:high OR agent_operator+ role
#   normal   → requires tool:execute OR tool:execute:medium
#   low      → always allowed (read-only)
#
# Dev-mode compatibility: when context has no "roles" and no "scopes"
# (unauthenticated / local dev), the plugin allows the call. This keeps
# the plugin opt-in: it only enforces when identity information is
# present. It does NOT replace the existing ABAC layer in Go — it is a
# Python-side defense-in-depth check.
# ─────────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from typing import Any

from ..plugin_spec import hookimpl

logger = logging.getLogger("agenthub.plugins.permission")

# ── Tool risk classification (mirrors Go iam.BuiltinToolRisk) ──────────
# Keys are tool names; values are risk levels: "low" | "normal" | "high".
# Tools not listed default to "normal".
TOOL_RISK: dict[str, str] = {
    # High-risk: code execution, file writes, shell
    "code_execute": "high",
    "file_write": "high",
    "shell": "high",
    "terminal": "high",
    "bash": "high",
    # Normal-risk: network / state mutation
    "web_search": "normal",
    "http_request": "normal",
    "http_fetch": "normal",
    "file_read": "normal",
    "memory_write": "normal",
    # Low-risk: read-only
    "memory_read": "low",
    "list_files": "low",
    "read_file": "low",
}

# Roles that implicitly satisfy any tool risk (mirrors Go super_admin /
# tenant_admin break-glass). The agent_operator role satisfies high risk.
_HIGH_RISK_ROLES = {"super_admin", "tenant_admin", "agent_operator"}


class PermissionPlugin:
    """Builtin permission plugin — RBAC scope check.

    Registered as ``builtin.permission``. Implements ``pre_tool_use`` to
    block tools when the caller lacks permission. Returns a dict with
    ``blocked=True`` on denial, otherwise None.
    """

    @hookimpl
    def pre_tool_use(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        roles: list[str] = list(context.get("roles") or [])
        scopes: list[str] = list(context.get("scopes") or [])

        # Dev-mode: no identity info → allow (Python-side opt-in)
        if not roles and not scopes:
            return None

        risk = self._risk_of(tool_name)

        # Break-glass roles bypass the check entirely
        if any(r in _HIGH_RISK_ROLES for r in roles):
            return None

        # Scope check by risk level
        if risk == "low":
            return None  # read-only tools always allowed
        if risk == "normal":
            if self._has_any_scope(scopes, ("tool:execute", "tool:execute:medium", "*")):
                return None
            return self._deny(tool_name, f"tool '{tool_name}' requires tool:execute scope")
        # high — requires the explicit high-risk scope (plain tool:execute
        # is NOT enough; high-risk tools need the elevated grant)
        if self._has_any_scope(scopes, ("tool:execute:high", "*")):
            return None
        return self._deny(
            tool_name,
            f"tool '{tool_name}' is high-risk and requires tool:execute:high scope or agent_operator role",
        )

    @hookimpl
    def tool_categories(self) -> list[str] | None:
        """Permission cares about every tool category."""
        return None

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _risk_of(tool_name: str) -> str:
        return TOOL_RISK.get(tool_name, "normal")

    @staticmethod
    def _has_any_scope(scopes: list[str], required: tuple[str, ...]) -> bool:
        scope_set = set(scopes)
        return any(r in scope_set for r in required)

    @staticmethod
    def _deny(tool_name: str, reason: str) -> dict[str, Any]:
        logger.info("permission_plugin: blocked tool '%s': %s", tool_name, reason)
        return {"blocked": True, "reason": reason}
