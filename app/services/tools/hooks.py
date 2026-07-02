from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger("agenthub.tools.hooks")


# ── Hook result types ──────────────────────────────────────────────────


@dataclass
class PreToolUseResult:
    """Result from a pre-tool-use hook.

    Attributes:
        blocked: If True, the tool call should be aborted
        reason: Human-readable reason for blocking (used if blocked=True)
        modified_input: If provided, replaces the tool's input arguments
    """
    blocked: bool = False
    reason: str = ""
    modified_input: dict[str, Any] | None = None


@dataclass
class PostToolUseResult:
    """Result from a post-tool-use hook.

    Attributes:
        modified_result: If provided, replaces the tool's result dict
        side_effects: List of side-effect callables to run after the hook
    """
    modified_result: dict[str, Any] | None = None
    side_effects: list[Callable[[], Awaitable[None]]] = field(default_factory=list)


# ── Hook function type aliases ────────────────────────────────────────

PreToolUseHook = Callable[
    [str, dict[str, Any], dict[str, Any]],
    Awaitable[PreToolUseResult],
]
"""Signature: async def hook(tool_name, arguments, context) -> PreToolUseResult"""

PostToolUseHook = Callable[
    [str, dict[str, Any], dict[str, Any], dict[str, Any]],
    Awaitable[PostToolUseResult],
]
"""Signature: async def hook(tool_name, arguments, result, context) -> PostToolUseResult"""


# ── Hook Manager ──────────────────────────────────────────────────────


class HookManager:
    """Manages pre/post tool hooks for the tool execution lifecycle.

    Singleton pattern, consistent with ToolRegistry.

    Hooks can be registered at three scopes:
      1. **Global** (tool_name=None) — fires for all tools
      2. **Category** (tool_name="category:*") — fires for all tools in a category
      3. **Per-tool** (tool_name="web_search") — fires only for a specific tool

    Execution order within each scope: first-registered, first-called.

    Modeled on FUNCTION_CALLING_IMPLEMENTATION.md §6.
    """

    def __init__(self) -> None:
        self._pre_hooks: dict[str, list[PreToolUseHook]] = {}
        self._post_hooks: dict[str, list[PostToolUseHook]] = {}

    # ── Registration ──────────────────────────────────────────────────

    def register_pre(self, tool_name: str | None, hook: PreToolUseHook) -> None:
        """Register a pre-tool-use hook.

        Args:
            tool_name: Tool name (e.g. "file_write"), category prefix
                       (e.g. "category:file"), or None for global.
            hook: The async hook function.
        """
        key = tool_name or "__global__"
        self._pre_hooks.setdefault(key, []).append(hook)
        logger.debug("hook_manager: registered pre hook for '%s'", key)

    def register_post(self, tool_name: str | None, hook: PostToolUseHook) -> None:
        """Register a post-tool-use hook."""
        key = tool_name or "__global__"
        self._post_hooks.setdefault(key, []).append(hook)
        logger.debug("hook_manager: registered post hook for '%s'", key)

    def unregister_pre(self, tool_name: str | None, hook: PreToolUseHook) -> bool:
        """Remove a pre-tool-use hook. Returns True if it existed."""
        key = tool_name or "__global__"
        hooks = self._pre_hooks.get(key, [])
        if hook in hooks:
            hooks.remove(hook)
            return True
        return False

    def unregister_post(self, tool_name: str | None, hook: PostToolUseHook) -> bool:
        """Remove a post-tool-use hook. Returns True if it existed."""
        key = tool_name or "__global__"
        hooks = self._post_hooks.get(key, [])
        if hook in hooks:
            hooks.remove(hook)
            return True
        return False

    # ── Execution ─────────────────────────────────────────────────────

    async def run_pre_hooks(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
        category: str = "",
    ) -> PreToolUseResult:
        """Execute all matching pre-tool-use hooks in order.

        Execution order: global → category → per-tool.
        The first hook that blocks (blocked=True) short-circuits the chain.

        Returns the final PreToolUseResult, which may have modified_input
        accumulated from all hooks.
        """
        hook_keys = self._resolve_hook_keys(tool_name, category)
        current_input = dict(arguments)  # work on a copy

        for key in hook_keys:
            for hook in self._pre_hooks.get(key, []):
                try:
                    result = await hook(tool_name, current_input, context)
                    if result.blocked:
                        logger.info(
                            "hook_manager: tool '%s' blocked by pre hook '%s': %s",
                            tool_name, key, result.reason,
                        )
                        return result
                    if result.modified_input is not None:
                        current_input = result.modified_input
                except Exception as exc:
                    logger.warning(
                        "hook_manager: pre hook '%s' for tool '%s' raised: %s",
                        key, tool_name, exc,
                    )
                    # Don't let a hook error block execution

        return PreToolUseResult(modified_input=current_input)

    async def run_post_hooks(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        context: dict[str, Any],
        category: str = "",
    ) -> PostToolUseResult:
        """Execute all matching post-tool-use hooks in order.

        Execution order: global → category → per-tool.
        Modified results from hooks are merged (last wins).

        Returns the final PostToolUseResult with any modified_result
        and accumulated side_effects.
        """
        hook_keys = self._resolve_hook_keys(tool_name, category)
        current_result = dict(result)
        all_side_effects: list[Callable[[], Awaitable[None]]] = []

        for key in hook_keys:
            for hook in self._post_hooks.get(key, []):
                try:
                    hook_result = await hook(
                        tool_name, arguments, current_result, context,
                    )
                    if hook_result.modified_result is not None:
                        current_result = hook_result.modified_result
                    all_side_effects.extend(hook_result.side_effects)
                except Exception as exc:
                    logger.warning(
                        "hook_manager: post hook '%s' for tool '%s' raised: %s",
                        key, tool_name, exc,
                    )

        return PostToolUseResult(
            modified_result=current_result,
            side_effects=all_side_effects,
        )

    # ── Helpers ────────────────────────────────────────────────────────

    def _resolve_hook_keys(self, tool_name: str, category: str = "") -> list[str]:
        """Build the ordered list of hook keys for a tool execution.

        Returns: ["__global__", "category:{category}", "{tool_name}"]
        """
        keys = ["__global__"]
        if category:
            keys.append(f"category:{category}")
        keys.append(tool_name)
        return keys

    def get_hook_count(self) -> dict[str, int]:
        """Return counts: {"pre": N, "post": M}."""
        pre_count = sum(len(v) for v in self._pre_hooks.values())
        post_count = sum(len(v) for v in self._post_hooks.values())
        return {"pre": pre_count, "post": post_count}


# ── Singleton ──────────────────────────────────────────────────────────
hook_manager = HookManager()
