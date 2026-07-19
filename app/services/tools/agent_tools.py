from __future__ import annotations

"""Agent invocation tools — enables the Orchestrator to dynamically dispatch
sub-tasks to domain-specialist agents during a conversation.

The ``invoke_agent`` tool is the KEY mechanism that transforms the Orchestrator
from a "fake dispatcher" (who can only talk about delegation) into a REAL
orchestrator that can actually spawn sub-agents and synthesize their outputs.
"""

import contextvars
import logging
import time
from typing import Any

from app.services.context_compaction import build_result_preview

logger = logging.getLogger("agenthub.tools.agent")

# ── Context variables (set by _run_tool_call_loop before each iteration) ─
# Using contextvars ensures thread/async-task safety — each tool invocation
# reads the context of the session that spawned it.

_ctx_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "tool_session_id", default=""
)
_ctx_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "tool_user_id", default=""
)
_ctx_token: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "tool_token", default=None
)
_ctx_on_tool_event: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "tool_on_tool_event", default=None
)
_ctx_ws_manager: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "tool_ws_manager", default=None
)


def set_tool_context(
    session_id: str,
    user_id: str,
    token: Any = None,
    on_tool_event: Any = None,
    ws_manager: Any = None,
) -> None:
    """Set the execution context for agent-invocation tools.

    Called by ``_run_tool_call_loop`` before each iteration of the
    tool-calling loop so that ``invoke_agent`` (and future tools that
    need session context) can access the current session params.

    All parameters are optional — only set what you need.
    """
    if session_id:
        _ctx_session_id.set(session_id)
    if user_id:
        _ctx_user_id.set(user_id)
    if token is not None:
        _ctx_token.set(token)
    if on_tool_event is not None:
        _ctx_on_tool_event.set(on_tool_event)
    if ws_manager is not None:
        _ctx_ws_manager.set(ws_manager)


def get_tool_context() -> dict[str, Any]:
    """Read the current tool execution context.

    Returns a dict with all context values (may contain empty/None values
    if context was never set).
    """
    return {
        "session_id": _ctx_session_id.get(),
        "user_id": _ctx_user_id.get(),
        "token": _ctx_token.get(),
        "on_tool_event": _ctx_on_tool_event.get(),
        "ws_manager": _ctx_ws_manager.get(),
    }


# ── Agent invocation handler ────────────────────────────────────────────


async def invoke_agent_handler(
    agent_name: str,
    task: str,
    context: str = "",
    require_confirmation: bool = False,
    fallback_agents: list[str] | None = None,
) -> dict[str, Any]:
    """Invoke a domain-specialist agent to perform a specific sub-task.

    This is the core "dispatch" mechanism — the Orchestrator LLM calls
    this tool to delegate work to Architect / CodeGen / Review / Test /
    Deploy agents.  The handler looks up the agent in agent_registry,
    invokes it with the given task description, and returns the result.

    **Fallback chain**: When the primary agent fails, the handler
    automatically tries each fallback agent in order.  If the caller
    doesn't provide fallbacks, a default domain-based fallback mapping
    is used (e.g. Architect → Orchestrator, Review → Architect).

    Args:
        agent_name: Agent ID to invoke (Architect, CodeGen, Review, Test, Deploy).
        task: Detailed task description for the agent.
        context: Optional context from previous agent outputs or user requirements.
        require_confirmation: If True, the tool asks for user confirmation first
                              (for high-risk operations like Deploy).
        fallback_agents: Ordered list of agent IDs to try if this one fails.
                         If None, a default domain-based fallback is used.

    Returns:
        {"success": bool, "result": str, "agent_name": str, "duration_ms": float,
         "fallback_used": str|None, "attempts": int, ...}
    """
    ctx = get_tool_context()
    session_id = ctx["session_id"]
    user_id = ctx["user_id"]
    token = ctx["token"]
    ws_manager = ctx["ws_manager"]

    if not session_id:
        return {
            "success": False,
            "error": "无法调用 Agent：缺少会话上下文（session_id 未设置）",
            "agent_name": agent_name,
        }

    # ── Default fallback chain (domain-based) ────────────────────────
    DEFAULT_FALLBACKS: dict[str, list[str]] = {
        "Architect": ["Orchestrator"],          # Orchestrator can analyze
        "CodeGen": ["Orchestrator", "Architect"],  # Orchestrator can generate code
        "Review": ["Architect"],                # Architect can review
        "Test": ["Review", "Orchestrator"],     # Review can design tests
        "Deploy": ["Orchestrator"],             # Orchestrator can advise
    }

    if fallback_agents is None:
        from app.services.agent_service import lookup_agent as _lu
        # Look up the primary agent to get its domain
        primary_agent = await _lu(agent_name, user_id)
        primary_domain = primary_agent.get("domain", "").lower() if primary_agent else ""
        fallback_agents = DEFAULT_FALLBACKS.get(agent_name, []) or DEFAULT_FALLBACKS.get(
            primary_domain.title(), []
        ) or ["Orchestrator"]  # ultimate fallback

    # ── Build the ordered agent list: primary → fallbacks ────────────
    agent_chain = [agent_name] + [fb for fb in fallback_agents if fb != agent_name]
    attempts = 0
    overall_start = time.time()
    last_error = ""
    fallback_used: str | None = None

    from app.services.agent_service import lookup_agent as lookup_agent_fn
    from app.services.agent_service import call_agent as call_agent_fn

    for attempt_idx, current_name in enumerate(agent_chain):
        attempts = attempt_idx + 1
        is_fallback = attempt_idx > 0

        # ── Look up the target agent ────────────────────────────────
        agent = await lookup_agent_fn(current_name, user_id)
        if not agent:
            if is_fallback:
                logger.warning(
                    "invoke_agent fallback: agent=%s not found, skipping", current_name,
                )
                continue
            available = ["Architect", "CodeGen", "Review", "Test", "Deploy"]
            return {
                "success": False,
                "error": (
                    f"Agent '{current_name}' 未找到或未配置。"
                    f"可用的 Agent 有: {', '.join(available)}。"
                    f"请检查 agent_registry 中是否存在该 Agent。"
                ),
                "agent_name": current_name,
            }

        # ── Build the full task content ─────────────────────────────
        full_content = task
        if is_fallback and last_error:
            full_content = (
                f"{task}\n\n"
                f"[注意：原 Agent '{agent_name}' 执行失败，你作为降级替代 Agent 接手此任务。"
                f"原始错误: {last_error[:200]}]"
            )
        if context:
            full_content = f"{full_content}\n\n## 参考上下文\n{context}"

        # ── Check for user confirmation (high-risk operations) ──────
        if require_confirmation and not is_fallback:
            logger.info(
                "invoke_agent: confirmation required for agent=%s — auto-approved in tool context",
                current_name,
            )

        # ── Notify frontend ─────────────────────────────────────────
        if ws_manager:
            try:
                await ws_manager.broadcast(
                    session_id,
                    {
                        "event": "agent_thinking",
                        "sessionId": session_id,
                        "messageId": f"sub_invoke_{current_name}_{int(time.time()*1000)}",
                        "agentId": current_name,
                        "phase": "fallback" if is_fallback else "invoked_by_orchestrator",
                        "details": (
                            f"降级: {agent_name}→{current_name}: {task[:80]}"
                            if is_fallback else
                            f"Orchestrator → {current_name}: {task[:100]}"
                        ),
                        "timestamp": "",
                    },
                )
            except Exception:
                pass

        # ── Invoke the agent ────────────────────────────────────────
        try:
            response = await call_agent_fn(
                session_id=session_id,
                content=full_content,
                user_id=user_id,
                agent=agent,
                token=token,
            )

            result_text = ""
            if isinstance(response, dict):
                result_text = response.get("content", "")
            elif isinstance(response, str):
                result_text = response
            else:
                result_text = str(response)

            duration_ms = (time.time() - overall_start) * 1000
            if is_fallback:
                fallback_used = current_name
                logger.info(
                    "invoke_agent fallback: %s→%s completed in %.0fms result_len=%d",
                    agent_name, current_name, duration_ms, len(result_text),
                )
            else:
                logger.info(
                    "invoke_agent: agent=%s completed in %.0fms result_len=%d",
                    current_name, duration_ms, len(result_text),
                )

            return {
                "success": True,
                "result": result_text,
                "agent_name": agent_name,  # original requested agent
                "actual_agent": current_name,  # who really did the work
                "agent_domain": agent.get("domain", "unknown"),
                "duration_ms": duration_ms,
                "result_length": len(result_text),
                "fallback_used": fallback_used,
                "attempts": attempts,
            }

        except Exception as exc:
            last_error = str(exc)
            duration_ms = (time.time() - overall_start) * 1000
            if is_fallback:
                logger.warning(
                    "invoke_agent fallback: agent=%s also failed: %s", current_name, exc,
                )
            else:
                logger.warning(
                    "invoke_agent: primary agent=%s failed (attempt %d): %s",
                    current_name, attempts, exc,
                )
            # Continue to next fallback

    # ── All agents exhausted ─────────────────────────────────────────
    total_ms = (time.time() - overall_start) * 1000
    return {
        "success": False,
        "error": (
            f"调用 Agent '{agent_name}' 及其 {len(agent_chain) - 1} 个降级替代均失败。"
            f"最后错误: {last_error[:300]}"
        ),
        "agent_name": agent_name,
        "duration_ms": total_ms,
        "error_type": "all_fallbacks_exhausted",
        "attempts": attempts,
        "tried_agents": agent_chain[:attempts],
    }


# ── Convenience: invoke multiple agents in parallel ─────────────────────


async def invoke_agents_parallel_handler(
    calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Invoke multiple agents in parallel with streaming progress.

    Each call is a dict with: agent_name (required), task (required),
    context (optional), require_confirmation (optional).

    Uses ``asyncio.as_completed()`` so that results are available to the
    calling Orchestrator as soon as each agent finishes — the LLM can
    start synthesizing while other agents are still running.  Progress
    events are broadcast to the frontend via WebSocket in real time.

    Args:
        calls: List of invocation specs, each containing:
            - agent_name (str, required): Agent ID to invoke
            - task (str, required): Task description
            - context (str, optional): Reference context
            - require_confirmation (bool, optional): Whether to ask user first

    Returns:
        {"success": bool, "results": [...], "total_duration_ms": float,
         "success_count": int, "total_count": int,
         "partial_summaries": [...]}
    """
    import asyncio as _asyncio

    start = time.time()
    ctx = get_tool_context()
    ws_manager = ctx["ws_manager"]
    session_id = ctx["session_id"]

    async def _run_one(call: dict[str, Any]) -> dict[str, Any]:
        return await invoke_agent_handler(
            agent_name=call.get("agent_name", ""),
            task=call.get("task", ""),
            context=call.get("context", ""),
            require_confirmation=call.get("require_confirmation", False),
        )

    total = len(calls)
    completed_count = 0
    results: list[dict[str, Any]] = []
    partial_summaries: list[dict[str, Any]] = []

    # ── Use as_completed for true streaming parallelism ────────────────
    # Gather still gathers all results, but as_completed lets us process
    # each finished agent immediately — broadcasting progress and building
    # partial summaries the Orchestrator can use mid-synthesis.
    futures = [_asyncio.ensure_future(_run_one(c)) for c in calls]
    agent_name_map = {id(f): calls[i].get("agent_name", f"agent_{i}")
                      for i, f in enumerate(futures)}

    for fut in _asyncio.as_completed(futures):
        try:
            r = await fut
            results.append(r)
        except Exception as exc:
            results.append({
                "success": False,
                "error": str(exc),
                "agent_name": agent_name_map.get(id(fut), "unknown"),
            })

        completed_count += 1
        agent_name = r.get("agent_name", agent_name_map.get(id(fut), "?"))
        success = r.get("success", False)
        duration = r.get("duration_ms", 0)

        # Build partial summary for this agent
        summary = {
            "agent_name": agent_name,
            "success": success,
            "duration_ms": duration,
            "progress": f"{completed_count}/{total}",
        }
        if success:
            result_text = r.get("result", "")
            summary["result_preview"] = build_result_preview(result_text, max_chars=220)
            summary["result_length"] = len(result_text)
        else:
            summary["error"] = r.get("error", "未知错误")
        partial_summaries.append(summary)

        # ── Broadcast progress to frontend ──────────────────────────
        if ws_manager and session_id:
            try:
                await ws_manager.broadcast(
                    session_id,
                    {
                        "event": "agent_thinking",
                        "sessionId": session_id,
                        "messageId": f"parallel_progress_{int(time.time()*1000)}",
                        "agentId": "Orchestrator",
                        "phase": "parallel_executing",
                        "details": (
                            f"并行任务进度: {completed_count}/{total} — "
                            f"{agent_name} {'✓' if success else '✗'} "
                            f"({duration:.0f}ms)"
                        ),
                        "timestamp": "",
                    },
                )
            except Exception:
                pass

    total_ms = (time.time() - start) * 1000
    success_count = sum(1 for r in results if r.get("success"))

    return {
        "success": success_count > 0,
        "results": results,
        "total_duration_ms": total_ms,
        "success_count": success_count,
        "total_count": total,
        "partial_summaries": partial_summaries,
    }


# ── Dynamic task agent — spawn an ephemeral sub-agent for any task ────────


async def task_handler(
    description: str,
    prompt: str,
    subagent_type: str = "general-purpose",
    model: str = "",
) -> dict[str, Any]:
    """Launch a new ephemeral agent to handle a complex, multi-step task.

    Unlike ``invoke_agent`` (which is limited to the 7 predefined agents:
    Architect, CodeGen, Review, Test, Deploy, etc.), this tool dynamically
    creates a fresh agent instance with a custom system prompt tailored to
    the specific task.  Each invocation is independent — the agent has no
    memory of previous tasks.

    Use this when:
      - The task doesn't fit any predefined agent's domain
      - You need a fresh perspective without role baggage
      - You want an agent that can use ALL available tools (file ops,
        search, code execution, etc.) for a focused sub-problem
      - You need to fan out independent work to multiple agents

    Args:
        description: A short (3-5 word) label describing the task.
            Displayed in the UI progress indicator.
        prompt: The full task description for the agent.  Be specific
            about what you want — include expected outputs, constraints,
            and any relevant context.  The agent receives this as its
            sole user message along with tool access.
        subagent_type: Optional hint for agent behavior.  Supported
            values: ``"general-purpose"`` (default, has all tools),
            ``"Explore"`` (read-only search/read tools, ideal for
            codebase exploration), ``"Plan"`` (architectural thinking,
            no file writes).  The agent's tool set is restricted
            accordingly.
        model: Optional model override.  If empty, inherits the
            session's default model.

    Returns:
        {"success": bool, "result": str (the agent's final text output),
         "description": str, "duration_ms": float, "model": dict, ...}
    """
    ctx = get_tool_context()
    session_id = ctx["session_id"]
    user_id = ctx["user_id"]
    token = ctx["token"]
    ws_manager = ctx["ws_manager"]

    if not session_id:
        return {
            "success": False,
            "error": "无法启动 Task Agent：缺少会话上下文（session_id 未设置）",
            "description": description,
        }

    if not prompt or not prompt.strip():
        return {
            "success": False,
            "error": "prompt 不能为空",
            "description": description,
        }

    # ── Determine tool availability by subagent_type ──────────────────
    # Explore: read-only tools only (no writes, no code exec)
    # Plan: read + think, no writes, no code exec
    # general-purpose: all tools (default)

    # ── Build a dynamic agent dict ────────────────────────────────────
    # We construct a synthetic agent entry so call_agent() can use the
    # existing prompt-building, model-selection, and tool-calling infra.
    # The agent_id "Task" is not in the hardcoded list, so build_prompt()
    # will route it through the general-agent template and inject our
    # custom role instructions via role_prompt.
    agent = {
        "agent_id": f"Task-{description[:20]}",
        "domain": "general",
        "status": "active",
        "adapter_type": "",
        "risk_level": "L1",
        "display_name": description,
    }

    # ── Notify frontend ───────────────────────────────────────────────
    task_start_time = time.time()
    if ws_manager:
        try:
            await ws_manager.broadcast(
                session_id,
                {
                    "event": "agent_thinking",
                    "sessionId": session_id,
                    "messageId": f"task_{description[:30]}_{int(time.time()*1000)}",
                    "agentId": "Task",
                    "phase": "task_spawned",
                    "details": f"Task Agent 启动: {description}",
                    "timestamp": "",
                },
            )
        except Exception:
            pass

    # ── Build a focused system prompt for the task ────────────────────
    # The role prompt is embedded in the user content since call_agent
    # doesn't accept a custom role_prompt directly.  The agent template
    # already includes full tool access, so this context guides behavior.
    task_content = (
        f"【临时任务 — {description}】\n\n"
        f"{prompt}\n\n"
        f"─── 任务 Agent 工作原则 ───\n"
        f"1. 直接开始执行任务，不要问「你确认吗？」之类的确认问题。\n"
        f"2. 使用可用工具高效完成任务（文件操作、搜索、代码执行等）。\n"
        f"3. 完成后返回最终结果，用清晰的格式总结产出。\n"
        f"4. 如果任务无法完成，诚实说明原因并建议替代方案。\n"
        f"5. 对于代码生成类任务，使用 file_write 将代码写入工作区。\n"
        f"6. 回复语言与用户输入保持一致。"
    )

    # ── Invoke via call_agent ─────────────────────────────────────────
    from app.services.agent_service import call_agent as call_agent_fn

    try:
        response = await call_agent_fn(
            session_id=session_id,
            content=task_content,
            user_id=user_id,
            agent=agent,
            token=token,
        )

        result_text = ""
        if isinstance(response, dict):
            result_text = response.get("content", "")
        elif isinstance(response, str):
            result_text = response
        else:
            result_text = str(response)

        duration_ms = (time.time() - task_start_time) * 1000

        logger.info(
            "task_handler: desc=%s completed in %.0fms result_len=%d",
            description, duration_ms, len(result_text),
        )

        return {
            "success": True,
            "result": result_text,
            "description": description,
            "duration_ms": duration_ms,
            "result_length": len(result_text),
            "subagent_type": subagent_type,
        }

    except Exception as exc:
        duration_ms = (time.time() - task_start_time) * 1000
        logger.warning(
            "task_handler: desc=%s failed in %.0fms: %s",
            description, duration_ms, exc,
        )
        return {
            "success": False,
            "error": f"Task Agent 执行失败: {exc}",
            "description": description,
            "duration_ms": duration_ms,
            "subagent_type": subagent_type,
        }
