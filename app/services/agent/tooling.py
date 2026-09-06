from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from app.db.init_db import now
from app.db.session import aexecute
from app.services.adapter_manager import adapter_manager
from app.services.prompt_messages import split_prompt_for_adapter
from app.services.token_budget import (
    fit_prompt,
)
from app.services.secret_service import decrypt_secret
from app.services.text_processing import (
    filter_streaming_chunk,
    is_code_request,
    is_codegen_json_response,
    latex_to_unicode,
    remove_repeated_text,
    reset_stream_filter,
    strip_codegen_prefix,
    strip_kimi_thinking,
    strip_think_tags,
)

# Backward-compatible aliases for internal use
_filter_streaming_chunk = filter_streaming_chunk
_is_code_request = is_code_request
_is_codegen_json_response = is_codegen_json_response
_latex_to_unicode = latex_to_unicode
_remove_repeated_text = remove_repeated_text
_reset_stream_filter = reset_stream_filter
_strip_codegen_prefix = strip_codegen_prefix
_strip_kimi_thinking = strip_kimi_thinking
_strip_think_tags = strip_think_tags

from app.services.symbolic import (
    generate_symbolic_message,
    public_symbolic,
)

logger = logging.getLogger("agenthub.agent.tooling")

# Cross-module refs kept local to this package to avoid import cycles.
from app.services.agent.context import (  # noqa: E402
    _build_attachment_context,
    _build_quote_context,
    _format_conversation,
    _get_agent_tools,
    _intent_from_domain,
    build_prompt,
)
from app.services.agent.persistence import (  # noqa: E402
    _enter_degradation,
    _schedule_recovery_check,
    save_message,
)
from app.services.agent.routing import _race_models, _update_runtime  # noqa: E402
from app.services.agent.vision_turn import (  # noqa: E402
    decide_turn_vision,
    extract_screenshot_uris,
)
from app.services.agent.circuit_breaker import ToolLoopCircuitBreaker  # noqa: E402
from app.services.model_contract import ToolCall


async def _log_tool_call(session_id: str, agent_id: str, tool_name: str, arguments: dict, result: dict) -> None:
    """Log a tool call to the audit table (best-effort, non-fatal)."""
    try:
        await aexecute(
            "INSERT INTO tool_call_log (id, session_id, agent_id, tool_name, arguments_json, result_json, success, duration_ms, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            str(uuid.uuid4()),
            session_id,
            agent_id,
            tool_name,
            json.dumps(arguments, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
            1 if result.get("success") else 0,
            int(result.get("duration_ms", 0)),
            now(),
        )
    # noqa: BLE001 - tool registry best-effort
    except Exception:
        pass  # table may not exist yet

async def _run_tool_call_loop(
    session_id: str,
    content: str,
    user_id: str,
    agent: dict,
    domain: str,
    llm_input: str,
    models: list[dict],
    history: str,
    memory_ctx: str,
    collab_ctx: str = "",
    token: Any = None,
    on_tool_event: Any = None,
    streaming_executor: Any = None,
    stream_callback: Any = None,
    preprocess_context: str = "",
    simple_mode: bool = False,
    image_parts: list[dict[str, Any]] | None = None,
) -> tuple[str, dict, dict]:
    """Execute the tool-calling loop for a single user message.

    1. Call LLM with tool-enabled prompt.
    2. Check response for tool_calls JSON.
    3. Execute tools, append results, re-call LLM.
    4. If no tool calls: return final text.
    5. Max iterations: 5. Overall timeout: 180s.

    When *stream_callback* is an async callable, the final synthesis
    iteration uses real SSE streaming via ``adapter.stream_prompt()``
    and feeds every chunk through the callback as it arrives.
    """
    import asyncio as _asyncio
    from app.services.tool_executor import tool_executor
    from app.services.tool_registry import tool_registry

    executor = tool_executor
    conversation: list[dict] = [{"role": "user", "content": llm_input}]
    available_tools = await _get_agent_tools(agent["agent_id"])

    # ── Set tool execution context for agent-invocation tools ─────────
    # invoke_agent / invoke_agents_parallel need session context to look
    # up agents and save messages.  We set contextvars before the loop
    # so every tool handler can access the current session params.
    from app.services.tools.agent_tools import set_tool_context

    set_tool_context(
        session_id=session_id,
        user_id=user_id,
        token=token,
        on_tool_event=on_tool_event,
    )

    # Also set session context for session-scoped tools (artifact, conversation)
    from app.services.tools.session_tools import set_session_tool_context as _set_sess_ctx
    _set_sess_ctx(session_id)

    LOOP_TIMEOUT = 1800  # 30-minute overall safety cap (was 180s — too short for complex generation)

    all_tool_names = tool_registry.list_names()
    logger.info(
        "tool_loop start: agent=%s domain=%s all_tools=%s available_tools=%s",
        agent["agent_id"], domain, all_tool_names,
        available_tools if available_tools is not None else "<all>",
    )

    final_text = ""
    usage: dict = {}
    selected = models[0] if models else {"provider": "mock", "model_name": "mock", "api_key": "", "base_url": ""}
    adapter = None
    # MM-2/ADR-0105: inline image parts ride along the first user turn as
    # structured content; adapters fail closed on non-vision models.
    _image_parts = list(image_parts or [])
    # MM-4: screenshots produced by tools queue here for the NEXT turn —
    # either as vision parts (vision-capable model) or a text description.
    _queued_vision: list[str] = []

    # ── System prefix cache key for this tool-call loop ────────────
    # The system prefix (everything before "符号消息:") is identical
    # across all iterations.  We build it once on iteration 0 and
    # reuse on iterations 1-4, appending only the dynamic conversation
    # content.  This saves ~3-6KB of repeated string construction per
    # iteration.
    _loop_prefix_cached: str | None = None  # populated on iteration 0

    async def _loop_body() -> tuple[str, dict, dict]:
        nonlocal final_text, usage, selected, adapter, _loop_prefix_cached
        # ── Circuit breaker state (three-tier detection lives in
        # app/services/agent/circuit_breaker.py) ──────────────────────────
        _breaker = ToolLoopCircuitBreaker()
        for iteration in range(executor.MAX_ITERATIONS):
            # ── Respect cancellation token ────────────────────────────
            if token and token.cancelled:
                logger.info("tool_loop cancelled at iteration %d", iteration)
                return "流式响应已被中断。", usage, selected

            # ★ 改进3: 发送迭代进度事件 — 让前端知道工具循环正在进行
            if iteration > 0 and on_tool_event:
                try:
                    await on_tool_event("agent_thinking", {
                        "messageId": f"tool-loop-iter-{iteration}",
                        "agentId": agent["agent_id"],
                        "phase": "executing",
                        "details": f"正在执行第 {iteration + 1}/{executor.MAX_ITERATIONS} 轮工具调用...",
                    })
                # noqa: BLE001 - tool registry best-effort
                except Exception:
                    pass  # 进度事件失败不影响工具执行

            conv_text = _format_conversation(conversation)
            symbolic = generate_symbolic_message(
                conv_text, "text", session_id,
                sender_role=agent["agent_id"],
                intent_type=_intent_from_domain(domain, conv_text),
                risk_level=agent.get("risk_level", "L1"),
            )

            # ── Build prompt with system prefix caching ──────────────
            # The system prefix (role instructions, tool definitions,
            # memory context, etc.) is typically 3-6KB and identical
            # across all iterations of the tool-call loop.  We build it
            # once on iteration 0, cache it, and reuse on iterations
            # 1-4 — appending only the dynamic conversation content.
            if _loop_prefix_cached is not None:
                # Reuse cached system prefix; only rebuild the user suffix
                collab_suffix = f"\n\n{collab_ctx}" if collab_ctx else ""
                user_suffix = (
                    f"{collab_suffix}"
                    f"符号消息: {json.dumps(public_symbolic(symbolic), ensure_ascii=False)}\n"
                    f"用户需求: {conv_text}"
                )
                prompt = _loop_prefix_cached + user_suffix
            else:
                from app.services.tools.permission import get_permission_mode_for_session

                prompt = await build_prompt(
                    agent["agent_id"], domain, conv_text, symbolic,
                    models[0].get("prompt", "") if models else "",
                    collab_ctx, history, memory_ctx,
                    tools_enabled=not simple_mode, available_tools=available_tools,
                    model_provider=(models[0].get("provider", "") if models else ""),
                    model_name=(models[0].get("model_name", "") if models else ""),
                    preprocess_context=preprocess_context,
                    permission_mode=get_permission_mode_for_session(session_id).value,
                )

            primary_model = models[0] if models else {}

            # ── Decide what rides THIS call as vision (MM-2/3/4) ────────
            extra_parts, describe_note, billed_images = await decide_turn_vision(
                queued_vision=_queued_vision,
                image_parts=_image_parts,
                iteration=iteration,
                provider=str(primary_model.get("provider", "")),
                model_name=str(primary_model.get("model_name", "")),
            )

            prompt, prompt_budget_stats = fit_prompt(
                prompt,
                primary_model.get("provider", ""),
                primary_model.get("model_name", ""),
                anchor="符号消息:",
                image_count=billed_images,
            )
            from app.services.performance_monitor import monitor
            monitor.record_token_compaction(
                "prompt",
                int(prompt_budget_stats["tokens_before"]),
                int(prompt_budget_stats["tokens_after"]),
                bool(prompt_budget_stats["truncated"]),
            )

            # Send the static prefix once as the system message. Only the
            # dynamic symbolic message and conversation are user content.
            system_prefix, user_prompt = split_prompt_for_adapter(prompt)
            if system_prefix:
                _loop_prefix_cached = system_prefix
                prompt = user_prompt

            # Dual-track user content (ADR-0105): image-bearing turns are a
            # parts list [image..., text]; plain turns stay strings.
            if extra_parts:
                call_content: str | list[dict[str, Any]] = [
                    *extra_parts,
                    {"type": "text", "text": prompt + describe_note},
                ]
            elif _image_parts and iteration == 0:
                call_content = [
                    *_image_parts, {"type": "text", "text": prompt},
                ]
            elif describe_note:
                call_content = prompt + describe_note
            else:
                call_content = prompt

            logger.info(
                "tool_loop iter=%d: prompt_len=%d has_tool_section=%s",
                iteration, len(prompt),
                "tool_calls" in prompt.lower(),
            )

            # Try each model — race on iteration 0, serial fallback thereafter
            result = ""
            errors: list[str] = []
            native_tools = None
            if iteration == 0:
                native_tools = (
                    tool_registry.build_openai_tools(available_tools)
                    if available_tools is not None
                    else tool_registry.build_openai_tools()
                )
                if native_tools:
                    logger.info(
                        "tool_loop iter=0: using native function calling with %d tools",
                        len(native_tools),
                    )

            if iteration == 0 and stream_callback and not native_tools:
                # ── Direct streaming (no tools → stream from best model) ──
                # When no native function-calling tools are configured,
                # stream the response directly from the top-ranked model.
                # We no longer race models for text-only chat because the
                # latency benefit (~200-500ms) doesn't justify wasting 50%
                # of API credits on a cancelled concurrent call.
                model = models[0] if models else {"provider": "mock", "model_name": "mock"}
                selected = model
                adapter = adapter_manager.get_adapter(model.get("provider", "mock"))
                started = time.perf_counter()
                gathered: list[str] = []
                try:
                    async for chunk in adapter.stream_prompt(
                        call_content,
                        model.get("model_name", "mock"),
                        decrypt_secret(model.get("api_key", "")),
                        model.get("base_url", ""),
                        system_prompt=_loop_prefix_cached or "",
                    ):
                        if chunk:
                            gathered.append(chunk)
                            await stream_callback(chunk)
                    elapsed = (time.perf_counter() - started) * 1000
                    _update_runtime(model, True, elapsed)
                    final_text = "".join(gathered)
                    logger.info(
                        "tool_loop iter=%d direct_stream: provider=%s model=%s elapsed=%.0fms len=%d",
                        iteration, model.get("provider"), model.get("model_name"),
                        elapsed, len(final_text),
                    )
                    break
                except Exception as exc:
                    elapsed = (time.perf_counter() - started) * 1000
                    _update_runtime(model, False, elapsed)
                    errors.append(f"{model.get('provider')}/{model.get('model_name')}: {exc}")
                    logger.warning(
                        "tool_loop iter=%d direct_stream_fail: provider=%s model=%s error=%s",
                        iteration, model.get("provider"), model.get("model_name"), exc,
                    )
                    if len(models) > 1:
                        # Fall through to serial fallback with remaining models
                        result = ""
                    else:
                        final_text = "模型调用失败，已降级为本地响应：" + " | ".join(errors[:2])
                        failed_model_ids = [f"{m.get('provider')}/{m.get('model_name')}" for m in models[:3]]
                        await _enter_degradation(
                            session_id, " | ".join(errors[:2]), failed_model_ids,
                        )
                        asyncio.create_task(_schedule_recovery_check(session_id))
                        break

            elif iteration == 0 and len(models) >= 2:
                # ── Race top models concurrently (first success wins) ──
                result, selected, adapter, errors = await _race_models(
                    call_content, models, iteration, native_tools, token,
                    system_prompt=_loop_prefix_cached or "",
                )
                if result:
                    logger.info(
                        "tool_loop iter=%d race_win: provider=%s model=%s result_len=%d",
                        iteration, selected.get("provider"), selected.get("model_name"),
                        len(result),
                    )
            else:
                # ── Serial fallback (single model or post-tool-call iterations) ──
                for model in models:
                    if token and token.cancelled:
                        return "流式响应已被中断。", usage, selected
                    selected = model
                    adapter = adapter_manager.get_adapter(model.get("provider", "mock"))
                    started = time.perf_counter()
                    try:
                        result = await adapter.execute_prompt(
                            call_content,
                            model.get("model_name", "mock"),
                            decrypt_secret(model.get("api_key", "")),
                            model.get("base_url", ""),
                            tools=native_tools if native_tools else None,
                            system_prompt=_loop_prefix_cached or "",
                        )
                        elapsed = (time.perf_counter() - started) * 1000
                        _update_runtime(model, True, elapsed)
                        logger.info(
                            "tool_loop iter=%d llm_call: provider=%s model=%s elapsed=%.0fms result_len=%d",
                            iteration, model.get("provider"), model.get("model_name"),
                            elapsed, len(result),
                        )
                        break
                    except Exception as exc:
                        elapsed = (time.perf_counter() - started) * 1000
                        _update_runtime(model, False, elapsed)
                        errors.append(f"{model.get('provider')}/{model.get('model_name')}: {exc}")
                        logger.warning(
                            "tool_loop iter=%d llm_fail: provider=%s model=%s elapsed=%.0fms error=%s",
                            iteration, model.get("provider"), model.get("model_name"),
                            elapsed, exc,
                        )
                        result = ""

            if token and token.cancelled:
                return "流式响应已被中断。", usage, selected

            if not result:
                final_text = "模型调用失败，已降级为本地响应：" + " | ".join(errors[:2])
                # Enter degradation mode and notify frontend
                failed_model_ids = [f"{m.get('provider')}/{m.get('model_name')}" for m in models[:3]]
                await _enter_degradation(
                    session_id, " | ".join(errors[:2]), failed_model_ids,
                )
                # Schedule a background recovery check
                asyncio.create_task(_schedule_recovery_check(session_id))
                break

            # Check for tool calls
            has_tc = executor.has_tool_calls(result)
            logger.info(
                "tool_loop iter=%d: has_tool_calls=%s result_preview=%s",
                iteration, has_tc, result[:200].replace("\n", "\\n"),
            )
            if has_tc:
                tool_calls = [ToolCall(id=str(tc.get("id") or f"legacy-call-{iteration}-{index}"), name=str(tc.get("name") or ""), arguments=tc.get("arguments") if isinstance(tc.get("arguments"), dict) else {}) for index, tc in enumerate(executor.parse_tool_calls(result)) if isinstance(tc, dict) and tc.get("name")]
                logger.info(
                    "tool_loop iter=%d: parsed %d tool_calls: %s",
                    iteration, len(tool_calls),
                    [tc.name for tc in tool_calls],
                )
                if tool_calls:
                    if token and token.cancelled:
                        return "流式响应已被中断。", usage, selected

                    # ── Soft cap: at iteration 12, strongly nudge the LLM ──
                    #     to synthesize instead of calling more tools.
                    #     This prevents the "analysis paralysis" pattern where
                    #     the Orchestrator keeps searching/planning without
                    #     ever producing a final answer.
                    if iteration == 12:
                        conversation.append({
                            "role": "user",
                            "content": (
                                "【系统提示】你已经进行了多轮工具调用。"
                                "请基于已有结果生成最终回复，不要再调用新工具。"
                                "如果信息不足，请诚实告知用户当前进展和缺失的部分。"
                            ),
                        })

                    # Notify frontend
                    if on_tool_event:
                        try:
                            await on_tool_event("calling", tool_calls, None)
                        # noqa: BLE001 - tool registry best-effort
                        except Exception:
                            pass

                    # Execute tools
                    # ── Guardrail: classify each tool's risk before execution ──
                    from app.services.guardrails import classify_tool_risk as _ctr
                    from app.services.tools.permission import (
                        get_permission_mode_for_session,
                        PermissionMode,
                    )
                    high_risk_tools: list[dict] = []
                    for tc in tool_calls:
                        risk = _ctr(tc.name, tc.arguments)
                        if risk.requires_confirmation:
                            high_risk_tools.append({
                                "name": tc.name,
                                "arguments": dict(tc.arguments),
                                "risk": risk.to_dict(),
                            })
                    # BYPASS（跳过权限）模式下不重复弹风险警告：
                    # 用户已明确表示"自动放行一切"，PermissionManager 也会返回 ALLOW，
                    # 此时再发 risk_warning 反而会让用户觉得"切了跟没切一样"。
                    current_mode = get_permission_mode_for_session(session_id)
                    if high_risk_tools and on_tool_event and current_mode != PermissionMode.BYPASS:
                        try:
                            await on_tool_event("risk_warning", high_risk_tools, None)
                        except Exception:
                            pass
                    elif high_risk_tools and current_mode == PermissionMode.BYPASS:
                        logger.info(
                            "tool_loop risk_warning 跳过: session=%s mode=bypass 涉及工具=%s",
                            session_id, [t["name"] for t in high_risk_tools],
                        )

                    if streaming_executor is not None:
                        streaming_executor.set_context(session_id=session_id, agent_id=agent["agent_id"], user_id=user_id)
                        for tc in tool_calls:
                            name = tc.name
                            streaming_executor.enqueue(
                                name=name,
                                arguments=dict(tc.arguments),
                                is_concurrency_safe=tool_registry.get_concurrency_safety(name),
                            )
                        tool_results = await streaming_executor.process_queue()
                    else:
                        tool_results = await executor.execute_all(tool_calls)

                    # MM-4: hijack screenshot payloads before they reach the
                    # text context; queue them for the next model turn.
                    _fresh_uris, tool_results = extract_screenshot_uris(tool_results)
                    if _fresh_uris:
                        _queued_vision.extend(_fresh_uris)
                        logger.info(
                            "tool_loop iter=%d: queued %d screenshot(s) for vision refeed",
                            iteration, len(_fresh_uris),
                        )

                    if on_tool_event:
                        try:
                            await on_tool_event("done", tool_calls, tool_results)
                        # noqa: BLE001 - tool registry best-effort
                        except Exception:
                            pass

                    # Log tool calls (best-effort)
                    try:
                        for tc, tr in zip(tool_calls, tool_results):
                            await _log_tool_call(session_id, agent["agent_id"], tc.name, dict(tc.arguments), tr)
                    # noqa: BLE001 - tool registry best-effort
                    except Exception:
                        pass

                    conversation.append({"role": "assistant", "tool_calls": tool_calls})
                    conversation.append({"role": "tool", "results": tool_results})

                    # ── Circuit breaker: three-tier failure detection ───────
                    _event = _breaker.assess(tool_results, iteration)
                    if _event is not None:
                        final_text = executor.build_tool_result_context(tool_results)
                        if on_tool_event:
                            try:
                                await on_tool_event(
                                    "circuit_breaker", tool_calls,
                                    _event.tools or [{"tier": _event.tier}])
                            # noqa: BLE001 - tool registry best-effort
                            except Exception:
                                pass
                        break

                    if iteration >= executor.MAX_ITERATIONS - 1:
                        final_text = executor.build_tool_result_context(tool_results)
                        break

                    # ★ 改进3: 工具执行完成后发送 "synthesizing" 阶段事件
                    if on_tool_event:
                        try:
                            await on_tool_event("agent_thinking", {
                                "messageId": f"tool-loop-synth-{iteration}",
                                "agentId": agent["agent_id"],
                                "phase": "synthesizing",
                                "details": "工具执行完成，正在综合结果生成回复...",
                            })
                        # noqa: BLE001 - tool registry best-effort
                        except Exception:
                            pass

                    continue  # loop back for synthesis

            # No tool calls found — this is the synthesis iteration.
            # If native tools were used and didn't produce tool_calls,
            # retry with prompt-based calling only (reasoning models
            # often don't support native function calling).
            if iteration == 0 and native_tools:
                logger.info(
                    "tool_loop iter=%d: native tools produced no tool_calls, "
                    "retrying with prompt-based calling only",
                    iteration,
                )
                continue  # next iteration will NOT pass native tools

            if stream_callback:
                # ── Real SSE streaming for the final synthesis ──
                logger.info(
                    "tool_loop iter=%d: streaming synthesis via stream_prompt",
                    iteration,
                )
                gathered: list[str] = []
                async for chunk in adapter.stream_prompt(
                    call_content,
                    model.get("model_name", "mock"),
                    decrypt_secret(model.get("api_key", "")),
                    model.get("base_url", ""),
                    system_prompt=_loop_prefix_cached or "",
                ):
                    if chunk:
                        gathered.append(chunk)
                        await stream_callback(chunk)
                final_text = "".join(gathered)
            else:
                final_text = result
            break

        if not final_text:
            final_text = "工具调用后未能生成回复。"

        usage_dict = adapter.last_usage if adapter else {}
        return final_text, usage_dict, selected

    # ── Execute with overall timeout ──────────────────────────────
    try:
        final_text, usage_dict, selected = await _asyncio.wait_for(
            _loop_body(), timeout=LOOP_TIMEOUT,
        )
    except _asyncio.TimeoutError:
        logger.error(
            "tool_loop timeout after %ds session=%s agent=%s",
            LOOP_TIMEOUT, session_id, agent["agent_id"],
        )
        final_text = (
            f"工具调用超时（{LOOP_TIMEOUT}秒限制）。"
            "请尝试简化问题或稍后重试。"
        )
        usage_dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "note": "timeout"}
        selected = models[0] if models else {
            "provider": "mock", "model_name": "mock", "api_key": "", "base_url": "",
        }

    # ── Record tool-call loop for performance monitoring ──────────
    try:
        from app.services.performance_monitor import monitor
        monitor.record_tool_call_loop(1)  # count each complete loop
    # noqa: BLE001 - tool registry best-effort
    except Exception:
        pass

    return final_text, usage_dict, selected

async def _stream_cloudcode_response(
    session_id: str,
    content: str,
    user_id: str,
    agent: dict,
    token: Any = None,
    attachments: list[dict[str, Any]] | None = None,
    quote_references: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[str, None] | None:
    """Stream a CloudCode (subprocess-based) agent response.

    Unlike the standard tool-call loop which calls HTTP LLM APIs, this
    function talks to a subprocess via JSON Lines on stdout.  Each JSON
    Line is parsed, dispatched as a WebSocket event to the frontend, and
    text chunks are yielded for the SSE stream.

    Two execution modes based on the adapter's :class:`SubprocessProtocol`:

    **Interactive mode** (Claude Code — ``supports_interactive() == True``)
        stdin stays open.  When the CLI produces a ``tool_use`` event,
        AgentHub executes the tool and feeds the result back via
        ``adapter.send_input()``.  The CLI continues processing and
        produces its final ``end`` event only when done.

    **One-shot mode** (Codex CLI, OpenClaw — ``supports_interactive() == False``)
        stdin is closed after the initial prompt.  Tool calls are
        broadcast to the frontend for display only (no feedback loop).
        This is the same behaviour as the pre-protocol implementation.

    Parameters
    ----------
    session_id : str
    content : str
        The user's prompt (passed to subprocess stdin).
    user_id : str
    agent : dict
        Agent registry row with at least ``agent_id`` and ``adapter_type``.
    token : StreamToken or None
        Cancellation token.
    """
    import asyncio as _asyncio

    from app.services.adapter_manager import adapter_manager
    from app.services.event_mapper import map_event, is_diff_event, is_terminal_event
    from app.services.websocket_manager import manager as ws_manager

    adapter_type = agent.get("adapter_type", "cloud_code")
    adapter = adapter_manager.get_adapter(adapter_type)
    message_id = str(uuid.uuid4())
    turn_id = str(uuid.uuid4())

    # ── Resolve protocol (may be None for generic cloud_code) ────────
    protocol = getattr(adapter, "protocol", None)
    use_interactive = protocol is not None and protocol.supports_interactive()

    # ── Thread / CLI session continuity ─────────────────────────────
    # When the CLI supports --resume, carry the cached session ID forward
    # so the subprocess can pick up where the last turn left off.
    thread_id = getattr(adapter, "_cli_session_id", None) or ""

    # Build attachment / quote context (same as normal stream path)
    attachment_context, _ = _build_attachment_context(attachments)
    quote_context = _build_quote_context(quote_references)
    llm_input = content
    if quote_context:
        llm_input = f"{quote_context}\n\n[用户当前问题]\n{content}"
    if attachment_context:
        llm_input = f"{llm_input}\n\n[用户上传附件上下文]\n{attachment_context}"

    full_text: list[str] = []
    tool_call_count = 0
    MAX_TOOL_ROUNDS = 10  # safety limit for interactive tool feedback loop

    async def stream():
        nonlocal full_text, tool_call_count, thread_id

        try:
            # ── Choose streaming strategy ──────────────────────────
            if use_interactive:
                line_iter = adapter.stream_prompt_interactive(
                    llm_input, adapter_type, turn_id=turn_id,
                )
            else:
                line_iter = adapter.stream_prompt(llm_input, adapter_type)

            async for json_line in line_iter:
                # Check cancellation
                if token and token.cancelled:
                    adapter.cancel()
                    yield "\n[已中断 CloudCode 执行]"
                    return

                if not json_line:
                    continue  # skip empty lines / sentinel (end-of-stream marker)

                # Parse JSON
                try:
                    obj = json.loads(json_line)
                except json.JSONDecodeError:
                    # Non-JSON line → treat as raw text chunk
                    full_text.append(json_line)
                    yield json_line
                    continue

                # ── Try to extract CLI session ID for --resume ──────
                if use_interactive and not thread_id and protocol is not None:
                    extracted = protocol.extract_session_id(obj)
                    if extracted:
                        thread_id = extracted
                        adapter._cli_session_id = extracted

                # ── Unified event mapping ───────────────────────────
                mapped = map_event(
                    obj, session_id, message_id, agent["agent_id"],
                    turn_id=turn_id, thread_id=thread_id,
                )

                if mapped is None:
                    # end / result / system events — handled below
                    evt_type = obj.get("type", "")
                    if evt_type in ("end", "result"):
                        # ── Finalise ────────────────────────────────
                        # Claude Code uses "result" (interactive) or "end" (one-shot)
                        final_text = (
                            obj.get("result", "")
                            or obj.get("content", "")
                            or "\n".join(full_text)
                        )
                        # Broadcast the final message to the frontend
                        if final_text or full_text:
                            display_text = final_text or "\n".join(full_text)
                            await ws_manager.broadcast(
                                session_id,
                                {
                                    "event": "message",
                                    "sessionId": session_id,
                                    "messageId": message_id,
                                    "turnId": turn_id,
                                    "threadId": thread_id,
                                    "content": display_text,
                                    "sender": agent["agent_id"],
                                    "timestamp": now(),
                                    "type": "text",
                                },
                            )

                        # Persist the message
                        try:
                            persist_text = final_text or "\n".join(full_text)
                            pt = max(1, len(llm_input) // 4)
                            ct = max(1, len(persist_text) // 4)
                            await save_message(
                                session_id, agent["agent_id"], persist_text, "text", 0.0,
                                public_symbolic(
                                    generate_symbolic_message(
                                        llm_input, "text", session_id,
                                        sender_role=agent["agent_id"],
                                        intent_type=_intent_from_domain(agent.get("domain", ""), content),
                                        risk_level=agent.get("risk_level", "L1"),
                                    )
                                ),
                                pt, ct, pt + ct,
                                user_id=user_id,
                            )
                        except Exception:
                            logger.debug("save_message failed in cloudcode stream", exc_info=True)

                        # Trigger post-agent pipeline (background)
                        _asyncio.create_task(
                            _run_cloudcode_post_hooks(session_id, agent["agent_id"])
                        )
                        return
                    # system events → skip silently
                    continue

                # ── Dispatch by mapped event type ───────────────────
                event_type = mapped.get("event", "")

                if event_type == "message_chunk":
                    chunk = mapped.get("content", "")
                    if chunk:
                        full_text.append(chunk)
                        yield chunk

                elif event_type == "tool_call":
                    tool_call_count += 1
                    tool_calls_list = mapped.get("toolCalls", [])
                    tool_use_id = ""
                    tool_name = "unknown"
                    if tool_calls_list:
                        tool_use_id = tool_calls_list[0].get("toolUseId", "")
                        tool_name = tool_calls_list[0].get("name", "unknown")

                    # Broadcast to frontend
                    await ws_manager.broadcast(session_id, mapped)

                    # ── Extra: diff_update for edit_file ────────────
                    if is_diff_event(obj):
                        await ws_manager.broadcast(
                            session_id,
                            {
                                "event": "diff_update",
                                "sessionId": session_id,
                                "messageId": message_id,
                                "turnId": turn_id,
                                "path": obj.get("path", ""),
                                "diff": obj.get("diff", ""),
                                "timestamp": now(),
                            },
                        )

                    # ── Extra: terminal_output for run_command ──────
                    if is_terminal_event(obj):
                        cmd_output = obj.get("output", obj.get("stdout", ""))
                        if cmd_output:
                            await ws_manager.broadcast(
                                session_id,
                                {
                                    "event": "terminal_output",
                                    "sessionId": session_id,
                                    "messageId": message_id,
                                    "turnId": turn_id,
                                    "content": cmd_output,
                                    "sender": agent["agent_id"],
                                    "timestamp": now(),
                                },
                            )

                    # ── Tool feedback loop (interactive mode only) ──
                    if use_interactive and protocol is not None and tool_call_count <= MAX_TOOL_ROUNDS:
                        # Execute the tool via AgentHub's tool executor
                        tool_args = tool_calls_list[0].get("arguments", {}) if tool_calls_list else {}
                        tool_result = await _execute_cli_tool(
                            tool_name, tool_args, session_id,
                        )
                        success = tool_result.get("success", False)

                        # Broadcast tool result to frontend
                        await ws_manager.broadcast(
                            session_id,
                            {
                                "event": "tool_result",
                                "sessionId": session_id,
                                "messageId": message_id,
                                "turnId": turn_id,
                                "threadId": thread_id,
                                "results": [
                                    {
                                        "tool_name": tool_name,
                                        "success": success,
                                        "result": tool_result.get("result", tool_result.get("error", "")),
                                    }
                                ],
                            },
                        )

                        # Feed result back to CLI (keeps generating)
                        encoded = protocol.encode_tool_result(
                            tool_use_id, tool_name, tool_result,
                            is_error=not success,
                        )
                        adapter.send_input(encoded)
                        # Continue the stream loop — CLI will produce more output

                    elif not use_interactive:
                        # One-shot mode: tool calls are broadcast for
                        # display only — the CLI process doesn't expect
                        # feedback and will exit on its own.
                        logger.info(
                            "cloudcode one-shot tool_call: %s (no feedback loop)",
                            tool_name,
                        )

                elif event_type == "tool_result":
                    # CLI-side tool_result (informational, not from our
                    # own executor) — broadcast to frontend
                    await ws_manager.broadcast(session_id, mapped)

        except Exception as exc:
            logger.exception("CloudCode stream crashed session=%s agent=%s", session_id, agent["agent_id"])
            yield f"\n[CloudCode 执行异常：{exc}]"

    return stream()

async def _execute_cli_tool(
    tool_name: str,
    arguments: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    """Execute a tool on behalf of a CLI subprocess agent.

    Reuses the same :class:`ToolExecutor` that the standard tool-call
    loop uses, so CLI agents get identical tool behaviour to HTTP-based
    agents.

    Returns a dict with ``{"success": bool, "result": Any, "error": str|null,
    "tool_name": str}``.
    """
    try:
        from app.services.tool_executor import tool_executor
        result = await tool_executor.execute(tool_name, arguments)
        return result
    except Exception as exc:
        logger.warning(
            "cli_tool_exec failed: tool=%s session=%s error=%s",
            tool_name, session_id, exc,
        )
        return {
            "success": False,
            "error": f"工具执行异常: {exc}",
            "tool_name": tool_name,
        }

async def _run_cloudcode_post_hooks(session_id: str, agent_id: str) -> None:
    """Background task: register artifacts and trigger pipeline after CloudCode completes."""
    try:
        from app.services.git_service import git_service
        from app.services.pipeline import run_post_agent_pipeline
        import uuid as _uuid

        # Check for changed files via git diff
        git_service.ensure_repo()
        diff_output = git_service.diff()
        diff_text = diff_output.get("diff", "")

        if diff_text.strip():
            changed_files = _parse_changed_files_from_diff(diff_text)
            for file_path in changed_files:
                try:
                    content = _read_file_content(file_path)
                    if content:
                        await aexecute(
                            "INSERT INTO artifacts(id, session_id, file_path, content, version, created_at) "
                            "VALUES($1,$2,$3,$4,$5,$6)",
                            str(_uuid.uuid4()), session_id, file_path, content, 1, now(),
                        )
                # noqa: BLE001 - tool registry best-effort
                except Exception:
                    pass

        await run_post_agent_pipeline(session_id, agent_id)

    except Exception:
        logger.debug("cloudcode post-hooks failed", exc_info=True)

def _parse_changed_files_from_diff(diff_text: str) -> list[str]:
    """Extract changed file paths from git diff output."""
    paths: list[str] = []
    for line in diff_text.split("\n"):
        m = re.match(r"^diff --git a/(.+) b/\1$", line)
        if m:
            paths.append(m.group(1))
    return list(set(paths))

def _read_file_content(file_path: str) -> str | None:
    """Read a file's content from the user's per-session workspace."""
    from pathlib import Path
    from app.services.workspace_context import get_workspace_root
    try:
        ws_root = get_workspace_root()
        full = ws_root / file_path if not Path(file_path).is_absolute() else Path(file_path)
        if full.exists() and full.is_file():
            return full.read_text(encoding="utf-8", errors="replace")
    # noqa: BLE001 - tool registry best-effort
    except Exception:
        pass
    return None
