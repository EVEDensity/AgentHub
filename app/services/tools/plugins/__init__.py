# ─────────────────────────────────────────────────────────────────────
# Builtin plugins for the AgentHub tool system (P0.2 — Sprint 5)
# ─────────────────────────────────────────────────────────────────────
# Three builtin plugins provide cross-cutting concerns:
#   - AuditPlugin      : post_tool_use → audit log
#   - PermissionPlugin : pre_tool_use  → RBAC scope check
#   - SanitizePlugin   : post_tool_use → output redaction
#
# These are registered by PluginManager.load_builtin_plugins() with names
# "builtin.audit", "builtin.permission", "builtin.sanitize".
# ─────────────────────────────────────────────────────────────────────
from __future__ import annotations

from .builtin_audit import AuditPlugin
from .builtin_permission import PermissionPlugin
from .builtin_sanitize import SanitizePlugin

__all__ = ["AuditPlugin", "PermissionPlugin", "SanitizePlugin"]
