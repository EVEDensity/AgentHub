from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncGenerator
from typing import Any

from app.db.init_db import now
from app.services.adapter_manager import adapter_manager
from app.services.auth.service import AuthService
from app.services.codegen_service import write_generated_files
from app.services.response_quality import estimate_response_quality
from app.services.token_budget import (
    TokenBudget,
    cognitive_memory_budgets,
    count_tokens,
    truncate_to_tokens,
)
from app.services.text_processing import (
    filter_streaming_chunk,
    is_code_request,
    is_codegen_json_response,
    latex_to_unicode,
    normalize_agent_output,
    remove_repeated_text,
    reset_stream_filter,
    strip_codegen_prefix,
    strip_kimi_thinking,
    strip_think_tags,
)
from app.utils.async_file import aexists, aisdir, aread_text, aiterdir

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

logger = logging.getLogger("agenthub.agent.orchestrator")

# ── Collaboration role labels (shared with multi-agent context) ─────
_ROLE_LABELS: dict[str, str] = {
    "orchestrator": "协调调度",
    "architect": "架构设计",
    "codegen": "代码生成",
    "review": "代码审查",
    "test": "测试验证",
    "deploy": "部署发布",
}

# Cross-module refs kept local to this package to avoid import cycles.
# All helpers live in the sibling agent modules; the facade re-exports them.
from app.services.agent.context import (  # noqa: E402
    _build_attachment_context,
    _build_conversation_history,
    _build_memory_context,
    _build_quote_context,
    _estimate_token_usage,
)
from app.services.agent.persistence import save_message  # noqa: E402
from app.services.agent.routing import (  # noqa: E402
    _get_streaming_executor,
    candidate_models_for_role,
    choose_models,
    resolve_agent,
)
from app.services.agent.tooling import (  # noqa: E402
    _run_tool_call_loop,
    _stream_cloudcode_response,
)

from app.services.agent.context import _intent_from_domain  # noqa: E402
from app.services.agent_prompt_context import build_image_parts  # noqa: E402

class CollaborationContext:
    """Shared memory for one multi-agent collaboration turn.

    Extracts structured summaries and key points from each agent's
    contribution so downstream agents can build on peer output without
    re-reading full responses.
    """

    def __init__(self, user_content: str):
        self.user_content = user_content
        self.participants: list[dict] = []
        self.contributions: list[dict] = []

    def register(self, agent: dict) -> None:
        self.participants.append(agent)

    def record(self, agent_id: str, domain: str, content: str) -> None:
        """Record a contribution: extract summary + key points for peer context."""
        clean = _strip_think_tags(_strip_kimi_thinking(content))
        sentences = re.split(r"[。！？\n]", clean)
        summary_parts = [s.strip() for s in sentences[:3] if len(s.strip()) > 10]
        summary = "。".join(summary_parts) if summary_parts else clean[:200]

        key_points: list[str] = []
        for line in clean.split("\n"):
            line = line.strip()
            if 20 < len(line) < 200 and (
                re.match(r"^[-•*\d]+[.)]", line)
                or any(kw in line for kw in ["建议", "方案", "采用", "需要", "注意", "关键", "核心", "必须", "推荐", "优先"])
            ):
                key_points.append(line[:150])
        if not key_points:
            key_points.append(summary[:150])

        self.contributions.append({
            "agent_id": agent_id,
            "domain": domain,
            "summary": summary[:300],
            "key_points": key_points[:3],
        })

    def context_for(self, agent_id: str) -> str:
        if not self.contributions:
            return ""

        my = next((p for p in self.participants if p["agent_id"] == agent_id), None)
        my_domain = (my or {}).get("domain", "")

        roster = "\n".join(
            f"- {p['agent_id']}（{_ROLE_LABELS.get(p.get('domain',''), 'general')}）"
            for p in self.participants
        )

        peer_blocks: list[str] = []
        for c in self.contributions:
            if c["agent_id"] == agent_id:
                continue
            pts = "\n".join(f"  · {pt}" for pt in c["key_points"][:3])
            peer_blocks.append(
                f"### {c['agent_id']}（{_ROLE_LABELS.get(c['domain'], 'general')}）\n"
                f"摘要：{c['summary']}\n"
                f"关键要点：\n{pts}"
            )

        expectations = _collab_expectations(my_domain, agent_id)

        return (
            "【多智能体协作上下文 — 你正在与其他Agent协同完成用户任务】\n\n"
            f"## 协作团队\n{roster}\n\n"
            + (f"## 同伴已完成的工作\n" + "\n\n".join(peer_blocks) + "\n\n" if peer_blocks else "")
            + f"## 对你（{agent_id}）的角色期望\n{expectations}\n\n"
            "## 协作铁律\n"
            "1. 基于同伴输出进行补充和深化，严禁重复已有内容\n"
            "2. 你的回复将与其他Agent的回复一起展示给用户\n"
            "3. 如有不同意见请标注「补充意见」后继续，不要陷入辩论\n"
            "4. 保持专业、具体、可执行，给出下一步行动建议"
        )

    @property
    def summary(self) -> str:
        if not self.contributions:
            return ""
        lines = ["【本轮协作摘要】"]
        for i, c in enumerate(self.contributions, 1):
            lines.append(f"{i}. {c['agent_id']}（{_ROLE_LABELS.get(c['domain'], 'general')}）：{c['summary'][:120]}")
        return "\n".join(lines)

def _collab_expectations(domain: str, agent_id: str) -> str:
    return {
        "architect": "基于用户需求进行技术方案设计。如果前面已有Agent给出分析，请在其基础上补充架构层面（技术选型、模块划分、数据流、部署拓扑）的建议，不要重复具体实现细节。",
        "codegen": "基于已有的技术方案生成具体可运行代码。如果Architect已给出架构方案，严格遵循其设计；如果Review已提出修改意见，先修正再输出最终代码。",
        "review": "审查已有方案和代码。逐项检查同伴输出是否存在逻辑漏洞、安全隐患、性能瓶颈或偏离需求之处，给出条目化的修改建议和风险等级。",
        "test": "基于已有代码和方案设计测试策略。列出关键测试路径、边界条件和推荐的测试框架，指出同伴代码中最可能出错的模块。",
        "deploy": "基于已完成的工作检查部署前置条件：代码是否已审查通过、测试是否通过、环境配置是否完整。给出分步部署方案。",
        "orchestrator": "你是多Agent协作的协调者。综合所有Agent的输出，识别冲突和缺口，规划下一步执行顺序，确保整体任务高质量完成。",
    }.get(domain, f"基于你的专业领域（{domain}），在同伴已有工作的基础上给出独立且互补的分析和建议。不要重复已有内容，给出增量价值。")

async def load_skill_prompt(skill_name: str, max_chars: int = 30_000) -> str | None:
    """Load a skill's SKILL.md body for prompt injection.

    The body is truncated to ``max_chars`` to prevent a single enormous
    skill from overflowing the model's context window.  If truncation
    happens a note is appended so the model knows the instructions were
    cut short.
    """
    from pathlib import Path

    async def _find_skill_dir(base: Path) -> Path | None:
        if not await aisdir(base):
            return None
        candidate = base / skill_name
        if await aisdir(candidate):
            return candidate
        try:
            for d in await aiterdir(base):
                if await aisdir(d) and d.name.lower() == skill_name.lower():
                    return d
        except OSError:
            pass
        return None

    # User skills
    skill_dir = await _find_skill_dir(Path.home() / ".claude" / "skills")
    # Project skills
    if not skill_dir:
        cwd = Path.cwd()
        for parent in [cwd, *cwd.parents]:
            skill_dir = await _find_skill_dir(parent / ".claude" / "skills")
            if skill_dir:
                break
        if not skill_dir:
            import os
            proj_env = os.environ.get("AGENTHUB_PROJECT_DIR", "")
            if proj_env:
                skill_dir = await _find_skill_dir(Path(proj_env) / ".claude" / "skills")
    if not skill_dir:
        return None
    try:
        skill_dir = skill_dir.resolve() if skill_dir.is_symlink() else skill_dir
    except OSError:
        pass
    for filename in ("SKILL.md", "skill.md"):
        skill_file = skill_dir / filename
        if await aexists(skill_file):
            try:
                raw = await aread_text(skill_file)
                fm_match = re.match(r"^---\s*\n.*?\n---\s*\n", raw, re.DOTALL)
                if fm_match:
                    body = raw[fm_match.end():].strip()
                else:
                    body = raw.strip()
                if len(body) > max_chars:
                    body = body[:max_chars] + (
                        f"\n\n... [技能 {skill_name} 的正文已截断，"
                        f"原始长度 {len(body)} 字符，当前显示前 {max_chars} 字符]"
                    )
                    logger.warning(
                        "skill body truncated skill=%s orig=%d max=%d",
                        skill_name, len(body), max_chars,
                    )
                return body
            except (OSError, UnicodeDecodeError):
                return None
    return None

async def call_agent(session_id: str, content: str, user_id: str, attachments: list[dict[str, Any]] | None = None, agent: dict | None = None, collab_ctx: str = "", token: Any = None, on_tool_event: Any = None, quote_references: list[dict[str, Any]] | None = None, simple_mode: bool = False) -> dict:
    if agent is None:
        agent = await resolve_agent(content)
    domain = agent["domain"]
    msg_type = "code" if domain == "codegen" or any(word in content.lower() for word in ["code", "fastapi", "react", "代码", "实现"]) else "text"

    attachment_context, attachment_meta = _build_attachment_context(attachments)
    quote_context = _build_quote_context(quote_references)
    # MM-2/ADR-0105: inline images travel as structured parts next to the
    # text prompt — never re-embedded into the string body.
    image_parts = build_image_parts(attachments)
    llm_input = content
    if quote_context:
        llm_input = f"{quote_context}\n\n[用户当前问题]\n{content}"
    if attachment_context:
        llm_input = f"{llm_input}\n\n[用户上传附件上下文]\n{attachment_context}"

    # ── Auto-decomposition for compound tasks ──────────────────────────
    # When the user's request is large and compound (e.g. "write the entire
    # user management system front+backend"), inject a decomposition prefix
    # so the agent breaks the work into smaller sub-steps within its tool-
    # call loop.  Each sub-step gets its own LLM call → no single call
    # exceeds the per-request timeout.
    decomposition_prefix = ""
    if agent["agent_id"] != "Orchestrator":
        from app.services.orchestrator_preprocessor import should_decompose
        if should_decompose(content):
            decomposition_prefix = (
                "[系统指令 — 任务自动分解]\n"
                "用户请求内容较多且涉及多个文件/模块，请按以下策略分步执行：\n"
                "1. 首先用一句话总结你对任务的理解\n"
                "2. 将任务拆分为 3-5 个独立的子步骤（每个子步骤聚焦单个文件或模块）\n"
                "3. 按顺序执行每个子步骤：先完成 → 再下一个（避免一次生成过多代码导致超时）\n"
                "4. 每个子步骤完成后简要告知用户进度\n"
                "5. 如果某个子步骤遇到错误，尝试修复后再继续下一个\n\n"
                "⚠️ 重要：请不要尝试在一次回复中完成所有工作。分步执行，每步只写一个文件。\n\n"
            )
            llm_input = decomposition_prefix + llm_input
            logger.info(
                "call_agent: auto-decompose injected for agent=%s content_len=%d",
                agent["agent_id"], len(content),
            )

    symbolic = generate_symbolic_message(
        llm_input, msg_type, session_id,
        sender_role=agent["agent_id"],
        intent_type=_intent_from_domain(domain, content),
        risk_level=agent.get("risk_level", "L1"),
    )
    models = choose_models(await candidate_models_for_role(agent["agent_id"], user_id))
    history = await _build_conversation_history(session_id)
    primary_model = models[0] if models else {}
    provider = primary_model.get("provider", "")
    model_name = primary_model.get("model_name", "")
    budget = TokenBudget.for_model(provider, model_name)
    memory_budgets = cognitive_memory_budgets(
        budget.section_limit("history") + budget.section_limit("memory"), content, domain,
    )
    history_tokens_before = count_tokens(history, provider, model_name)
    history, history_truncated = truncate_to_tokens(
        history, memory_budgets["working"], provider, model_name,
    )
    from app.services.performance_monitor import monitor
    monitor.record_token_compaction(
        "history",
        history_tokens_before,
        count_tokens(history, provider, model_name),
        history_truncated,
    )
    memory_ctx = await _build_memory_context(
        user_id=user_id,
        session_id=session_id,
        history=history,
        provider=provider,
        model=model_name,
        max_tokens=sum(value for key, value in memory_budgets.items() if key != "working"),
        section_budgets=memory_budgets,
        query=content,
    )

    # Use tool-enabled call loop (handles tool detection, execution, synthesis)
    result, usage, selected = await _run_tool_call_loop(
        session_id, content, user_id, agent, domain, llm_input,
        models, history, memory_ctx, collab_ctx, token=token,
        streaming_executor=_get_streaming_executor(),
        on_tool_event=on_tool_event,
        simple_mode=simple_mode,
        image_parts=image_parts,
    )

    content_out = normalize_agent_output(agent["agent_id"], result, content)
    from app.services.performance_monitor import monitor
    monitor.record_answer_quality(estimate_response_quality(content, content_out))
    adapter = adapter_manager.get_adapter(selected.get("provider", "mock"))
    if usage and usage.get("total_tokens", 0) > 0:
        prompt_tokens, completion_tokens, total_tokens = usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"]
    else:
        prompt_tokens, completion_tokens, total_tokens = _estimate_token_usage(llm_input, content_out)
    generated = await write_generated_files(content_out, content) if agent["agent_id"] == "CodeGen" else None
    public = {
        **public_symbolic(symbolic),
        "generated": generated,
        "model": {"provider": selected.get("provider"), "modelName": selected.get("model_name")},
        "attachments": attachment_meta,
    }
    AuthService.write_audit(user_id, agent["agent_id"], "agent_execute", agent.get("risk_level", "L1"), "auto", {"sessionId": session_id, "domain": domain, "generated": generated, "model": public["model"]})
    display_content = "CodeGen 已生成结构化文件，请在下方生成文件面板中检查内容、查看 Diff，并确认提交。" if (generated and generated.get("files")) else content_out
    message = {
        "event": "message",
        "sessionId": session_id,
        "content": display_content,
        "sender": agent["agent_id"],
        "timestamp": now(),
        "type": "code" if agent["agent_id"] == "CodeGen" else "text",
        "symbolic": public,
    }
    await save_message(
        session_id,
        message["sender"],
        message["content"],
        message["type"],
        0.0,
        message["symbolic"],
        prompt_tokens,
        completion_tokens,
        total_tokens,
    )
    return message

async def stream_agent_response(
    session_id: str,
    content: str,
    user_id: str,
    token=None,
    attachments: list[dict[str, Any]] | None = None,
    agent: dict | None = None,
    collab_ctx: str = "",
    on_tool_event: Any = None,
    quote_references: list[dict[str, Any]] | None = None,
    preprocess_context: str = "",
    simple_mode: bool = False,
) -> AsyncGenerator[str, None] | None:
    """Stream an agent response with full tool-calling support.

    Uses the same proven ``_run_tool_call_loop`` as ``call_agent()``,
    then streams the final synthesized result to the frontend in chunks.
    """
    if agent is None:
        agent = await resolve_agent(content)

    # ── CloudCode / Local subprocess adapters ──
    _SUB_PROCESS_ADAPTERS = frozenset({
        "cloud_code", "local_claude", "local_codex", "local_openclaw",
    })
    if agent.get("adapter_type") in _SUB_PROCESS_ADAPTERS:
        return await _stream_cloudcode_response(
            session_id, content, user_id, agent, token=token,
            attachments=attachments, quote_references=quote_references,
        )

    models = choose_models(await candidate_models_for_role(agent["agent_id"], user_id))
    if not models:
        return None

    attachment_context, _ = _build_attachment_context(attachments)
    quote_context = _build_quote_context(quote_references)
    llm_input = content
    if quote_context:
        llm_input = f"{quote_context}\n\n[用户当前问题]\n{content}"
    if attachment_context:
        llm_input = f"{llm_input}\n\n[用户上传附件上下文]\n{attachment_context}"

    async def stream():
        # ── Real SSE streaming via asyncio.Queue bridge ────────────
        # The tool-call loop runs in a background task and pushes
        # chunks through a queue as they arrive from the LLM.
        # This generator drains the queue and yields chunks to the
        # WebSocket layer immediately — true token-by-token streaming.
        import asyncio as _asyncio

        t_start = time.perf_counter()
        first_chunk_sent = False
        ttfc_ms = 0.0
        chunk_count = 0

        chunk_queue: _asyncio.Queue = _asyncio.Queue()
        _SENTINEL = object()

        async def on_chunk(chunk: str) -> None:
            # Apply streaming-safe filters BEFORE pushing to the queue so
            # the WebSocket layer never has to clean up leaked markers
            # mid-flight.  The filter is idempotent and preserves
            # <think>...</think> blocks for the ThinkingPanel.
            filtered = _filter_streaming_chunk(session_id, chunk)
            if filtered:
                await chunk_queue.put(filtered)

        async def _run_loop():
            try:
                history = await _build_conversation_history(session_id)
                primary_model = models[0] if models else {}
                provider = primary_model.get("provider", "")
                model_name = primary_model.get("model_name", "")
                budget = TokenBudget.for_model(provider, model_name)
                memory_budgets = cognitive_memory_budgets(
                    budget.section_limit("history") + budget.section_limit("memory"),
                    content,
                    agent["domain"],
                )
                history_tokens_before = count_tokens(history, provider, model_name)
                history, history_truncated = truncate_to_tokens(
                    history, memory_budgets["working"], provider, model_name,
                )
                from app.services.performance_monitor import monitor
                monitor.record_token_compaction(
                    "history",
                    history_tokens_before,
                    count_tokens(history, provider, model_name),
                    history_truncated,
                )
                memory_context = await _build_memory_context(
                    user_id=user_id,
                    session_id=session_id,
                    history=history,
                    provider=provider,
                    model=model_name,
                    max_tokens=sum(value for key, value in memory_budgets.items() if key != "working"),
                    section_budgets=memory_budgets,
                    query=content,
                )
                r, u, s = await _run_tool_call_loop(
                    session_id, content, user_id, agent, agent["domain"], llm_input,
                    models, history, memory_context,
                    collab_ctx, token=token, on_tool_event=on_tool_event,
                    streaming_executor=_get_streaming_executor(),
                    stream_callback=on_chunk,
                    preprocess_context=preprocess_context,
                    simple_mode=simple_mode,
                    image_parts=build_image_parts(attachments),
                )
                return ("ok", r, u, s)
            except Exception as _loop_exc:
                logger.exception(
                    "stream_agent_response: _run_tool_call_loop crashed session=%s agent=%s",
                    session_id, agent["agent_id"],
                )
                return ("error", _loop_exc)
            finally:
                await chunk_queue.put(_SENTINEL)

        loop_task = _asyncio.create_task(_run_loop())

        # Phase 1: Yield chunks as they arrive from the adapter
        # (token-by-token from the real SSE stream)
        full_text: list[str] = []
        try:
            while True:
                chunk = await chunk_queue.get()
                if chunk is _SENTINEL:
                    break
                if token and token.cancelled:
                    loop_task.cancel()
                    return
                if chunk:
                    if not first_chunk_sent:
                        ttfc_ms = (time.perf_counter() - t_start) * 1000
                        first_chunk_sent = True
                    full_text.append(chunk)
                    chunk_count += 1
                    yield chunk
        finally:
            # Always release per-session filter state so a later
            # interrupted-and-restarted stream starts with a clean slate.
            _reset_stream_filter(session_id)

        # Phase 2: Wait for the tool loop to finish
        loop_result = await loop_task
        status = loop_result[0]

        if status == "error":
            _loop_exc = loop_result[1]
            result = (
                f"模型调用异常：{_loop_exc}\n\n"
                "请检查：\n"
                "1. 模型 API Key 是否正确配置\n"
                "2.  API 端点是否可达（网络/GFW）\n"
                "3. 模型适配器是否正常加载"
            )
            usage = {}
            selected = models[0] if models else {"provider": "unknown", "model_name": "unknown"}
            content_out = result
        else:
            _, result, usage, selected = loop_result
            content_out = normalize_agent_output(agent["agent_id"], result, content)

        from app.services.performance_monitor import monitor
        monitor.record_answer_quality(estimate_response_quality(content, content_out))

        # ── Stream performance metrics ────────────────────────────
        t_end = time.perf_counter()
        total_ms = (t_end - t_start) * 1000
        logger.info(
            "stream_metrics session=%s agent=%s provider=%s model=%s "
            "ttfc=%.0fms total=%.0fms chunks=%d len=%d",
            session_id, agent["agent_id"],
            selected.get("provider", "?"), selected.get("model_name", "?"),
            ttfc_ms, total_ms, chunk_count, len(content_out),
        )
        try:
            from app.services.performance_monitor import monitor
            monitor.record_stream_start()
            if ttfc_ms > 0:
                monitor.record_ttft(ttfc_ms)
            # Record chunk throughput
            if chunk_count > 0 and total_ms > 0:
                monitor.record_chunk(len(content_out), total_ms / max(1, chunk_count))
        except Exception:
            pass

        # ── Persist message & audit (best-effort, non-fatal) ────────
        try:
            usage_dict = usage or {}
            pt = usage_dict.get("prompt_tokens", max(1, len(llm_input) // 4))
            ct = usage_dict.get("completion_tokens", max(1, len(content_out) // 4))
            tt = usage_dict.get("total_tokens", pt + ct)
            symbolic_out = generate_symbolic_message(
                llm_input, "text", session_id,
                sender_role=agent["agent_id"],
                intent_type=_intent_from_domain(agent["domain"], content),
                risk_level=agent.get("risk_level", "L1"),
            )
            await save_message(session_id, agent["agent_id"], content_out, "text", 0.0, public_symbolic(symbolic_out), pt, ct, tt, user_id=user_id)
            AuthService.write_audit(
                user_id,
                agent["agent_id"],
                "agent_execute",
                agent.get("risk_level", "L1"),
                "auto",
                {
                    "sessionId": session_id,
                    "domain": agent["domain"],
                    "model": {"provider": selected.get("provider"), "modelName": selected.get("model_name")},
                },
            )
        except Exception:
            logger.warning("stream_agent_response: save_message/audit failed (non-fatal)", exc_info=True)

    return stream()
