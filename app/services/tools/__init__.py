from __future__ import annotations

"""Tools module — built-in tool definitions, handlers, and enhanced execution.

Call ``register_builtin_tools()`` at application startup to populate
the global tool registry.

Call ``initialize_tool_system()`` to wire up the enhanced function-calling
system (permission manager, hook manager, streaming executor, etc.).
"""

import logging

logger = logging.getLogger("agenthub.tools")


def register_builtin_tools() -> int:
    """Register all built-in tools with the global tool_registry.

    Returns the number of tools registered.
    """
    from app.services.tool_registry import tool_registry
    from app.services.tools.definitions import BUILTIN_TOOLS

    for tool in BUILTIN_TOOLS:
        tool_registry.register(tool)

    count = tool_registry.count()
    logger.info("tools: registered %d built-in tools: %s",
                count, tool_registry.list_names())
    return count


# ── Enhanced system singletons (lazy-init via initialize_tool_system) ──

_streaming_executor = None
_permission_manager = None
_hook_manager = None
_result_storage = None
_progress_tracker = None


def initialize_tool_system() -> "StreamingToolExecutor":
    """Wire up all enhanced tool system components.

    Called once during FastAPI startup (lifespan).

    Returns:
        The configured StreamingToolExecutor instance.

    Sets module-level singletons so agent_service can lazily retrieve
    them via ``_get_streaming_executor()`` without dependency injection.
    """
    global _streaming_executor, _permission_manager, _hook_manager
    global _result_storage, _progress_tracker

    from app.services.tool_registry import tool_registry
    from app.services.tool_executor import tool_executor
    from app.services.tools.permission import PermissionManager
    from app.services.tools.hooks import hook_manager as _hm
    from app.services.tools.result_storage import ResultStorage
    from app.services.tools.progress import progress_tracker as _pt
    from app.services.tools.streaming_executor import StreamingToolExecutor

    # ── 1. Permission manager ──────────────────────────────────────────
    _permission_manager = PermissionManager()
    try:
        _permission_manager.load_rules()
    except Exception:
        logger.debug("initialize_tool_system: permission rules not loaded (DB may not be ready)")

    # ── 2. Hook manager ────────────────────────────────────────────────
    _hook_manager = _hm
    try:
        from app.services.tools.builtin_hooks import register_builtin_hooks
        register_builtin_hooks(_hook_manager)
    except Exception:
        logger.debug("initialize_tool_system: builtin hooks not registered", exc_info=True)

    # ── 3. Result storage ──────────────────────────────────────────────
    _result_storage = ResultStorage()

    # ── 4. Progress tracker ────────────────────────────────────────────
    _progress_tracker = _pt

    # ── 5. Configure tool executor with enhancements ───────────────────
    tool_executor.configure(
        permission_manager=_permission_manager,
        hook_manager=_hook_manager,
        result_storage=_result_storage,
    )

    # ── 6. Build streaming executor ────────────────────────────────────
    _streaming_executor = StreamingToolExecutor(
        permission_manager=_permission_manager,
        hook_manager=_hook_manager,
        progress_tracker=_progress_tracker,
    )

    logger.info(
        "initialize_tool_system: enhanced function calling ready "
        "(permissions=%s, hooks=%s, streaming_executor=%s)",
        "enabled" if _permission_manager else "disabled",
        "enabled" if _hook_manager else "disabled",
        "enabled" if _streaming_executor else "disabled",
    )

    return _streaming_executor


def get_streaming_executor() -> "StreamingToolExecutor | None":
    """Return the module-level streaming executor (may be None if not initialized)."""
    return _streaming_executor


# ── Re-exports for convenience ─────────────────────────────────────────

from app.services.tools.streaming_executor import StreamingToolExecutor  # noqa: E402, F811
