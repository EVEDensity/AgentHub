from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.db.session import afetch_all
from app.services.conversation_history import build_conversation_history_transcript
from app.services import agent_prompt_context as prompt_context
from app.services.agent_prompt_templates import (
    build_architect_prompt,
    build_codegen_prompt,
    build_deploy_prompt,
    build_general_prompt,
    build_orchestrator_prompt,
)
from app.services import prompt_sections
from app.services.prompt_cache import prompt_cache
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
from app.utils.async_file import aexists, aread_json

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
    public_symbolic,
)

logger = logging.getLogger("agenthub.agent.context")

# ── Memory context cache (avoid scanning 200+ files on every message) ──
# TTL 300 s (5 min) — keeps the context cache warm within the prompt-cache
# window, reducing disk I/O while staying fresh enough for cross-session
# memory.  Invalidation is explicit via _invalidate_memory_cache() when
# new memories are written (extraction, /memory commands).
_MEMORY_CONTEXT_CACHE: dict[str, tuple[float, str]] = {}

def _build_attachment_context(attachments: list[dict[str, Any]] | None) -> tuple[str, list[dict[str, Any]]]:
    return prompt_context.build_attachment_context(attachments)

def _build_quote_context(quote_references: list[dict[str, Any]] | None) -> str:
    return prompt_context.build_quote_context(quote_references)


def _intent_from_domain(domain: str, _content: str = "") -> str:
    """Map agent domain to a symbolic-message intent_type (§3.2)."""
    mapping = {
        "architect": "architecture",
        "codegen": "code_generation",
        "review": "code_review",
        "test": "testing",
        "deploy": "deployment",
        "orchestrator": "orchestration",
    }
    return mapping.get(domain, "general")

async def _build_conversation_history(session_id: str, max_chars: int = 3600) -> str:
    """Fetch recent messages from this session and format as a transcript.

    Gives every agent called in the session full awareness of what was
    discussed before — the foundation of long-term conversational memory.

    Messages are processed newest-first so that the most recent (and most
    relevant) context is always included when the char limit is hit.
    LIMIT 25 balances context depth with prompt size.

    Results are cached for 5s per session to avoid redundant DB queries
    during rapid-fire messages or tool-call loop iterations.
    """
    cached = prompt_cache.get_history(session_id)
    if cached is not None:
        return cached

    try:
        rows = await afetch_all(
            "SELECT sender,content FROM messages WHERE session_id=$1 AND type!='system' ORDER BY created_at DESC LIMIT 18",
            session_id,
        )
    except Exception:
        return ""

    if not rows:
        prompt_cache.set_history(session_id, "")
        return ""

    result = build_conversation_history_transcript(rows, max_chars=max_chars)
    prompt_cache.set_history(session_id, result)
    return result

async def _build_memory_context(
    user_id: str = "",
    session_id: str = "",
    *,
    history: str = "",
    provider: str = "",
    model: str = "",
    max_tokens: int = 3000,
    query: str = "",
    section_budgets: dict[str, int] | None = None,
    force: bool = False,
) -> str:
    """Build a lightweight L0/L1 memory projection (ADR-0107).

    DB history is the working transcript (L0) and is excluded here. L1 =
    session summary (higher priority) plus recent durable conversation
    turns. The heavy L2/semantic/L3-global/procedural layers and the
    budgeted multi-class projection (memory_context) were removed with the
    web-chat memory decommission (ADR-0107); the CLI/Mission path already
    builds its own compact context (build_compact_context).
    """
    import hashlib

    from app.config import MEMORY_DIR
    from app.services.memory.storage import MemoryStorage
    from app.services.memory.session_memory import SessionMemoryManager
    from app.services.memory.session_store import SessionMemoryStore

    uid = user_id or "local-admin"
    history_fp = hashlib.sha256(history.encode("utf-8")).hexdigest()[:12]
    cache_key = f"l1:{uid}:{session_id}:{provider}:{model}:{max_tokens}:{history_fp}"
    now_ts = time.monotonic()
    cached = _MEMORY_CONTEXT_CACHE.get(cache_key)
    if not force and cached and now_ts - cached[0] < 60.0:
        return cached[1]

    user_memory_dir = MEMORY_DIR / "users" / uid
    session_mgr = SessionMemoryManager(MemoryStorage(user_memory_dir))
    session_store = SessionMemoryStore(user_memory_dir)
    parts: list[str] = []

    if session_id:
        try:
            session_summary = await session_mgr.get_session_summary(session_id)
            if session_summary:
                parts.append(f"【会话摘要】\n{session_summary}")
        except Exception:
            logger.debug("memory context: session summary unavailable", exc_info=True)

        try:
            conversation = await session_store.get_conversation(
                session_id, max_chars=max(2200, max_tokens * 4), recent_turns=6,
            )
            if conversation:
                parts.append(f"【近期持久对话】\n{conversation}")
        except Exception:
            logger.debug("memory context: durable conversation unavailable", exc_info=True)

    result = "\n\n".join(parts)
    _MEMORY_CONTEXT_CACHE[cache_key] = (now_ts, result)
    return result

def _invalidate_memory_cache() -> None:
    """Clear the memory context cache so next call rebuilds it."""
    _MEMORY_CONTEXT_CACHE.clear()

async def _load_settings() -> dict[str, Any]:
    """Load general settings from the shared settings.json file.

    Returns a dict with defaults for all known keys.  Results are cached
    for 30s to avoid reading the file from disk on every prompt build.
    """
    cached = prompt_cache.get_settings()
    if cached is not None:
        return cached

    defaults: dict[str, Any] = {
        "theme": "warm",
        "lang": "zh",
        "reply_lang": "default",
        "reasoning": 2,
        "thinking": True,
        "notify": True,
        "zoom": 100,
    }
    try:
        from app.config import DATA_DIR
        path = DATA_DIR / "settings.json"
        if await aexists(path):
            data = await aread_json(path)
            if isinstance(data, dict):
                # Only accept known keys with correct types
                for k, v in defaults.items():
                    val = data.get(k)
                    if val is not None and isinstance(val, type(v)):
                        defaults[k] = val
    except Exception:
        pass

    prompt_cache.set_settings(defaults)
    return defaults

def _build_reply_lang_instruction(settings: dict[str, Any]) -> str:
    """Return a prompt instruction for the configured reply language."""
    lang = settings.get("reply_lang", "default")
    if lang == "english":
        return "\n【回复语言】请始终使用 English 回复用户的所有消息，包括代码注释和文档。\n"
    elif lang == "chinese":
        return "\n【回复语言】请始终使用中文回复用户的所有消息，包括代码注释和文档。\n"
    elif lang == "japanese":
        return "\n【回复语言】请常に日本語で返信してください。コードのコメントやドキュメントも日本語で記述してください。\n"
    return ""

def _build_reasoning_instruction(settings: dict[str, Any]) -> str:
    """Return a prompt instruction for the configured reasoning intensity."""
    level = settings.get("reasoning", 2)
    if level >= 4:
        return (
            "\n【推理强度：最大】请对问题进行最深入、最全面的分析：\n"
            "1. 从多个角度和维度考虑问题\n"
            "2. 探索多种解决方案并比较优劣\n"
            "3. 提供详细的论证过程和决策依据\n"
            "4. 考虑边界情况和潜在风险\n"
        )
    elif level >= 3:
        return (
            "\n【推理强度：高】请进行较为深入的分析：\n"
            "1. 从多个角度考虑问题\n"
            "2. 比较至少两种解决方案\n"
            "3. 提供论证过程和决策依据\n"
        )
    elif level >= 2:
        return ""
    else:
        return "\n【推理强度：低】请直接给出简洁的结论和方案，减少分析过程。\n"

async def _get_agent_tools(agent_id: str) -> list[str] | None:
    """Get the list of tool names available to an agent.

    Queries agent_tool_bindings. If no bindings exist, returns None
    (meaning all tools are available by default).
    """
    try:
        rows = await afetch_all(
            "SELECT td.name FROM tool_definitions td "
            "JOIN agent_tool_bindings atb ON td.id = atb.tool_id "
            "WHERE atb.agent_id=$1 AND atb.enabled=1 AND td.enabled=1",
            agent_id,
        )
        if rows:
            return [r["name"] for r in rows]
    except Exception:
        pass  # table may not exist yet — fall through to default
    return None  # None = all tools available

def _build_tool_section(
    agent_id: str = "",
    available_tools: list[str] | None = None,
    permission_mode: str | None = None,
) -> str:
    """Build the tool-calling prompt section for injection into the agent prompt.

    Returns empty string if no tools are registered or tools are disabled.

    Results are cached for the process lifetime — tool definitions only
    change when the server restarts (which clears the in-process cache).
    """
    # 模式感知的独立缓存：避免 (a) 命中无 mode 提示的陈旧缓存
    #                       (b) 多次调用重复拼装 notice
    base_tools_key = tuple(sorted(available_tools)) if available_tools else None
    _mode_key = (agent_id, base_tools_key, permission_mode or "")
    _mode_cache: dict = getattr(_build_tool_section, "_cache", {})
    if _mode_key in _mode_cache:
        return _mode_cache[_mode_key]

    from app.services.tool_registry import tool_registry

    tool_defs = tool_registry.build_prompt_section(available_tools)
    if not tool_defs:
        _mode_cache[_mode_key] = ""
        _build_tool_section._cache = _mode_cache
        return ""

    instructions = tool_registry.build_calling_instructions()

    # 模式感知：在 tool 段开头注入当前会话的权限模式，避免 LLM 在 PLAN 模式下
    # 盲目尝试调用 file_write/code_execute 等高危工具。
    mode_notice = ""
    if permission_mode == "plan":
        mode_notice = (
            "\n\n【当前权限模式：计划模式（PLAN / read-only）】\n"
            "用户已开启计划模式：以下写/执行类工具调用将被系统直接拒绝（Deny），"
            "且不会弹窗确认。请不要调用：file_write / file_write_batch / file_edit / "
            "file_patch / code_execute / command_execute（写操作或 shell）。"
            "你可以使用 file_read / file_search / file_glob / web_search / memory_search 等只读工具来调研，"
            "并向用户输出一个「实施计划」——用文字描述需要改哪些文件、改成什么样，"
            "等用户切回默认或 Bypass 模式后再实际落盘。\n"
        )
    elif permission_mode == "bypass":
        mode_notice = (
            "\n\n【当前权限模式：跳过权限（BYPASS / auto-allow）】\n"
            "用户已开启跳过权限：所有工具调用会直接放行，不会弹窗确认。"
            "请直接动手执行用户的请求，不需要中途打断。\n"
        )
    elif permission_mode == "default":
        mode_notice = (
            "\n\n【当前权限模式：询问权限（DEFAULT / ask-on-risky）】\n"
            "高风险工具（写文件、执行代码、运行 Shell 等）在调用时可能会触发用户确认弹窗。"
            "请正常调用工具，但准备好在用户拒绝时调整方案。\n"
        )

    result = "\n\n" + mode_notice + tool_defs + "\n\n" + instructions
    _mode_cache[_mode_key] = result
    _build_tool_section._cache = _mode_cache
    return result

async def build_prompt(agent_id: str, domain: str, content: str, symbolic: dict, role_prompt: str, collab_ctx: str = "", history: str = "", memory_context: str = "", tools_enabled: bool = True, available_tools: list[str] | None = None, model_provider: str = "", model_name: str = "", preprocess_context: str = "", permission_mode: str | None = None) -> str:
    # ── Shared session context (ALL agents see this FIRST) ──────────
    # This is the "main context window" — every agent reads it before
    # its role-specific instructions, ensuring a unified understanding
    # of what the conversation is about regardless of domain.
    shared_context = prompt_sections.build_shared_context(history)
    collab_section = prompt_sections.build_collab_section(collab_ctx)

    # ── Current date (so the model knows what "today" is) ────────────
    # The model's training cutoff may be months ago.  Without this, the
    # model hallucinates dates or uses stale ones in search queries.
    date_context = prompt_sections.build_date_context()

    # Workspace filesystem context is intentionally compact. Tool details live in
    # prompt_sections so build_prompt stays focused on orchestration.
    workspace_context_block = prompt_sections.build_workspace_context()

    # ── Load settings for reply language, reasoning, thinking ───────
    settings = await _load_settings()
    reply_lang_instr = _build_reply_lang_instruction(settings)
    reasoning_instr = _build_reasoning_instruction(settings)

    # 真实运行模型身份（防止 agent 瞎编"我是什么大模型"）。
    # 这是硬性身份：被问到时必须如实回答，不准编造。
    actual_model_line = (
        f"【当前运行模型】你实际由 {model_provider or '未知 provider'} "
        f"提供的 {model_name or '未知模型'} 驱动。\n"
        f'当被问及“你是什么大模型/底层模型/谁训练了你”时，请如实回答，'
        f"不要编造其他模型名称。\n"
    )

    # ── Role identity (lightweight, below shared context) ──────────
    role_labels = {
        "orchestrator": "协调调度专家",
        "architect": "架构设计专家",
        "codegen": "代码生成专家",
        "review": "代码审查专家",
        "test": "测试验证专家",
        "deploy": "部署发布专家",
    }
    role_desc = role_labels.get(domain, f"{domain}领域专家")

    code_format_rules = (
        "【代码输出格式规范】当回复中包含代码、终端命令、脚本、SQL 或配置文件时：\n"
        "1. 统一使用 ```[语言] 代码块格式（python/javascript/typescript/bash/sql/json/yaml/toml）\n"
        "2. 代码内容完整、语法无误，复制后可直接执行\n"
        "3. 代码块上方标注用途，多个代码片段依次编号\n"
        "4. 仅使用原生 Markdown，不插入 HTML、自定义标签\n"
    ) if not role_prompt else ""

    # Mermaid diagram syntax rules — LLMs frequently generate subtly broken
    # Mermaid that causes "Syntax error in text" at render time.  These rules
    # encode the most common failure patterns observed in production.
    mermaid_rules = (
        "【Mermaid 图表语法规范 — 必须严格遵守】当输出 flowchart/sequenceDiagram/classDiagram 等 Mermaid 图表时：\n"
        "1. 图表类型：第一行必须为 flowchart TD/LR（不用已弃用的 graph）\n"
        "2. 节点ID（关键！）：只能使用英文字母+数字+下划线，绝对禁止中文、空格、连字符、点号\n"
        "   正确: A[用户登录页面] 或 start[开始]\n"
        "   错误: 用户登录[用户登录] / user-login[登录] / 1.start[开始]\n"
        "3. 标签引号：含中文、空格、标点的标签必须用双引号包裹\n"
        "   正确: A[\"用户登录 — 步骤1\"]\n"
        "   错误: A[用户登录 — 步骤1]\n"
        "4. 箭头：每条连接单独一行，箭头两侧各留一个空格\n"
        "   正确: A --> B / C -->|\"标签\"| D\n"
        "   错误: A-->B-->C（链式）\n"
        "5. subgraph 名称含中文或空格须加引号：subgraph \"用户模块\" ... end\n"
        "6. 禁止在标签中使用裸 HTML 标签、裸 & 符号（用 &amp; 替代）\n"
        "7. 代码块内不要出现 ``` 标记（会破坏 Markdown 解析）\n"
        "8. 节点标签内如有双引号，转义为 &quot;\n"
        "9. 完整示例（复制此模板修改）：\n"
        "```mermaid\n"
        "flowchart TD\n"
        "    A[\"开始\"] --> B[\"处理数据\"]\n"
        "    B --> C{\"条件成立？\"}\n"
        "    C -->|\"是\"| D[\"执行操作A\"]\n"
        "    C -->|\"否\"| E[\"执行操作B\"]\n"
        "    D --> F[\"结束\"]\n"
        "    E --> F\n"
        "```\n"
    )

    thinking_rule = ""
    if not settings.get("thinking", True):
        thinking_rule = "【思考模式已关闭】直接给出最终答案，不要进行任何思考、推理或分析。\n"

    output_rules = (
        "【输出规则】直接给出最终回复，禁止输出思考/规则复述。简单问候限20字以内。\n"
    )

    if agent_id == "CodeGen":
        return build_codegen_prompt(
            agent_id=agent_id,
            content=content,
            symbolic_text=json.dumps(public_symbolic(symbolic), ensure_ascii=False),
            memory_context=memory_context,
            shared_context=shared_context,
            date_context=date_context,
            workspace_context=workspace_context_block,
            actual_model_line=actual_model_line,
            reply_lang_instruction=reply_lang_instr,
            reasoning_instruction=reasoning_instr,
            thinking_rule=thinking_rule,
            code_format_rules=code_format_rules,
            mermaid_rules=mermaid_rules,
            output_rules=output_rules,
            tool_section=_build_tool_section(agent_id, available_tools, permission_mode) if tools_enabled else "",
            collab_section=collab_section,
        )

    if agent_id == "Orchestrator":
        return build_orchestrator_prompt(
            content=content,
            symbolic_text=json.dumps(public_symbolic(symbolic), ensure_ascii=False),
            memory_context=memory_context,
            shared_context=shared_context,
            date_context=date_context,
            workspace_context=workspace_context_block,
            actual_model_line=actual_model_line,
            reply_lang_instruction=reply_lang_instr,
            reasoning_instruction=reasoning_instr,
            thinking_rule=thinking_rule,
            mermaid_rules=mermaid_rules,
            tool_section=_build_tool_section(agent_id, available_tools, permission_mode) if tools_enabled else "",
            collab_section=collab_section,
            preprocess_context=preprocess_context,
            tools_enabled=tools_enabled,
        )

    if agent_id == "Architect":
        return build_architect_prompt(
            agent_id=agent_id,
            content=content,
            symbolic_text=json.dumps(public_symbolic(symbolic), ensure_ascii=False),
            memory_context=memory_context,
            shared_context=shared_context,
            date_context=date_context,
            workspace_context=workspace_context_block,
            actual_model_line=actual_model_line,
            reply_lang_instruction=reply_lang_instr,
            reasoning_instruction=reasoning_instr,
            thinking_rule=thinking_rule,
            mermaid_rules=mermaid_rules,
            tool_section=_build_tool_section(agent_id, available_tools, permission_mode) if tools_enabled else "",
            collab_section=collab_section,
        )

    if agent_id == "Deploy":
        return build_deploy_prompt(
            agent_id=agent_id,
            content=content,
            symbolic_text=json.dumps(public_symbolic(symbolic), ensure_ascii=False),
            memory_context=memory_context,
            shared_context=shared_context,
            date_context=date_context,
            workspace_context=workspace_context_block,
            actual_model_line=actual_model_line,
            reply_lang_instruction=reply_lang_instr,
            reasoning_instruction=reasoning_instr,
            thinking_rule=thinking_rule,
            code_format_rules=code_format_rules,
            mermaid_rules=mermaid_rules,
            output_rules=output_rules,
            tool_section=_build_tool_section(agent_id, available_tools, permission_mode) if tools_enabled else "",
            collab_section=collab_section,
        )

    # ── General agent prompt ────────────────────────────────────────
    prompt = build_general_prompt(
        agent_id=agent_id,
        role_desc=role_desc,
        content=content,
        symbolic_text=json.dumps(public_symbolic(symbolic), ensure_ascii=False),
        memory_context=memory_context,
        shared_context=shared_context,
        date_context=date_context,
        workspace_context=workspace_context_block,
        role_prompt=role_prompt,
        actual_model_line=actual_model_line,
        reply_lang_instruction=reply_lang_instr,
        reasoning_instruction=reasoning_instr,
        thinking_rule=thinking_rule,
        code_format_rules=code_format_rules,
        mermaid_rules=mermaid_rules,
        output_rules=output_rules,
        tool_section=_build_tool_section(agent_id, available_tools, permission_mode) if tools_enabled else "",
        collab_section=collab_section,
    )

    # ── Prompt size guard ──────────────────────────────────────────
    # If the prompt exceeds the safe limit, truncate the user-content
    # section (which carries the skill body — the most likely culprit).
    # We keep the last portion (the user's actual message) intact and
    # cut from the middle so the model still sees the instructions.
    MAX_PROMPT_CHARS = 80_000  # ~20K tokens, comfortable for most models
    prompt_orig_len = len(prompt)
    if prompt_orig_len > MAX_PROMPT_CHARS:
        # Find the "用户需求: " anchor — everything before it is
        # system instructions, everything at/after is user content.
        anchor = "用户需求: "
        anchor_idx = prompt.rfind(anchor)
        if anchor_idx > 0:
            system_part = prompt[:anchor_idx + len(anchor)]
            user_part = prompt[anchor_idx + len(anchor):]
            # Keep the last portion of user content (the user's actual
            # message is at the very end; the skill body is in the middle).
            user_budget = MAX_PROMPT_CHARS - len(system_part) - 200
            if user_budget < 2000:
                user_budget = 2000  # floor: at least keep the user's message
            if len(user_part) > user_budget:
                # Take head (first 20%) + tail (last 80%) of user content
                head_chars = int(user_budget * 0.2)
                tail_chars = user_budget - head_chars - 100
                user_part = (
                    user_part[:head_chars]
                    + f"\n\n... [技能说明已截断，原始用户输入共 {len(user_part)} 字符] ...\n\n"
                    + user_part[-tail_chars:]
                )
            prompt = system_part + user_part
        else:
            # No anchor — simple truncation with a note
            prompt = prompt[:MAX_PROMPT_CHARS - 200] + (
                f"\n\n... [Prompt 已截断，原始长度 {prompt_orig_len} 字符]"
            )

        logger.warning(
            "prompt truncated for agent=%s domain=%s orig=%d final=%d",
            agent_id, domain, prompt_orig_len, len(prompt),
        )

    return prompt

def _estimate_token_usage(user_text: str, model_output: str) -> tuple[int, int, int]:
    return prompt_context.estimate_token_usage(user_text, model_output)

def _format_conversation(conversation: list[dict]) -> str:
    return prompt_context.format_conversation_for_prompt(conversation)
