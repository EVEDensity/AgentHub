from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.services.tool_registry import ToolDefinition, tool_registry

logger = logging.getLogger("agenthub.tool_executor")

SIDE_EFFECTING_TOOLS = frozenset({
    "file_write", "file_edit", "file_patch", "file_write_batch",
    "apply_change_set", "command_execute", "code_execute",
    "git_commit", "git_branch_create", "git_revert",
    "git_cherry_pick", "network_request", "http_request",
})


class ToolExecutionError(Exception):
    """Raised when a tool call fails validation or execution."""

    def __init__(self, tool_name: str, message: str, missing_params: list[str] | None = None) -> None:
        self.tool_name = tool_name
        self.message = message
        self.missing_params = missing_params or []
        super().__init__(message)


class ToolExecutor:
    """Validates and executes tool calls, returning structured results.

    Handles the full lifecycle:
      1. Parse tool_calls JSON from LLM response text
      2. Validate parameters against tool definitions
      3. Execute tool handlers (with optional permission/hooks/result_storage)
      4. Return structured results for feedback into the LLM

    Enhanced with optional components via ``configure()``:
      - PermissionManager: permission checks before execution
      - HookManager: pre/post tool-use hooks
      - ResultStorage: content budget and truncation
    """

    MAX_ITERATIONS = 20  # safety limit for the tool-call loop (raised from 10 — complex multi-file projects need more write→execute→fix cycles)

    def __init__(self) -> None:
        self.permission_manager = None
        self.hook_manager = None
        self.result_storage = None
        self.receipt_store = None
        self.code_index = None

    def configure(
        self,
        permission_manager: Any = None,
        hook_manager: Any = None,
        result_storage: Any = None,
        receipt_store: Any = None,
        code_index: Any = None,
    ) -> None:
        """Inject optional enhancements. Called during app startup.

        All parameters are optional — if not provided, the executor
        behaves exactly as it did before (backward compatible).
        """
        if permission_manager is not None:
            self.permission_manager = permission_manager
        if hook_manager is not None:
            self.hook_manager = hook_manager
        if result_storage is not None:
            self.result_storage = result_storage
        if receipt_store is not None:
            self.receipt_store = receipt_store
        if code_index is not None:
            self.code_index = code_index

    def find_symbol(self, query: str, *, limit: int = 50) -> list[dict[str, Any]] | None:
        """Query the optional local AST index without making it authoritative."""
        index = self.code_index
        finder = getattr(index, "find_symbol", None)
        if not callable(finder):
            return None
        try:
            result = finder(query, limit=limit)
        except (OSError, RuntimeError, ValueError):
            return None
        return result if isinstance(result, list) else None

    def get_definition(self, symbol: str, *, path: str | None = None) -> list[dict[str, Any]] | None:
        """Return exact local definitions when the optional index is available."""
        index = self.code_index
        getter = getattr(index, "get_definition", None)
        if not callable(getter):
            return None
        try:
            result = getter(symbol, path=path)
        except (OSError, RuntimeError, ValueError):
            return None
        return result if isinstance(result, list) else None

    # ── JSON parsing ──────────────────────────────────────────────────

    @staticmethod
    def has_tool_calls(text: str) -> bool:
        """Quick check: does this text contain a tool_calls JSON block?"""
        if not text or not text.strip():
            return False
        # Fast check before full parsing
        return '"tool_calls"' in text or "'tool_calls'" in text

    @staticmethod
    def parse_tool_calls(text: str) -> list[dict[str, Any]]:
        """Legacy compatibility parser; new code must use structured ToolCall."""
        """Extract tool_calls from an LLM response.

        Handles common LLM quirks:
        - JSON embedded in markdown code blocks (```json ... ```)
        - Trailing commas in JSON
        - Natural language mixed with JSON
        - Multiple tool_call blocks
        - Single quotes instead of double quotes

        Returns a list of dicts like [{"name": "...", "arguments": {...}}].
        Returns an empty list if no valid tool_calls are found.
        """
        if not text or not text.strip():
            return []

        cleaned = text.strip()

        # Strip markdown code blocks if present
        # Case 1: ```json ... ``` or ``` ... ```
        md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.DOTALL)
        if md_match:
            cleaned = md_match.group(1).strip()

        # Case 2: Find the outermost { } pair that contains "tool_calls"
        # Try to locate the JSON object directly
        start = cleaned.find("{")
        if start < 0:
            return []

        # Find matching closing brace
        depth = 0
        end = -1
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end < 0:
            return []

        json_candidate = cleaned[start : end + 1]

        # Attempt to parse
        parsed = ToolExecutor._safe_json_parse(json_candidate)
        if parsed:
            return ToolExecutor._normalize_parsed(parsed)

        # Try fixing common issues: trailing commas, single quotes
        fixed = ToolExecutor._fix_json(json_candidate)
        parsed = ToolExecutor._safe_json_parse(fixed)
        if parsed:
            return ToolExecutor._normalize_parsed(parsed)

        # Last resort: regex-based extraction of individual tool call objects
        return ToolExecutor._regex_extract_tool_calls(cleaned)

    @staticmethod
    def _safe_json_parse(text: str) -> dict | None:
        """Attempt json.loads, returning None on failure."""
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    @staticmethod
    def _fix_json(text: str) -> str:
        """Apply common JSON repairs."""
        # Remove trailing commas before } or ]
        fixed = re.sub(r",\s*(\}|\])", r"\1", text)
        # Replace single quotes with double quotes (naive but often effective)
        # Only do this if the text has no double quotes
        if '"' not in fixed:
            fixed = fixed.replace("'", '"')
        # Remove comments (// to end of line)
        fixed = re.sub(r"//[^\n]*", "", fixed)
        return fixed

    @staticmethod
    def _normalize_parsed(parsed: dict) -> list[dict[str, Any]]:
        """Normalize a parsed JSON dict to a list of tool calls."""
        tool_calls = parsed.get("tool_calls")
        if isinstance(tool_calls, list):
            result: list[dict[str, Any]] = []
            for tc in tool_calls:
                if isinstance(tc, dict) and "name" in tc:
                    result.append({
                        "name": str(tc["name"]),
                        "arguments": tc.get("arguments", {}) if isinstance(tc.get("arguments"), dict) else {},
                    })
            return result
        return []

    @staticmethod
    def _regex_extract_tool_calls(text: str) -> list[dict[str, Any]]:
        """Fallback: extract individual tool call objects via regex."""
        results: list[dict[str, Any]] = []
        # Match {"name": "..." , "arguments": {...}} patterns
        pattern = r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})\s*\}'
        for match in re.finditer(pattern, text, re.DOTALL):
            name = match.group(1)
            args_str = match.group(2)
            args = ToolExecutor._safe_json_parse(args_str)
            if name:
                results.append({
                    "name": name,
                    "arguments": args if isinstance(args, dict) else {},
                })
        return results

    # ── Validation ────────────────────────────────────────────────────

    @staticmethod
    def validate_params(tool: ToolDefinition, arguments: dict[str, Any]) -> list[str]:
        """Check that required params are present and types are valid.

        Returns a list of missing required parameter names.
        """
        missing: list[str] = []
        for p in tool.parameters:
            if p.required and p.name not in arguments:
                # Check if there's a default value
                if p.default is not None:
                    continue
                missing.append(p.name)
        return missing

    @staticmethod
    def _type_check(value: Any, expected_type: str) -> bool:
        """Basic type checking for tool parameters."""
        type_map = {
            "string": str,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        expected = type_map.get(expected_type)
        if expected is None:
            return True  # unknown type, skip check
        return isinstance(value, expected)

    # ── Execution ─────────────────────────────────────────────────────

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        receipt_store: Any = None,
    ) -> dict[str, Any]:
        """Execute a single tool call.

        Returns:
            {"success": bool, "result": Any, "error": str|null, "tool_name": str}
        """
        tool = tool_registry.get(tool_name)
        if tool is None:
            return await self.execute_callable(
                tool_name,
                arguments,
                None,
                idempotency_key=idempotency_key,
                receipt_store=receipt_store,
                missing_error=f"未知工具: {tool_name}。可用的工具有: {', '.join(tool_registry.list_names())}",
            )
        missing = self.validate_params(tool, arguments)
        if missing:
            result = await self.execute_callable(
                tool_name,
                arguments,
                None,
                idempotency_key=idempotency_key,
                receipt_store=receipt_store,
                missing_error=(
                    f"工具 '{tool_name}' 缺少必填参数: {', '.join(missing)}。"
                    "请向用户询问这些参数的值。"
                ),
                error_type="invalid_arguments",
            )
            result.setdefault("missing_params", missing)
            return result
        if tool.handler is None:
            return await self.execute_callable(
                tool_name,
                arguments,
                None,
                idempotency_key=idempotency_key,
                receipt_store=receipt_store,
                missing_error=f"工具 '{tool_name}' 尚未实现执行处理器。",
                error_type="missing_handler",
            )
        return await self.execute_gateway(
            tool_name,
            arguments,
            lambda payload: tool.handler(**dict(payload)),
            idempotency_key=idempotency_key,
            receipt_store=receipt_store,
            risk_level=tool.risk_level,
            requires_user_confirmation=tool.requires_user_confirmation,
        )

    async def execute_callable(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        handler: Callable[[Mapping[str, Any]], Awaitable[Any]] | None,
        *,
        idempotency_key: str | None = None,
        receipt_store: Any = None,
        missing_error: str | None = None,
        error_type: str = "unknown_tool",
    ) -> dict[str, Any]:
        """Execute an injected callable through the canonical receipt boundary.

        Harness-scoped function tools are deliberately accepted as callables
        rather than importing Harness types into this infrastructure module.
        This keeps the dependency direction one-way while guaranteeing the
        same STARTED/SUCCEEDED/FAILED/UNKNOWN lifecycle as registry tools.
        """
        start_time = time.time()
        receipt_store = receipt_store if receipt_store is not None else getattr(self, "receipt_store", None)
        receipt_key = idempotency_key.strip() if isinstance(idempotency_key, str) else ""
        if receipt_store is not None and receipt_key:
            claim = getattr(receipt_store, "claim_started", None)
            decision = (
                str(claim(receipt_key, tool_name, start_time))
                if callable(claim)
                else receipt_store.replay_decision(receipt_key, strict=True)
            )
            if decision != "execute":
                return {
                    "success": decision == "already_succeeded",
                    "error": None if decision == "already_succeeded" else f"工具调用未重放（{decision}）",
                    "error_type": "idempotency" if decision != "already_succeeded" else None,
                    "tool_name": tool_name,
                    "recovered": decision == "already_succeeded",
                }
            if not callable(claim):
                receipt_store.mark_started(receipt_key, tool_name, start_time)

        if handler is None:
            logger.warning("tool_executor: unavailable tool '%s'", tool_name)
            result = {
                "success": False,
                "error": missing_error or f"工具 '{tool_name}' 尚未实现执行处理器。",
                "tool_name": tool_name,
                "duration_ms": (time.time() - start_time) * 1000,
            }
            if receipt_store is not None and receipt_key:
                from app.services.tools.receipts import ToolReceipt, ToolReceiptStatus
                receipt_store.mark_failed(receipt_key, tool_name, time.time(), error_type=error_type)
            return result

        try:
            result = await handler(arguments)
            duration_ms = (time.time() - start_time) * 1000
            logger.info(
                "tool_executor: tool '%s' executed in %.0fms success=%s",
                tool_name, duration_ms, result.get("success", False) if isinstance(result, dict) else True,
            )

            if isinstance(result, dict):
                result.setdefault("tool_name", tool_name)
                result.setdefault("duration_ms", duration_ms)
                # Apply result storage budget if configured
                if self.result_storage is not None:
                    result = self.result_storage.process(result)
                if receipt_store is not None and receipt_key:
                    from app.services.tools.receipts import ToolReceipt, ToolReceiptStatus
                    digest = hashlib.sha256(repr(result).encode("utf-8")).hexdigest()
                    receipt_store.put(ToolReceipt(
                        receipt_key,
                        tool_name,
                        ToolReceiptStatus.SUCCEEDED if result.get("success", True) else ToolReceiptStatus.FAILED,
                        time.time(),
                        result_digest=digest if result.get("success", True) else None,
                        error_type=str(result.get("error_type") or "tool_failure") if not result.get("success", True) else None,
                    ))
                return result

            final_result = {
                "success": True,
                "result": result,
                "tool_name": tool_name,
                "duration_ms": duration_ms,
            }
            if self.result_storage is not None:
                final_result = self.result_storage.process(final_result)
            if receipt_store is not None and receipt_key:
                from app.services.tools.receipts import ToolReceipt, ToolReceiptStatus
                digest = hashlib.sha256(repr(final_result).encode("utf-8")).hexdigest()
                receipt_store.put(ToolReceipt(receipt_key, tool_name, ToolReceiptStatus.SUCCEEDED, time.time(), result_digest=digest))
            return final_result
        except asyncio.CancelledError:
            if receipt_store is not None and receipt_key:
                receipt_store.mark_unknown(
                    receipt_key,
                    tool_name,
                    time.time(),
                    error_type="cancelled",
                )
            raise
        except Exception as exc:
            duration_ms = (time.time() - start_time) * 1000
            logger.exception("tool_executor: tool '%s' raised exception", tool_name)

            # Classify the error
            from app.services.tools.errors import classify_tool_error

            error_type, safe_message = classify_tool_error(exc, tool_name)
            result = {
                "success": False,
                "error": safe_message,
                "error_type": error_type.value,
                "exception_type": type(exc).__name__,
                "tool_name": tool_name,
                "duration_ms": duration_ms,
            }
            if receipt_store is not None and receipt_key:
                from app.services.tools.receipts import ToolReceipt, ToolReceiptStatus
                receipt_store.put(ToolReceipt(receipt_key, tool_name, ToolReceiptStatus.FAILED, time.time(), error_type=error_type.value))
            return result

    async def execute_gateway(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        handler: Callable[[Mapping[str, Any]], Awaitable[Any]] | None,
        *,
        idempotency_key: str,
        risk_level: str = "L1",
        requires_user_confirmation: bool = False,
        approval_callback: Callable[[str, Mapping[str, Any]], Awaitable[bool] | bool] | None = None,
        permission_context: Any = None,
        receipt_store: Any = None,
    ) -> dict[str, Any]:
        """Canonical risk, approval, receipt and execution gateway."""
        if handler is None:
            registry_tool = tool_registry.get(tool_name)
            if registry_tool is not None and registry_tool.handler is not None:
                handler = lambda payload: registry_tool.handler(**dict(payload))
                risk_level = registry_tool.risk_level
                requires_user_confirmation = registry_tool.requires_user_confirmation
        effective_confirmation = requires_user_confirmation or tool_name in SIDE_EFFECTING_TOOLS
        try:
            from app.services.guardrails import classify_tool_risk
            risk_result = classify_tool_risk(tool_name, dict(arguments))
        except (ImportError, RuntimeError, ValueError):
            risk_result = None
        if risk_result is not None and risk_result.blocked:
            return self._gateway_failure(tool_name, idempotency_key, receipt_store, "safety_block", "tool call blocked by safety policy")
        if risk_result is not None and risk_result.requires_confirmation:
            effective_confirmation = True
        if self.permission_manager is not None and permission_context is not None:
            try:
                permission = await self.permission_manager.check(
                    tool_name, dict(arguments), permission_context,
                    risk_level=risk_level, requires_user_confirmation=effective_confirmation,
                )
                behavior = str(getattr(permission, "behavior", "")).lower()
                if behavior.endswith("deny"):
                    return self._gateway_failure(tool_name, idempotency_key, receipt_store, "permission", str(getattr(permission, "reason", "permission denied")))
                if behavior.endswith("ask"):
                    effective_confirmation = True
            except (RuntimeError, ValueError, TypeError):
                return self._gateway_failure(tool_name, idempotency_key, receipt_store, "permission", "permission evaluation failed")
        if effective_confirmation:
            allowed = False
            if approval_callback is not None:
                decision = approval_callback(tool_name, arguments)
                allowed = bool(await decision) if inspect.isawaitable(decision) else bool(decision)
            if not allowed:
                return self._gateway_failure(tool_name, idempotency_key, receipt_store, "permission", "tool approval was not granted")
        return await self.execute_callable(tool_name, arguments, handler, idempotency_key=idempotency_key, receipt_store=receipt_store)

    @staticmethod
    def _gateway_failure(tool_name: str, idempotency_key: str, receipt_store: Any, error_type: str, message: str) -> dict[str, Any]:
        result = {"success": False, "error": message, "error_type": error_type, "tool_name": tool_name, "recovered": False}
        if receipt_store is not None and idempotency_key:
            try:
                from app.services.tools.receipts import ToolReceipt, ToolReceiptStatus
                receipt_store.put(ToolReceipt(idempotency_key, tool_name, ToolReceiptStatus.FAILED, time.time(), error_type=error_type))
            except Exception:
                logger.exception("tool_executor: failed to persist gateway denial receipt")
        return result

    async def execute_all(
        self,
        tool_calls: list[dict[str, Any]],
        streaming_executor: Any = None,
    ) -> list[dict[str, Any]]:
        """Execute a batch of tool calls in parallel with per-tool timeout.

        Each tool gets a 15-second timeout. All tools run concurrently via
        asyncio.gather for maximum throughput. A tool that times out returns
        a structured error rather than hanging the entire batch.

        When *streaming_executor* is provided, delegates to its
        process_queue() for concurrent-safety-aware execution.
        """
        import asyncio

        # Delegate to streaming executor if available
        if streaming_executor is not None:
            for tc in tool_calls:
                from app.services.tool_registry import tool_registry

                name = tc.get("name", "")
                streaming_executor.enqueue(
                    name=name,
                    arguments=tc.get("arguments", {}),
                    is_concurrency_safe=tool_registry.get_concurrency_safety(name),
                )
            return await streaming_executor.process_queue()

        PER_TOOL_TIMEOUT = 15  # seconds per individual tool

        async def run_one(tc: dict[str, Any]) -> dict[str, Any]:
            name = tc.get("name", "")
            args = tc.get("arguments", {})
            try:
                return await asyncio.wait_for(
                    self.execute(
                        name,
                        args,
                        idempotency_key=(
                            str(tc.get("idempotency_key") or tc.get("idempotencyKey"))
                            if tc.get("idempotency_key") or tc.get("idempotencyKey")
                            else None
                        ),
                    ),
                    timeout=PER_TOOL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "tool_executor: tool '%s' timed out after %ds",
                    name, PER_TOOL_TIMEOUT,
                )
                return {
                    "success": False,
                    "error": f"工具 '{name}' 执行超时（{PER_TOOL_TIMEOUT}秒）",
                    "error_type": "timeout",
                    "tool_name": name,
                    "duration_ms": PER_TOOL_TIMEOUT * 1000,
                }
            except Exception as exc:
                logger.error(
                    "tool_executor: tool '%s' unexpected error: %s",
                    name, exc,
                )
                from app.services.tools.errors import classify_tool_error

                error_type, safe_msg = classify_tool_error(exc, name)
                return {
                    "success": False,
                    "error": safe_msg,
                    "error_type": error_type.value,
                    "tool_name": name,
                }

        # Fire all tools concurrently — no sequential waiting
        coros = [run_one(tc) for tc in tool_calls]
        results = await asyncio.gather(*coros)
        return list(results)

    # ── Loop utility ──────────────────────────────────────────────────

    @staticmethod
    def build_tool_result_context(results: list[dict[str, Any]]) -> str:
        """Format tool execution results for injection back into the LLM context.

        Returns a string like:
        【工具调用结果】
        工具: web_search — 成功
        返回: {"results": [...]}

        工具: file_read — 失败
        错误: 路径超出允许范围
        """
        if not results:
            return ""

        lines = ["【工具调用结果】"]
        for r in results:
            tool_name = r.get("tool_name", "unknown")
            success = "成功" if r.get("success") else "失败"
            lines.append(f"\n工具: {tool_name} — {success}")
            if r.get("success"):
                result_data = r.get("result", "")
                if isinstance(result_data, str):
                    lines.append(f"返回数据:\n{result_data}")
                else:
                    lines.append(f"返回数据:\n{json.dumps(result_data, ensure_ascii=False, indent=2)}")
            else:
                lines.append(f"错误: {r.get('error', '未知错误')}")
            if r.get("duration_ms"):
                lines.append(f"耗时: {r['duration_ms']:.0f}ms")

        return "\n".join(lines)


# ── Singleton ──────────────────────────────────────────────────────────
tool_executor = ToolExecutor()
