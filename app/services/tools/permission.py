from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.db.session import afetch_all

logger = logging.getLogger("agenthub.tools.permission")


class PermissionMode(str, Enum):
    """Permission mode for the tool execution system."""
    DEFAULT = "default"   # User confirmation required for L2+ tools
    BYPASS = "bypass"     # Auto-allow all operations
    AUTO = "auto"         # Rule-based auto-decision
    PLAN = "plan"         # Read-only: deny all write/delete/shell ops


# ── Session exec_permission store ──────────────────────────────────
# 1 = 询问 (ask), 2 = 跳过 (bypass), 3 = 计划 (plan/read-only)
_session_exec_perm: dict[str, int] = {}


def set_exec_permission(session_id: str, mode: int) -> None:
    """Store the exec_permission mode for a session."""
    if mode in (1, 2, 3):
        _session_exec_perm[session_id] = mode


def get_exec_permission(session_id: str) -> int:
    """Get the exec_permission mode for a session (default 1 = ask)."""
    return _session_exec_perm.get(session_id, 1)


def get_permission_mode_for_session(session_id: str) -> PermissionMode:
    """Map session exec_permission to PermissionMode."""
    mode = get_exec_permission(session_id)
    if mode == 2:
        return PermissionMode.BYPASS
    elif mode == 3:
        return PermissionMode.PLAN
    return PermissionMode.DEFAULT


class PermissionBehavior(str, Enum):
    """Outcome of a permission check."""
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionResult:
    """Result of a permission check for a tool call.

    Attributes:
        behavior: ALLOW, DENY, or ASK
        reason: Human-readable explanation for the decision
        source: Where the rule came from (e.g. "always_allow_rule:file_read")
    """
    behavior: PermissionBehavior
    reason: str = ""
    source: str = ""


@dataclass
class PermissionRule:
    """A single permission rule matching tool calls by name and path pattern.

    Used by PermissionManager to decide whether to allow, deny, or ask
    the user before executing a tool.

    Attributes:
        tool_pattern: Tool name to match (supports fnmatch/glob patterns, e.g. "file_*")
        path_pattern: File path pattern (for file tools), "*" matches all
        behavior: "allow", "deny", or "ask"
        source: Origin label — "user", "system", "agent_config"
        priority: Higher priority rules are checked first
    """
    tool_pattern: str = "*"
    path_pattern: str = "*"
    behavior: str = "ask"
    source: str = "user"
    priority: int = 0
    enabled: bool = True


@dataclass
class ToolPermissionContext:
    """Context passed to the permission check.

    Carries all the information needed to make a permission decision.
    """
    user_id: str = ""
    agent_id: str = ""
    session_id: str = ""
    auth_role: str = "developer"
    mode: PermissionMode = PermissionMode.DEFAULT


class PermissionManager:
    """Central permission checker for tool execution.

    Decision flow (modeled on cc-haha's FUNCTION_CALLING_IMPLEMENTATION.md §5):
      1. Check always_allow_rules → ALLOW if any match
      2. Check always_deny_rules  → DENY if any match
      3. Check always_ask_rules   → ASK if any match
      4. If tool.requires_user_confirmation → ASK
      5. For L3 risk tools in DEFAULT mode → ASK
      6. Fall through → ALLOW

    Rules are loaded from the database table ``tool_permission_rules``.
    Admin users (role='admin') in BYPASS mode skip all checks.
    """

    def __init__(self) -> None:
        self._rules: dict[str, list[PermissionRule]] = {
            "always_allow": [],
            "always_deny": [],
            "always_ask": [],
        }
        self._default_mode = PermissionMode.DEFAULT

    # ── Rule loading ──────────────────────────────────────────────────

    async def load_rules(self, agent_id: str = "*", user_id: str = "") -> None:
        """Load permission rules from the database.

        Called during startup and can be refreshed at runtime.

        Args:
            agent_id: Load rules for this agent ("*" = all agents)
            user_id: Currently unused, reserved for per-user rules
        """
        try:
            rows = await afetch_all(
                "SELECT id, agent_id, tool_pattern, path_pattern, behavior, "
                "source, priority, enabled "
                "FROM tool_permission_rules "
                "WHERE enabled=1 AND (agent_id=$1 OR agent_id='*') "
                "ORDER BY priority DESC",
                agent_id,
            )
        except Exception:
            logger.debug("permission_manager: failed to load rules (table may not exist yet)")
            rows = []

        # Clear and rebuild
        self._rules = {
            "always_allow": [],
            "always_deny": [],
            "always_ask": [],
        }

        for row in rows:
            rule = PermissionRule(
                tool_pattern=row.get("tool_pattern", "*"),
                path_pattern=row.get("path_pattern", "*"),
                behavior=row.get("behavior", "ask"),
                source=row.get("source", "user"),
                priority=row.get("priority", 0),
                enabled=bool(row.get("enabled", 1)),
            )

            if rule.behavior == "allow":
                self._rules["always_allow"].append(rule)
            elif rule.behavior == "deny":
                self._rules["always_deny"].append(rule)
            else:
                self._rules["always_ask"].append(rule)

        total = sum(len(v) for v in self._rules.values())
        if total > 0:
            logger.info(
                "permission_manager: loaded %d rules (allow=%d deny=%d ask=%d)",
                total,
                len(self._rules["always_allow"]),
                len(self._rules["always_deny"]),
                len(self._rules["always_ask"]),
            )

    def add_rule(self, rule: PermissionRule) -> None:
        """Add a rule programmatically (for runtime registration)."""
        if rule.behavior == "allow":
            self._rules["always_allow"].append(rule)
        elif rule.behavior == "deny":
            self._rules["always_deny"].append(rule)
        else:
            self._rules["always_ask"].append(rule)

    # ── Permission check ──────────────────────────────────────────────

    async def check(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolPermissionContext,
        risk_level: str = "L1",
        requires_user_confirmation: bool = False,
    ) -> PermissionResult:
        """Run the full permission check for a tool call.

        Args:
            tool_name: Name of the tool being called
            arguments: Tool call arguments (may contain "path" for file tools)
            context: Permission context (user, agent, session, mode)
            risk_level: Tool's risk level (L1/L2/L3)
            requires_user_confirmation: Whether the tool definition requires confirmation

        Returns:
            PermissionResult with the decision (allow/deny/ask)
        """
        # ── Bypass mode: admin or explicit bypass ─────────────────────
        if context.mode == PermissionMode.BYPASS:
            return PermissionResult(
                behavior=PermissionBehavior.ALLOW,
                reason="Bypass mode active",
                source="mode:bypass",
            )

        # ── Plan mode: read-only — deny all write/delete/shell/code ops ──
        if context.mode == PermissionMode.PLAN:
            # 真实内置工具的写/执行类白名单（与 definitions.py 对齐）
            _WRITE_TOOLS = {
                # 文件写入
                "file_write", "file_write_batch", "file_edit", "file_patch",
                # 代码 / Shell 执行
                "code_execute", "command_execute",
            }
            if tool_name in _WRITE_TOOLS:
                return PermissionResult(
                    behavior=PermissionBehavior.DENY,
                    reason=f"计划模式：禁止执行 '{tool_name}'（只读模式，不允许写文件/执行代码/执行 Shell）",
                    source="mode:plan",
                )
            # 文件写入工具的参数 schema 不带 operation 字段——以"是否携带 content/contents/paths_contents 字段"作为写意图信号
            if tool_name.startswith("file_"):
                has_write_intent = any(
                    arguments.get(field) is not None
                    for field in ("content", "contents", "paths_contents", "new_content")
                )
                if has_write_intent:
                    return PermissionResult(
                        behavior=PermissionBehavior.DENY,
                        reason="计划模式：检测到文件写入意图（content/contents/paths_contents 参数），已拒绝",
                        source="mode:plan",
                    )
            # http_request 若指向非 GET 也算写操作（POST/PUT/DELETE/PATCH）
            if tool_name == "http_request":
                method = (arguments.get("method") or "GET").upper()
                if method in {"POST", "PUT", "DELETE", "PATCH"}:
                    return PermissionResult(
                        behavior=PermissionBehavior.DENY,
                        reason=f"计划模式：禁止 HTTP {method}（只读模式）",
                        source="mode:plan",
                    )
            # 其他工具（read/search/list/navigate/screenshot/extract 等）放行
            return PermissionResult(
                behavior=PermissionBehavior.ALLOW,
                reason="Plan mode — read-only operations allowed",
                source="mode:plan",
            )

        if context.auth_role == "admin" and context.mode == PermissionMode.AUTO:
            return PermissionResult(
                behavior=PermissionBehavior.ALLOW,
                reason="Admin user in auto mode",
                source="role:admin",
            )

        # Extract path argument if present (for file tool matching)
        path_arg = arguments.get("path", arguments.get("file_path", "*"))

        # ── Step 1: Check always_allow rules ──────────────────────────
        for rule in self._rules["always_allow"]:
            if self._match_rule(rule, tool_name, path_arg):
                return PermissionResult(
                    behavior=PermissionBehavior.ALLOW,
                    reason=f"Matched allow rule {rule.tool_pattern}:{rule.path_pattern} (source={rule.source}, priority={rule.priority})",
                    source=f"rule:{rule.source}:allow:{rule.priority}",
                )

        # ── Step 2: Check always_deny rules ───────────────────────────
        for rule in self._rules["always_deny"]:
            if self._match_rule(rule, tool_name, path_arg):
                return PermissionResult(
                    behavior=PermissionBehavior.DENY,
                    reason=f"Matched deny rule {rule.tool_pattern}:{rule.path_pattern} (source={rule.source}, priority={rule.priority})",
                    source=f"rule:{rule.source}:deny:{rule.priority}",
                )

        # ── Step 3: Check always_ask rules ────────────────────────────
        for rule in self._rules["always_ask"]:
            if self._match_rule(rule, tool_name, path_arg):
                return PermissionResult(
                    behavior=PermissionBehavior.ASK,
                    reason=f"Matched ask rule {rule.tool_pattern}:{rule.path_pattern} (source={rule.source}, priority={rule.priority})",
                    source=f"rule:{rule.source}:ask:{rule.priority}",
                )

        # ── Step 4: Tool requires explicit confirmation ───────────────
        if requires_user_confirmation:
            return PermissionResult(
                behavior=PermissionBehavior.ASK,
                reason=f"工具 '{tool_name}' 需要用户确认后才能执行",
                source="tool:requires_user_confirmation",
            )

        # ── Step 5: L3 risk tools in DEFAULT mode need confirmation ───
        if risk_level == "L3" and context.mode == PermissionMode.DEFAULT:
            return PermissionResult(
                behavior=PermissionBehavior.ASK,
                reason=f"工具 '{tool_name}' 风险等级为 L3，需要确认后执行",
                source="risk_level:L3",
            )

        # ── Step 6: Default allow ──────────────────────────────────────
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            reason="No matching rules — default allow",
            source="default",
        )

    # ── Rule matching ─────────────────────────────────────────────────

    @staticmethod
    def _match_rule(rule: PermissionRule, tool_name: str, path_arg: Any) -> bool:
        """Check if a permission rule matches a tool call.

        Uses fnmatch for pattern matching so rules can use glob-style
        patterns like ``file_*`` or ``*search*``.
        """
        # Match tool name against rule's tool_pattern
        if not fnmatch.fnmatch(tool_name, rule.tool_pattern):
            return False

        # Match path argument against rule's path_pattern
        if rule.path_pattern != "*" and path_arg:
            path_str = str(path_arg) if not isinstance(path_arg, str) else path_arg
            if not fnmatch.fnmatch(path_str, rule.path_pattern):
                return False

        return True
