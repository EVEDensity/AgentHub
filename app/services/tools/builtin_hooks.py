from __future__ import annotations

import json
import logging
import uuid
from typing import Any

logger = logging.getLogger("agenthub.tools.builtin_hooks")


# ── Global post-tool-use hook: audit log ──────────────────────────────

async def audit_log_hook(
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
    context: dict[str, Any],
) -> "PostToolUseResult":
    """Log every successful tool execution to the audit trail.

    This is a global post-tool-use hook — it fires for ALL tools.
    Logs tool name, arguments (safe subset), success/failure, and duration.
    Best-effort: failures in this hook never block the tool result.

    Context keys expected:
        - session_id: str
        - agent_id: str
        - user_id: str (optional)
    """
    # Import here to avoid circular imports
    from app.services.tools.hooks import PostToolUseResult

    try:
        from app.db.session import aexecute
        from app.db.init_db import now

        session_id = context.get("session_id", "")
        agent_id = context.get("agent_id", "")
        success = 1 if result.get("success") else 0
        duration_ms = result.get("duration_ms", 0)

        # Sanitize arguments: only log non-sensitive params
        safe_args = _sanitize_arguments(arguments)

        await aexecute(
            "INSERT INTO tool_call_log (id, session_id, agent_id, tool_name, "
            "arguments_json, result_json, success, duration_ms, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            str(uuid.uuid4()),
            session_id,
            agent_id,
            tool_name,
            json.dumps(safe_args, ensure_ascii=False),
            json.dumps(_summarize_result(result), ensure_ascii=False),
            success,
            int(duration_ms),
            now(),
        )
    except Exception:
        # Best-effort — never let audit logging break the tool pipeline
        pass

    return PostToolUseResult()


# ── Pre-tool-use hook: file_write safety ──────────────────────────────

async def file_write_safety_hook(
    tool_name: str,
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> "PreToolUseResult":
    """Validate file_write calls: ensure path is within the user's session workspace.

    This is a per-tool pre hook for ``file_write``.
    Blocks writes to paths outside the per-user per-session workspace.
    """
    from app.services.tools.hooks import PreToolUseResult

    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    path = arguments.get("path", "")
    if not path:
        return PreToolUseResult()  # let the validator catch missing path

    try:
        safe = resolve_workspace_path(path)
        if safe is None:
            ws_root = get_workspace_root()
            return PreToolUseResult(
                blocked=True,
                reason=f"路径 '{path}' 超出工作区允许范围，写入被阻止",
            )
    except (OSError, ValueError) as exc:
        return PreToolUseResult(
            blocked=True,
            reason=f"路径 '{path}' 无效: {exc}",
        )

    return PreToolUseResult()


# ── Pre-tool-use hook: code_execute sandbox ───────────────────────────

async def code_sandbox_hook(
    tool_name: str,
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> "PreToolUseResult":
    """Validate code_execute calls: enforce timeout caps and block
    dangerous patterns.

    This is a per-tool pre hook for ``code_execute``.
    """
    from app.services.tools.hooks import PreToolUseResult

    code = arguments.get("code", "")
    if not code:
        return PreToolUseResult()

    # Block obviously dangerous patterns
    dangerous_patterns = [
        ("rm -rf /", "删除根目录"),
        ("mkfs.", "格式化文件系统"),
        ("dd if=", "直接磁盘写入"),
        ("fork bomb", "Fork 炸弹"),
        (":(){ :|:& };:", "Fork 炸弹 (bash)"),
        ("os.system(", "系统命令执行 (Python)"),
        ("subprocess.call(", "子进程调用 (Python)"),
    ]

    code_lower = code.lower()
    for pattern, description in dangerous_patterns:
        if pattern.lower() in code_lower:
            return PreToolUseResult(
                blocked=True,
                reason=f"代码包含危险模式 ({description}): 已自动阻止",
            )

    # Enforce timeout cap
    timeout = arguments.get("timeout", 30)
    max_timeout = 60
    if isinstance(timeout, (int, float)) and timeout > max_timeout:
        return PreToolUseResult(
            modified_input={**arguments, "timeout": max_timeout},
        )

    return PreToolUseResult()


# ── Registration helper ───────────────────────────────────────────────

def register_builtin_hooks(hook_manager) -> int:
    """Register all built-in hooks with the HookManager.

    Args:
        hook_manager: An instance of HookManager from app.services.tools.hooks

    Returns:
        Number of hooks registered.
    """
    from app.services.tools.hooks import PostToolUseResult, PreToolUseResult

    # Global hooks (fire for all tools)
    hook_manager.register_post(None, audit_log_hook)

    # Per-tool hooks
    hook_manager.register_pre("file_write", file_write_safety_hook)
    hook_manager.register_pre("code_execute", code_sandbox_hook)

    count = hook_manager.get_hook_count()
    logger.info(
        "builtin_hooks: registered %d pre + %d post hooks",
        count["pre"], count["post"],
    )
    return count["pre"] + count["post"]


# ── Helpers ────────────────────────────────────────────────────────────

def _sanitize_arguments(args: dict[str, Any]) -> dict[str, Any]:
    """Remove or truncate sensitive/large argument values for audit logging."""
    safe: dict[str, Any] = {}
    sensitive_keys = {"api_key", "password", "token", "secret", "authorization"}
    max_value_length = 200

    for key, value in args.items():
        if key.lower() in sensitive_keys:
            safe[key] = "***REDACTED***"
        elif isinstance(value, str) and len(value) > max_value_length:
            safe[key] = value[:max_value_length] + "..."
        elif isinstance(value, dict):
            safe[key] = _sanitize_arguments(value)
        else:
            safe[key] = value

    return safe


def _summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    """Create a compact summary of tool result for audit logging."""
    summary: dict[str, Any] = {
        "success": result.get("success", False),
        "tool_name": result.get("tool_name", ""),
    }
    if not result.get("success"):
        summary["error"] = str(result.get("error", ""))[:300]

    result_data = result.get("result")
    if isinstance(result_data, str):
        summary["result_preview"] = result_data[:200]
    elif isinstance(result_data, dict):
        summary["result_keys"] = list(result_data.keys())[:10]
    elif isinstance(result_data, list):
        summary["result_len"] = len(result_data)

    return summary
