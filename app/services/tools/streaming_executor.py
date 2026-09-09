from __future__ import annotations

import asyncio
import logging
import time
import uuid
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable
from app.services.tools.receipts import ToolReceipt, ToolReceiptStatus, ToolReceiptStore

logger = logging.getLogger("agenthub.tools.streaming_executor")


class ToolState(str, Enum):
    """States in a tool execution lifecycle.

    Modeled on FUNCTION_CALLING_IMPLEMENTATION.md §3.2.2 state machine:
      null → queued → executing → completed / yielded / error
    """
    QUEUED = "queued"
    EXECUTING = "executing"
    COMPLETED = "completed"
    YIELDED = "yielded"  # Reserved for async generator-based tools
    ERROR = "error"


@dataclass
class ToolExecutionItem:
    """Represents one tool call in the execution queue."""
    id: str  # Unique ID matching the tool_use block ID
    name: str
    arguments: dict[str, Any]
    status: ToolState = ToolState.QUEUED
    is_concurrency_safe: bool = False
    result: dict[str, Any] | None = None
    error: str | None = None
    error_type: str | None = None
    duration_ms: float = 0.0
    progress: list[dict[str, Any]] = field(default_factory=list)


class StreamingToolExecutor:
    """Concurrency-aware tool execution queue.

    Modeled on FUNCTION_CALLING_IMPLEMENTATION.md §3.2:
    ``StreamingToolExecutor`` class.

    Concurrency Rules:
      - **Concurrency-safe tools** (file_read, web_search, memory_search)
        can run in **parallel** with each other.
      - **Non-safe tools** (file_write, code_execute) run **exclusively**.
      - When a non-safe tool is executing, **all** other tools wait.
      - When safe tools are executing, a non-safe tool waits until all
        safe tools complete.

    Execution Lifecycle per tool:
      1. Enqueue → QUEUED
      2. canExecute() check passes → EXECUTING
      3. Execute with hooks → COMPLETED or ERROR
    """

    PER_TOOL_TIMEOUT = 15  # seconds per individual tool

    def __init__(
        self,
        permission_manager: Any = None,
        hook_manager: Any = None,
        progress_tracker: Any = None,
        receipt_store: ToolReceiptStore | None = None,
    ) -> None:
        self._queue: list[ToolExecutionItem] = []
        self._permission_manager = permission_manager
        self._hook_manager = hook_manager
        self._progress_tracker = progress_tracker
        self._receipt_store = receipt_store
        self._on_state_change: Callable[[ToolExecutionItem], Awaitable[None]] | None = None
        self._context_overrides: dict[str, str] = {}

    # ── Public API ────────────────────────────────────────────────────

    def on_state_change(
        self,
        callback: Callable[[ToolExecutionItem], Awaitable[None]],
    ) -> None:
        """Register a callback invoked whenever a tool's state changes.

        Used by the WebSocket layer to broadcast tool status updates.
        The callback receives the ToolExecutionItem with its current state.
        """
        self._on_state_change = callback

    def enqueue(
        self,
        name: str,
        arguments: dict[str, Any],
        is_concurrency_safe: bool = True,
        tool_id: str | None = None,
    ) -> ToolExecutionItem:
        """Add a tool to the execution queue.

        Args:
            name: Tool name (e.g. "file_read")
            arguments: Tool call arguments
            is_concurrency_safe: Whether this tool can run in parallel
            tool_id: Optional unique ID (generated if not provided)

        Returns:
            The created ToolExecutionItem
        """
        item = ToolExecutionItem(
            id=tool_id or str(uuid.uuid4()),
            name=name,
            arguments=arguments,
            is_concurrency_safe=is_concurrency_safe,
            status=ToolState.QUEUED,
        )
        self._queue.append(item)
        logger.debug(
            "streaming_executor: enqueued '%s' (safe=%s, queue_size=%d)",
            name, is_concurrency_safe, len(self._queue),
        )
        return item

    async def process_queue(self) -> list[dict[str, Any]]:
        """Process the execution queue until all tools complete.

        Continuously scans the queue, executing tools as they become
        eligible (per can_execute). Completes when all tools are either
        COMPLETED or ERROR.

        Returns:
            List of result dicts in the same format as ToolExecutor.execute_all()
        """
        while any(
            t.status in (ToolState.QUEUED, ToolState.EXECUTING)
            for t in self._queue
        ):
            # Find queued tools that can start now
            ready: list[ToolExecutionItem] = []
            for item in self._queue:
                if item.status == ToolState.QUEUED and self.can_execute(item):
                    ready.append(item)

            if ready:
                # Execute ready tools concurrently
                tasks = [self.execute_single(item) for item in ready]
                await asyncio.gather(*tasks)
            else:
                # No ready tools — wait for in-flight executions to complete
                await asyncio.sleep(0.05)

        # Collect results
        results: list[dict[str, Any]] = []
        for item in self._queue:
            if item.result:
                results.append(item.result)
            elif item.error:
                results.append({
                    "success": False,
                    "error": item.error,
                    "tool_name": item.name,
                    "error_type": item.error_type,
                    "duration_ms": item.duration_ms,
                })

        return results

    def can_execute(self, item: ToolExecutionItem) -> bool:
        """Check if this tool can start executing now.

        Rules:
          1. If no tools are executing → yes
          2. If item IS concurrency-safe AND all executing tools are
             also concurrency-safe → yes
          3. Otherwise → no (must wait)
        """
        executing = [t for t in self._queue if t.status == ToolState.EXECUTING]
        if not executing:
            return True

        # Non-safe tools must wait for all executing tools to finish
        if not item.is_concurrency_safe:
            return False

        # Safe tools can run alongside other safe tools
        return all(t.is_concurrency_safe for t in executing)

    async def execute_single(self, item: ToolExecutionItem) -> None:
        """Execute one tool through its full lifecycle:

        1. Permission check (if permission_manager is configured)
        2. Pre hooks (if hook_manager is configured)
        3. Tool execution with timeout
        4. Post hooks (if hook_manager is configured)
        5. Error classification on failure
        """
        item.status = ToolState.EXECUTING
        start_time = time.time()
        await self._notify_state_change(item)
        receipt_key = self._receipt_key(item)
        if self._receipt_store is not None:
            decision = self._receipt_store.replay_decision(receipt_key, strict=True)
            if decision != "execute":
                item.status = ToolState.ERROR
                item.error_type = "idempotency"
                item.error = f"工具调用未重放（{decision}）"
                item.duration_ms = (time.time() - start_time) * 1000
                await self._notify_state_change(item)
                return
            self._receipt_store.mark_started(receipt_key, item.name, time.time())

        try:
            from app.services.tool_registry import tool_registry

            tool = tool_registry.get(item.name)
            if tool is None:
                item.status = ToolState.ERROR
                item.error = f"未知工具: {item.name}"
                item.duration_ms = (time.time() - start_time) * 1000
                await self._notify_state_change(item)
                return

            context = {
                "tool_name": item.name,
                "session_id": self._context_overrides.get("session_id", ""),
                "agent_id": self._context_overrides.get("agent_id", ""),
                "user_id": self._context_overrides.get("user_id", ""),
                "progress_tracker": self._progress_tracker,
            }

            # ── 1. Permission check ────────────────────────────────
            if self._permission_manager is not None:
                from app.services.tools.permission import (
                    PermissionBehavior,
                    ToolPermissionContext,
                    get_permission_mode_for_session,
                )

                perm_context = ToolPermissionContext(
                    user_id=context.get("user_id", ""),
                    agent_id=context.get("agent_id", ""),
                    session_id=context.get("session_id", ""),
                    mode=get_permission_mode_for_session(context.get("session_id", "")),
                )
                perm_result = await self._permission_manager.check(
                    item.name,
                    item.arguments,
                    perm_context,
                    risk_level=tool.risk_level,
                    requires_user_confirmation=tool.requires_user_confirmation,
                )
                if perm_result.behavior == PermissionBehavior.DENY:
                    item.status = ToolState.ERROR
                    item.error = f"权限被拒绝: {perm_result.reason}"
                    item.error_type = "permission"
                    item.duration_ms = (time.time() - start_time) * 1000
                    await self._notify_state_change(item)
                    return
                if perm_result.behavior == PermissionBehavior.ASK:
                    # Permission requires user confirmation — the caller
                    # should handle this before enqueueing
                    logger.warning(
                        "streaming_executor: tool '%s' requires user confirmation — "
                        "caller should handle permission before enqueue",
                        item.name,
                    )

            # ── 1.5 Tool risk guardrail (safety check on arguments) ──
            try:
                from app.services.guardrails import classify_tool_risk as _ctr

                risk_result = _ctr(item.name, item.arguments)
                if risk_result.blocked:
                    item.status = ToolState.ERROR
                    item.error = f"安全策略阻止: {risk_result.flags[0].message if risk_result.flags else '此操作包含危险模式'}"
                    item.error_type = "safety_block"
                    item.duration_ms = (time.time() - start_time) * 1000
                    await self._notify_state_change(item)
                    return
                if risk_result.requires_confirmation:
                    logger.warning(
                        "streaming_executor: tool '%s' requires safety confirmation: %s",
                        item.name,
                        [f"{f.rule}:{f.message}" for f in risk_result.flags],
                    )
                    # Note: caller should handle CONFIRM-level operations before
                    # enqueueing. Log and continue — PermissionPlugin/permission.py
                    # will also flag this in step 1.
            except ImportError:
                pass  # guardrails module unavailable — skip
            except Exception as exc:
                logger.warning(
                    "streaming_executor: tool risk guardrail for '%s' failed: %s",
                    item.name, exc,
                )

            # ── 2. Pre hooks ───────────────────────────────────────
            effective_args = dict(item.arguments)
            if self._hook_manager is not None:
                try:
                    pre_result = await self._hook_manager.run_pre_hooks(
                        item.name, effective_args, context, category=tool.category,
                    )
                    if pre_result.blocked:
                        item.status = ToolState.ERROR
                        item.error = f"前置检查阻止: {pre_result.reason}"
                        item.error_type = "validation"
                        item.duration_ms = (time.time() - start_time) * 1000
                        await self._notify_state_change(item)
                        return
                    if pre_result.modified_input is not None:
                        effective_args = pre_result.modified_input
                except Exception as exc:
                    logger.warning(
                        "streaming_executor: pre hooks for '%s' failed: %s",
                        item.name, exc,
                    )

            # ── 3. Execute with timeout ────────────────────────────
            from app.services.tool_executor import tool_executor

            try:
                result = await asyncio.wait_for(
                    tool_executor.execute(item.name, effective_args),
                    timeout=self.PER_TOOL_TIMEOUT,
                )
                item.result = result

                # ── 4. Post hooks ─────────────────────────────────
                if self._hook_manager is not None and result:
                    try:
                        post_result = await self._hook_manager.run_post_hooks(
                            item.name, effective_args, result, context,
                            category=tool.category,
                        )
                        if post_result.modified_result is not None:
                            item.result = post_result.modified_result
                        # Execute side effects
                        for side_effect in post_result.side_effects:
                            try:
                                await side_effect()
                            except Exception:
                                pass
                    except Exception as exc:
                        logger.warning(
                            "streaming_executor: post hooks for '%s' failed: %s",
                            item.name, exc,
                        )

                item.status = (
                    ToolState.COMPLETED
                    if not isinstance(result, dict) or result.get("success", True)
                    else ToolState.ERROR
                )
                if self._receipt_store is not None:
                    digest = hashlib.sha256(repr(item.result).encode("utf-8")).hexdigest() if item.result is not None else None
                    self._receipt_store.put(ToolReceipt(
                        receipt_key,
                        item.name,
                        ToolReceiptStatus.SUCCEEDED if item.status is ToolState.COMPLETED else ToolReceiptStatus.FAILED,
                        time.time(),
                        result_digest=digest if item.status is ToolState.COMPLETED else None,
                        error_type=(
                            str(result.get("error_type") or "tool_failure")
                            if item.status is ToolState.ERROR and isinstance(result, dict)
                            else None
                        ),
                    ))

            except asyncio.TimeoutError:
                item.status = ToolState.ERROR
                item.error = f"工具 '{item.name}' 执行超时（{self.PER_TOOL_TIMEOUT}秒）"
                item.error_type = "timeout"
                from app.services.tools.errors import classify_tool_error

                _, safe_msg = classify_tool_error(
                    TimeoutError(f"Tool '{item.name}' timed out"), item.name,
                )
                item.error = safe_msg

        except asyncio.CancelledError:
            # Cancellation means the side effect outcome is indeterminate;
            # preserve UNKNOWN so a later resume cannot replay blindly.
            if self._receipt_store is not None:
                self._receipt_store.mark_unknown(
                    receipt_key,
                    item.name,
                    time.time(),
                    error_type="cancelled",
                )
            item.status = ToolState.ERROR
            item.error_type = "cancelled"
            item.error = "tool execution cancelled; outcome requires reconciliation"
            item.duration_ms = (time.time() - start_time) * 1000
            await self._notify_state_change(item)
            raise
        except Exception as exc:
            item.status = ToolState.ERROR
            from app.services.tools.errors import classify_tool_error

            error_type, safe_msg = classify_tool_error(exc, item.name)
            item.error_type = error_type.value
            item.error = safe_msg
            logger.warning(
                "streaming_executor: tool '%s' failed: %s (type=%s)",
                item.name, safe_msg, error_type.value,
            )

        if self._receipt_store is not None and item.status is ToolState.ERROR:
            # Reaching this branch means the executor observed a classified
            # failure.  UNKNOWN is reserved for a process crash or an
            # operator-reconciled STARTED receipt, never for a handled error.
            self._receipt_store.put(ToolReceipt(receipt_key, item.name, ToolReceiptStatus.FAILED, time.time(), error_type=item.error_type))

        item.duration_ms = (time.time() - start_time) * 1000

        # ── Ensure result dict has metadata ─────────────────────────
        if item.result and isinstance(item.result, dict):
            item.result.setdefault("tool_name", item.name)
            item.result.setdefault("duration_ms", item.duration_ms)

        await self._notify_state_change(item)

    def _receipt_key(self, item: ToolExecutionItem) -> str:
        mission = self._context_overrides.get("mission_id", "local")
        work_unit = self._context_overrides.get("work_unit_id", "default")
        attempt = self._context_overrides.get("attempt", "1")
        return f"{mission}/{work_unit}/{attempt}/{item.id}"

    # ── Context injection ─────────────────────────────────────────────

    def set_context(self, **kwargs: Any) -> None:
        """Set execution context values for permission checks and hooks.

        Common keys: session_id, agent_id, user_id
        """
        self._context_overrides = kwargs

    # ── State notification ────────────────────────────────────────────

    async def _notify_state_change(self, item: ToolExecutionItem) -> None:
        """Notify the registered state-change callback."""
        if self._on_state_change:
            try:
                await self._on_state_change(item)
            # noqa: BLE001 - best-effort (注释已说明故意吞)
            except Exception:
                pass  # Best-effort

    # ── Queue inspection ──────────────────────────────────────────────

    @property
    def queue_size(self) -> int:
        return len(self._queue)

    @property
    def active_count(self) -> int:
        return len([t for t in self._queue if t.status == ToolState.EXECUTING])

    def clear(self) -> None:
        """Clear the queue (e.g., on cancellation)."""
        self._queue.clear()
