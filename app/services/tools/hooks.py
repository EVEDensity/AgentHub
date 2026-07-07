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


# ── Hook Manager (dual-track: pluggy + legacy async) ──────────────────


class HookManager:
    """Manages pre/post tool hooks for the tool execution lifecycle.

    Singleton pattern, consistent with ToolRegistry.

    **Dual-track design (Sprint 5 — pluggy integration)**:
    This manager now delegates to the pluggy-based ``PluginManager`` for
    builtin and third-party plugins (synchronous ``@hookimpl`` methods),
    while preserving the original async hook registry for backward
    compatibility. Callers that registered hooks via ``register_pre`` /
    ``register_post`` continue to work unchanged.

    Hook execution order in ``run_pre_hooks``:
      1. pluggy ``pre_tool_use`` implementations (sync, fast block check)
      2. legacy async hooks (global → category → per-tool)

    Hook execution order in ``run_post_hooks``:
      1. legacy async hooks (global → category → per-tool)
      2. pluggy ``post_tool_use`` implementations (sync, e.g. sanitize)

    Hooks can be registered at three scopes (legacy async track):
      1. **Global** (tool_name=None) — fires for all tools
      2. **Category** (tool_name="category:*") — fires for all tools in a category
      3. **Per-tool** (tool_name="web_search") — fires only for a specific tool

    Execution order within each scope: first-registered, first-called.
    """

    def __init__(self) -> None:
        self._pre_hooks: dict[str, list[PreToolUseHook]] = {}
        self._post_hooks: dict[str, list[PostToolUseHook]] = {}
        # Lazy-loaded pluggy plugin manager (avoid circular import at module load)
        self._pm = None
        self._pluggy_loaded: bool = False

    # ── Pluggy integration (internal) ─────────────────────────────────

    def _ensure_pluggy_loaded(self) -> None:
        """Lazily import and initialise the PluginManager on first use.

        Importing at module load would create a circular dependency
        (plugin_manager → plugins → builtin_sanitize → sandbox_executor
        → httpx), so we defer it to the first hook execution.
        """
        if self._pluggy_loaded:
            return
        self._pluggy_loaded = True
        try:
            from .plugin_manager import plugin_manager
            self._pm = plugin_manager
            plugin_manager.load_all()
        except Exception as exc:  # noqa: BLE001 — pluggy is optional at runtime
            logger.warning("hook_manager: pluggy init failed, running legacy-only: %s", exc)
            self._pm = None

    def _run_pluggy_pre(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> PreToolUseResult:
        """Invoke pluggy pre_tool_use hooks (sync). Returns a merged result.

        If any implementation returns ``{"blocked": True}``, the merged
        result is blocked and the first blocking reason is preserved.
        ``modified_input`` from non-blocking implementations is merged
        into the returned ``modified_input``.
        """
        if self._pm is None:
            return PreToolUseResult()
        try:
            results = self._pm.hook.pre_tool_use(
                tool_name=tool_name,
                arguments=arguments,
                context=context,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("hook_manager: pluggy pre_tool_use raised: %s", exc)
            return PreToolUseResult()

        merged_input: dict[str, Any] | None = None
        for res in results or []:
            if not isinstance(res, dict):
                continue
            if res.get("blocked"):
                return PreToolUseResult(
                    blocked=True,
                    reason=str(res.get("reason", "blocked by plugin")),
                )
            mod = res.get("modified_input")
            if isinstance(mod, dict):
                merged_input = mod if merged_input is None else {**merged_input, **mod}
        return PreToolUseResult(modified_input=merged_input)

    def _run_pluggy_post(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke pluggy post_tool_use hooks (sync). Returns possibly-modified result."""
        if self._pm is None:
            return result
        try:
            results = self._pm.hook.post_tool_use(
                tool_name=tool_name,
                arguments=arguments,
                result=result,
                context=context,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("hook_manager: pluggy post_tool_use raised: %s", exc)
            return result

        current = dict(result)
        for res in results or []:
            if not isinstance(res, dict):
                continue
            mod = res.get("modified_result")
            if isinstance(mod, dict):
                current.update(mod)
        return current

    # ── Registration (legacy async track — unchanged) ─────────────────

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

    # ── Execution (dual-track) ────────────────────────────────────────

    async def run_pre_hooks(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
        category: str = "",
    ) -> PreToolUseResult:
        """Execute all matching pre-tool-use hooks (pluggy + legacy async).

        Execution order:
          1. pluggy ``pre_tool_use`` (sync) — fast block check. If any
             implementation blocks, return immediately without running
             legacy async hooks.
          2. legacy async hooks (global → category → per-tool). The first
             hook that blocks short-circuits the chain.

        Returns the final PreToolUseResult, which may have modified_input
        accumulated from both tracks.
        """
        self._ensure_pluggy_loaded()

        # ── Track 1: pluggy (sync) ──
        pluggy_result = self._run_pluggy_pre(tool_name, arguments, context)
        if pluggy_result.blocked:
            logger.info(
                "hook_manager: tool '%s' blocked by pluggy plugin: %s",
                tool_name, pluggy_result.reason,
            )
            return pluggy_result

        # Start from pluggy's merged input (if any), fall back to a copy
        # of the original arguments.
        current_input = (
            pluggy_result.modified_input
            if pluggy_result.modified_input is not None
            else dict(arguments)
        )

        # ── Track 2: legacy async hooks ──
        hook_keys = self._resolve_hook_keys(tool_name, category)
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
        """Execute all matching post-tool-use hooks (legacy async + pluggy).

        Execution order:
          1. legacy async hooks (global → category → per-tool). Modified
             results are merged (last wins). Side effects accumulate.
          2. pluggy ``post_tool_use`` (sync) — e.g. sanitize runs last so
             it sees the final result after async modifications.

        Returns the final PostToolUseResult with any modified_result
        and accumulated side_effects.
        """
        self._ensure_pluggy_loaded()

        hook_keys = self._resolve_hook_keys(tool_name, category)
        current_result = dict(result)
        all_side_effects: list[Callable[[], Awaitable[None]]] = []

        # ── Track 1: legacy async hooks ──
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

        # ── Track 2: pluggy (sync) — runs after async so sanitize sees final ──
        current_result = self._run_pluggy_post(
            tool_name, arguments, current_result, context,
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
        """Return counts of registered legacy async hooks: {"pre": N, "post": M}.

        Note: this counts only the legacy async track. Pluggy-managed
        plugins are tracked separately via ``plugin_manager.list_plugins()``.
        """
        pre_count = sum(len(v) for v in self._pre_hooks.values())
        post_count = sum(len(v) for v in self._post_hooks.values())
        return {"pre": pre_count, "post": post_count}


# ── Singleton ──────────────────────────────────────────────────────────
hook_manager = HookManager()
