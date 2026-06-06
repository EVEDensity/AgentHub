from __future__ import annotations

import json
import logging
import random
import re
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from app.db.init_db import now
from app.db.session import afetch_all, afetch_one, aexecute
from app.services.adapter_manager import adapter_manager
from app.services.auth.service import AuthService
from app.services.codegen_service import write_generated_files
from app.services.secret_service import decrypt_secret
from app.utils.async_file import aexists, aisdir, aread_text, aiterdir, aread_json
from app.config import REQUEST_TIMEOUT_SECONDS
from app.services.symbolic import (
    generate_symbolic_message,
    public_symbolic,
)

logger = logging.getLogger("agenthub.agent_service")


AGENTS = {"Orchestrator", "Architect", "CodeGen", "Review", "Test", "Deploy"}
_RUNTIME: dict[str, dict] = {}

# ── Memory context cache (avoid scanning 200+ files on every message) ──
# TTL 300 s (5 min) — keeps the context cache warm within the prompt-cache
# window, reducing disk I/O while staying fresh enough for cross-session
# memory.  Invalidation is explicit via _invalidate_memory_cache() when
# new memories are written (extraction, /memory commands).
_MEMORY_CACHE: dict[str, Any] = {"context": "", "ts": 0.0, "ttl": 300.0}
_SESSION_MGR_SINGLETON: Any = None


def _get_session_mgr_singleton():
    """Return a cached SessionMemoryManager — avoids recreating on every message."""
    global _SESSION_MGR_SINGLETON
    if _SESSION_MGR_SINGLETON is None:
        from app.services.memory.session_memory import SessionMemoryManager
        _SESSION_MGR_SINGLETON = SessionMemoryManager()
    return _SESSION_MGR_SINGLETON

# ── Collaboration context for multi-agent communication ──────────────
# Models how real employees communicate: shared project context,
# structured peer summaries, role-specific expectations, distilled
# information rather than raw text dumps.

_ROLE_LABELS: dict[str, str] = {
    "orchestrator": "协调调度",
    "architect": "架构设计",
    "codegen": "代码生成",
    "review": "代码审查",
    "test": "测试验证",
    "deploy": "部署发布",
}

# ── PM state machine ───────────────────────────────────────────────────
# Tracks the PM agent's current phase during a session.
_PM_STATES: dict[str, str] = {}  # session_id → PMState

async def _set_pm_state(session_id: str, state: str, details: str = "") -> None:
    """Transition the PM state and broadcast to connected clients."""
    from app.services.websocket_manager import manager as _mgr
    prev = _PM_STATES.get(session_id, "IDLE")
    if prev == state:
        return
    _PM_STATES[session_id] = state
    try:
        await _mgr.broadcast_pm_state(session_id, state, prev, details)
    except Exception:
        pass

def _get_pm_state(session_id: str) -> str:
    return _PM_STATES.get(session_id, "IDLE")

# ── Degradation tracking ──────────────────────────────────────────────
_DEGRADATION: dict[str, dict] = {}  # session_id → degradation info
_RECOVERY_CHECK_INTERVAL = 60  # seconds between recovery probes

async def _check_degradation_recovery(session_id: str) -> bool:
    """Probe whether models have recovered from degradation.
    Returns True if recovery succeeded (degradation ended)."""
    info = _DEGRADATION.get(session_id)
    if not info or not info.get("active"):
        return True  # not degraded
    # Try a lightweight health check on the first failed model
    from app.services.adapter_manager import adapter_manager as _am
    from app.db.session import afetch_all
    rows = await afetch_all(
        "SELECT provider, model_name, base_url, api_key FROM model_configs WHERE is_active=true"
    )
    if not rows:
        return False
    row = rows[0]
    try:
        adapter = _am.get_adapter(row.get("provider", "mock"))
        test_prompt = "Hello, respond with 'OK' only."
        result = await adapter.execute_prompt(
            test_prompt,
            row.get("model_name", ""),
            row.get("api_key", ""),
            row.get("base_url", ""),
        )
        if result and len(result.strip()) > 0:
            # Recovery success — clear degradation
            _DEGRADATION.pop(session_id, None)
            from app.services.websocket_manager import manager as _mgr2
            await _mgr2.broadcast_degradation_change(
                session_id, False, "", "", [], 0,
            )
            logger.info("degradation_recovery: session=%s model recovered", session_id)
            return True
    except Exception:
        pass
    # Increment recovery attempts
    info["recovery_attempts"] = info.get("recovery_attempts", 0) + 1
    info["last_recovery_attempt"] = time.time()
    return False

async def _enter_degradation(session_id: str, reason: str, failed_models: list[str]) -> None:
    """Enter degradation mode for a session."""
    from app.services.websocket_manager import manager as _mgr
    info = {
        "active": True,
        "reason": reason,
        "started_at": now(),
        "failed_models": failed_models,
        "recovery_attempts": 0,
    }
    _DEGRADATION[session_id] = info
    await _mgr.broadcast_degradation_change(
        session_id, True, reason, info["started_at"], failed_models, 0,
    )
    logger.warning("degradation_enter: session=%s reason=%s models=%s",
                   session_id, reason, failed_models)


async def _schedule_recovery_check(session_id: str) -> None:
    """Background task: periodically check if models have recovered."""
    import asyncio as _asyncio
    for i in range(5):  # Try up to 5 times
        await _asyncio.sleep(_RECOVERY_CHECK_INTERVAL)
        info = _DEGRADATION.get(session_id)
        if not info or not info.get("active"):
            return  # already recovered or cleared
        recovered = await _check_degradation_recovery(session_id)
        if recovered:
            return
    # After 5 attempts, stop trying — user can trigger recheck by sending new message
    logger.info("degradation_recovery: exhausted attempts for session=%s", session_id)


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


def _build_attachment_context(attachments: list[dict[str, Any]] | None) -> tuple[str, list[dict[str, Any]]]:
    if not attachments:
        return "", []

    blocks: list[str] = []
    clean: list[dict[str, Any]] = []
    max_text_len = 12000

    for idx, item in enumerate(attachments, start=1):
        name = str(item.get("name", f"file_{idx}"))
        file_type = str(item.get("type", "text/plain"))
        size = int(item.get("size", 0) or 0)
        content = str(item.get("content", ""))

        is_image = file_type.startswith("image/") or name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"))
        if is_image:
            preview = content[:180]
            blocks.append(
                f"[附件图片 {idx}] name={name}, type={file_type}, size={size}\\n"
                f"data_url_prefix={preview}"
            )
        else:
            trimmed = content[:max_text_len]
            ext = name.split(".")[-1] if "." in name else "text"
            blocks.append(
                f"[附件文件 {idx}] name={name}, type={file_type}, size={size}\\n"
                f"```{ext}\\n{trimmed}\\n```"
            )

        clean.append({"name": name, "type": file_type, "size": size})

    return "\\n\\n".join(blocks), clean


def _build_quote_context(quote_references: list[dict[str, Any]] | None) -> str:
    """Format quoted chat messages as a context block for the AI prompt.

    Injects a ``[用户引用的历史消息]`` section above the current question,
    giving the model visibility into what the user is referencing.
    """
    if not quote_references:
        return ""

    blocks: list[str] = []
    for idx, qr in enumerate(quote_references, start=1):
        original_sender = str(qr.get("originalSender", "unknown"))
        original_timestamp = str(qr.get("originalTimestamp", ""))
        quoted_text = str(qr.get("quotedText", ""))
        is_full_message = bool(qr.get("isFullMessage", False))

        truncation_note = ""
        display_text = quoted_text
        if len(quoted_text) > 2000:
            display_text = quoted_text[:2000] + "\n… [已截断]"
            truncation_note = " (已截断)"

        msg_type = "完整消息" if is_full_message else "消息片段"

        blocks.append(
            f"[引自历史消息 {idx}] 发送者: {original_sender}, "
            f"时间: {original_timestamp}, 类型: {msg_type}{truncation_note}\n"
            f"---\n{display_text}\n---"
        )

    return "[用户引用的历史消息]\n\n" + "\n\n".join(blocks)


def extract_mentions(content: str) -> list[str]:
    return re.findall(r"@(\w+)", content)


def extract_skill_calls(content: str) -> list[str]:
    """Extract skill invocations like ``/skill-name`` from message content."""
    return re.findall(r"(?:^|\s)/(\w[\w-]*)", content)


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


_STREAMING_EXECUTOR = None


def _get_streaming_executor():
    """Return the application-level StreamingToolExecutor, if configured."""
    global _STREAMING_EXECUTOR
    if _STREAMING_EXECUTOR is not None:
        return _STREAMING_EXECUTOR
    try:
        from app.services.tools import get_streaming_executor as _gse
        _STREAMING_EXECUTOR = _gse()
        return _STREAMING_EXECUTOR
    except Exception:
        return None


async def resolve_all_agents(content: str) -> list[dict]:
    """Return ALL valid agents @mentioned in the content.

    If no valid mention is found, falls back to the default chat agent.
    """
    agents: list[dict] = []
    seen: set[str] = set()
    for name in extract_mentions(content):
        if name in seen:
            continue
        seen.add(name)
        agent = await afetch_one(
            "SELECT agent_id,domain,status,adapter_type,risk_level FROM agent_registry WHERE agent_id=$1",
            name,
        )
        if agent:
            agents.append(agent)

    if agents:
        return agents

    # No valid mention — fall back to user-configured default, then Orchestrator
    default_row = await afetch_one("SELECT value FROM system_config WHERE key='default_chat_agent'")
    default_agent_id = default_row["value"] if default_row else "Orchestrator"
    agent = await afetch_one("SELECT agent_id,domain,status,adapter_type,risk_level FROM agent_registry WHERE agent_id=$1", default_agent_id)
    return [agent] if agent else [{"agent_id": "Orchestrator", "domain": "orchestrator", "adapter_type": "mock", "risk_level": "L2"}]


async def resolve_agent(content: str) -> dict:
    """Resolve a single agent from @mentions (kept for backward compatibility)."""
    return (await resolve_all_agents(content))[0]


async def candidate_models_for_role(role: str) -> list[dict]:
    # 1) Explicit role bindings (role_bindings JOIN model_configs)
    rows = await afetch_all(
        "SELECT mc.id,mc.provider,mc.model_name AS model_name,mc.api_key,mc.base_url,rb.prompt FROM role_bindings rb JOIN model_configs mc ON rb.model_config_id=mc.id WHERE rb.role=$1 AND mc.is_active=1 ORDER BY mc.id DESC",
        role,
    )
    if rows:
        return rows
    # 2) Agent's own config in agent_registry (adapter_type + base_model_name + base_url + api_key)
    agent_row = await afetch_one("SELECT adapter_type,base_model_name,base_url,api_key FROM agent_registry WHERE agent_id=$1", role)
    if agent_row and agent_row.get("adapter_type") and agent_row.get("adapter_type") != "mock":
        return [{
            "id": 0,
            "provider": agent_row["adapter_type"],
            "model_name": agent_row.get("base_model_name") or "ping",  # "ping" → adapter uses its default_model
            "api_key": agent_row.get("api_key") or "",
            "base_url": agent_row.get("base_url") or "",
            "prompt": "",
        }]
    # 3) Fallback: any active model_config
    rows = await afetch_all("SELECT id,provider,model_name,api_key,base_url,'' AS prompt FROM model_configs WHERE is_active=1 ORDER BY id DESC")
    return rows or [{"id": 0, "provider": "mock", "model_name": "mock", "api_key": "", "base_url": "", "prompt": ""}]


def _score(model: dict) -> float:
    key = f"{model.get('provider')}:{model.get('model_name')}:{model.get('base_url','')}"
    s = _RUNTIME.get(key, {"ok": 0, "fail": 0, "latency": 1200.0})
    total = max(1, s["ok"] + s["fail"])
    success = s["ok"] / total
    latency_score = max(0.05, min(1.0, 1000.0 / max(80.0, s["latency"])))
    return 0.65 * success + 0.35 * latency_score + random.uniform(0.0, 0.05)


def choose_models(models: list[dict]) -> list[dict]:
    ranked = sorted(models, key=_score, reverse=True)
    return ranked


def _update_runtime(model: dict, ok: bool, latency_ms: float) -> None:
    key = f"{model.get('provider')}:{model.get('model_name')}:{model.get('base_url','')}"
    state = _RUNTIME.setdefault(key, {"ok": 0, "fail": 0, "latency": latency_ms})
    if ok:
        state["ok"] += 1
    else:
        state["fail"] += 1
    state["latency"] = state["latency"] * 0.7 + latency_ms * 0.3


async def save_message(
    session_id: str,
    sender: str,
    content: str,
    msg_type: str,
    score: float = 0.0,
    symbolic: dict | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    ts = now()
    await aexecute(
        "INSERT INTO messages(id,session_id,sender,content,type,fidelity_score,symbolic_json,prompt_tokens,completion_tokens,total_tokens,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
        str(uuid.uuid4()),
        session_id,
        sender,
        content,
        msg_type,
        score,
        json.dumps(symbolic or {}, ensure_ascii=False),
        prompt_tokens,
        completion_tokens,
        total_tokens,
        ts,
    )
    await aexecute("UPDATE sessions SET last_message_at=$1 WHERE id=$2", ts, session_id)


async def list_messages(session_id: str) -> list[dict]:
    items = await afetch_all(
        "SELECT id,session_id AS \"sessionId\",sender,content,type,fidelity_score AS \"fidelityScore\",symbolic_json,created_at AS timestamp FROM messages WHERE session_id=$1 ORDER BY created_at",
        session_id,
    )
    for item in items:
        item["symbolic"] = json.loads(item.pop("symbolic_json") or "{}")
    return items


async def call_agent(session_id: str, content: str, user_id: str, attachments: list[dict[str, Any]] | None = None, agent: dict | None = None, collab_ctx: str = "", token: Any = None, on_tool_event: Any = None, quote_references: list[dict[str, Any]] | None = None) -> dict:
    if agent is None:
        agent = await resolve_agent(content)
    domain = agent["domain"]
    msg_type = "code" if domain == "codegen" or any(word in content.lower() for word in ["code", "fastapi", "react", "代码", "实现"]) else "text"

    attachment_context, attachment_meta = _build_attachment_context(attachments)
    quote_context = _build_quote_context(quote_references)
    llm_input = content
    if quote_context:
        llm_input = f"{quote_context}\n\n[用户当前问题]\n{content}"
    if attachment_context:
        llm_input = f"{llm_input}\n\n[用户上传附件上下文]\n{attachment_context}"

    symbolic = generate_symbolic_message(
        llm_input, msg_type, session_id,
        sender_role=agent["agent_id"],
        intent_type=_intent_from_domain(domain, content),
        risk_level=agent.get("risk_level", "L1"),
    )
    models = choose_models(await candidate_models_for_role(agent["agent_id"]))
    history = await _build_conversation_history(session_id)
    memory_ctx = await _build_memory_context()

    # Use tool-enabled call loop (handles tool detection, execution, synthesis)
    result, usage, selected = await _run_tool_call_loop(
        session_id, content, user_id, agent, domain, llm_input,
        models, history, memory_ctx, collab_ctx, token=token,
        streaming_executor=_get_streaming_executor(),
        on_tool_event=on_tool_event,
    )

    content_out = normalize_agent_output(agent["agent_id"], result, content)
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
) -> AsyncGenerator[str, None] | None:
    """Stream an agent response with full tool-calling support.

    Uses the same proven ``_run_tool_call_loop`` as ``call_agent()``,
    then streams the final synthesized result to the frontend in chunks.
    """
    if agent is None:
        agent = await resolve_agent(content)

    # ── CloudCode adapter: subprocess-based, no model resolution needed ──
    if agent.get("adapter_type") == "cloud_code":
        return await _stream_cloudcode_response(
            session_id, content, user_id, agent, token=token,
            attachments=attachments, quote_references=quote_references,
        )

    models = choose_models(await candidate_models_for_role(agent["agent_id"]))
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

        chunk_queue: _asyncio.Queue = _asyncio.Queue()
        _SENTINEL = object()

        async def on_chunk(chunk: str) -> None:
            await chunk_queue.put(chunk)

        async def _run_loop():
            try:
                r, u, s = await _run_tool_call_loop(
                    session_id, content, user_id, agent, agent["domain"], llm_input,
                    models, await _build_conversation_history(session_id), await _build_memory_context(),
                    collab_ctx, token=token, on_tool_event=on_tool_event,
                    streaming_executor=_get_streaming_executor(),
                    stream_callback=on_chunk,
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
        while True:
            chunk = await chunk_queue.get()
            if chunk is _SENTINEL:
                break
            if token and token.cancelled:
                loop_task.cancel()
                return
            if chunk:
                full_text.append(chunk)
                yield chunk

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
            await save_message(session_id, agent["agent_id"], content_out, "text", 0.0, public_symbolic(symbolic_out), pt, ct, tt)
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


async def _build_conversation_history(session_id: str, max_chars: int = 4000) -> str:
    """Fetch recent messages from this session and format as a transcript.

    Gives every agent called in the session full awareness of what was
    discussed before — the foundation of long-term conversational memory.

    Messages are processed newest-first so that the most recent (and most
    relevant) context is always included when the char limit is hit.
    LIMIT 15 avoids fetching data that will be truncated anyway.
    """
    try:
        rows = await afetch_all(
            "SELECT sender,content FROM messages WHERE session_id=$1 AND type!='system' ORDER BY created_at DESC LIMIT 15",
            session_id,
        )
    except Exception:
        return ""

    if not rows:
        return ""

    # Keep DESC order (newest first), build lines until we hit max_chars,
    # then reverse to chronological for the prompt.
    lines: list[str] = []
    total = 0
    for r in rows:
        content = _strip_think_tags(r["content"])
        # Truncate individual messages to avoid one giant message consuming the budget
        line = f"{r['sender']}：{content[:800]}"
        total += len(line)
        lines.append(line)
        if total > max_chars:
            break

    lines.reverse()  # chronological order for readability
    return "【会话历史记录 — 以下是本会话中之前的对话内容，请基于此上下文理解用户的后续问题】\n" + "\n".join(lines)


async def _build_memory_context(max_chars: int = 3000, force: bool = False) -> str:
    """Load persistent memories and global summary and format as a prompt block.

    Cached with a TTL to avoid scanning 200+ files from disk on every
    single chat message. Call with force=True to bypass the cache (e.g. after
    memory extraction completes).
    """
    now_ts = time.monotonic()
    if not force and _MEMORY_CACHE["context"] and (now_ts - _MEMORY_CACHE["ts"]) < _MEMORY_CACHE["ttl"]:
        return _MEMORY_CACHE["context"]

    sections: list[str] = []

    # ── Global summary (cross-session aggregated) ─────────────────
    try:
        session_mgr = _get_session_mgr_singleton()
        global_summary = await session_mgr.get_global_summary()
        if global_summary:
            sections.append(
                "【全局记忆 — 跨会话聚合摘要】\n"
                "以下是对所有会话内容的综合摘要，代表项目的长期积累知识：\n\n"
                f"{global_summary}"
            )
    except Exception:
        pass

    # ── File-backed memories ──────────────────────────────────────
    try:
        from app.config import MEMORY_DIR
        from app.services.memory.storage import MemoryStorage
        from app.services.memory.scanner import MemoryScanner

        storage = MemoryStorage(MEMORY_DIR)
        scanner = MemoryScanner(storage)
        headers = await scanner.scan(max_files=200)
        if headers:
            lines: list[str] = []
            total = 0
            for h in headers:
                entry = f"- [{h.type.value}] {h.name}: {h.description}"
                freshness = scanner.freshness_text(h.mtime)
                if freshness:
                    entry += f" ({freshness})"
                total += len(entry)
                if total > max_chars:
                    lines.append("- ... [更多记忆已截断]")
                    break
                lines.append(entry)
            sections.append(
                "【持久化记忆上下文 — 跨会话存储的关键信息】\n"
                "以下是从项目记忆库中加载的已知信息。请优先参考这些内容，"
                "避免重复询问用户已经明确过的偏好或背景。\n"
                "如果记忆标注了\"较旧\"，请结合上下文判断其是否仍然有效。\n\n"
                + "\n".join(lines)
            )
    except Exception:
        pass

    if not sections:
        _MEMORY_CACHE["context"] = ""
        _MEMORY_CACHE["ts"] = now_ts
        return ""

    result = "\n\n".join(sections) + "\n─── 以上为持久化记忆，以下是会话上下文 ───\n"
    _MEMORY_CACHE["context"] = result
    _MEMORY_CACHE["ts"] = now_ts
    return result


def _invalidate_memory_cache() -> None:
    """Clear the memory context cache so next call rebuilds it."""
    _MEMORY_CACHE["context"] = ""
    _MEMORY_CACHE["ts"] = 0.0


async def _load_settings() -> dict[str, Any]:
    """Load general settings from the shared settings.json file.

    Returns a dict with defaults for all known keys.  This is a lightweight
    read-every-call so settings changes take effect without restart.
    """
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


def _build_tool_section(agent_id: str = "", available_tools: list[str] | None = None) -> str:
    """Build the tool-calling prompt section for injection into the agent prompt.

    Returns empty string if no tools are registered or tools are disabled.
    """
    from app.services.tool_registry import tool_registry

    tool_defs = tool_registry.build_prompt_section(available_tools)
    if not tool_defs:
        return ""

    instructions = tool_registry.build_calling_instructions()
    return "\n\n" + tool_defs + "\n\n" + instructions




async def build_prompt(agent_id: str, domain: str, content: str, symbolic: dict, role_prompt: str, collab_ctx: str = "", history: str = "", memory_context: str = "", tools_enabled: bool = True, available_tools: list[str] | None = None) -> str:
    # ── Shared session context (ALL agents see this FIRST) ──────────
    # This is the "main context window" — every agent reads it before
    # its role-specific instructions, ensuring a unified understanding
    # of what the conversation is about regardless of domain.
    shared_context = ""
    if history:
        shared_context = (
            "【共享会话上下文 — 所有Agent的对话记忆窗口】\n"
            "以下是你与用户及其他Agent之间的完整对话记录。请首先通读此上下文，"
            "理解当前话题和讨论脉络，再结合你的专业角色给出回复。\n"
            "即使话题与你的专业领域不完全匹配，也请基于上下文给出合理回答，"
            "不要以\"我是XX专家\"为由拒绝回复。\n\n"
            f"{history}\n"
            "─── 以上为共享记忆，以下是你的角色指令 ───\n"
        )

    collab_section = f"\n\n{collab_ctx}" if collab_ctx else ""

    # ── Current date (so the model knows what "today" is) ────────────
    # The model's training cutoff may be months ago.  Without this, the
    # model hallucinates dates or uses stale ones in search queries.
    from datetime import datetime as _dt
    today_str = _dt.now().strftime("%Y年%m月%d日")
    weekday_str = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][_dt.now().weekday()]
    date_context = f"【当前日期】{today_str} {weekday_str}。涉及\"今天\"、\"最新\"、\"最近\"等内容时，请基于此日期。\n"

    # ── Load settings for reply language, reasoning, thinking ───────
    settings = await _load_settings()
    reply_lang_instr = _build_reply_lang_instruction(settings)
    reasoning_instr = _build_reasoning_instruction(settings)

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

    thinking_rule = ""
    if not settings.get("thinking", True):
        thinking_rule = "【思考模式已关闭】直接给出最终答案，不要进行任何思考、推理或分析。\n"

    output_rules = (
        "【输出规则】\n"
        "1. 只输出最终回复内容，严禁输出思考过程、推理分析、规则复述\n"
        "2. 禁止使用标记：💭、思考分析、思考过程、思考内容、回复策略、<think>\n"
        "3. 简单问候仅回复一句简短问候（不超过20字），不介绍服务范围\n"
        "4. 严禁重复输出相同内容\n"
    )

    if agent_id == "CodeGen":
        return (
            f"{memory_context}"
            f"{shared_context}"
            f"{date_context}"
            f"你是 CodeGenAgent，AgentHub 多智能体平台中的代码生成专家。\n\n"
            f"{reply_lang_instr}"
            f"{reasoning_instr}"
            f"{thinking_rule}"
            f"{code_format_rules}\n"
            f"{output_rules}\n"
            "# 代码生成规则\n"
            "当且仅当用户明确请求生成代码、创建文件、修改代码、实现具体功能时，回复使用 JSON 格式：\n"
            "{\"files\":[{\"path\":\"相对路径\",\"content\":\"文件完整内容\"}]}\n"
            "- 路径只能是相对路径，代码必须完整可运行\n"
            "- JSON 不要包裹在 Markdown 代码块中\n\n"
            "# 非代码请求：直接以纯文本回复，严禁输出 JSON 格式。\n"
            + (_build_tool_section(agent_id, available_tools) if tools_enabled else "")
            + f"{collab_section}"
            f"符号消息: {json.dumps(public_symbolic(symbolic), ensure_ascii=False)}\n用户需求: {content}"
        )

    # ── General agent prompt ────────────────────────────────────────
    custom_role = role_prompt.strip() if role_prompt else ""
    prompt = (
        f"{memory_context}"
        f"{shared_context}"
        f"{date_context}"
        f"你是 AgentHub 平台中的 {agent_id}（{role_desc}）。\n"
        + (f"\n{custom_role}\n\n" if custom_role else "\n")
        + f"{reply_lang_instr}"
        f"{reasoning_instr}"
        f"{thinking_rule}"
        f"{code_format_rules}\n"
        f"{output_rules}\n"
        + (_build_tool_section(agent_id, available_tools) if tools_enabled else "")
        + f"{collab_section}"
        f"符号消息: {json.dumps(public_symbolic(symbolic), ensure_ascii=False)}\n用户需求: {content}"
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
    """Estimate token counts with CJK-aware heuristics.

    Pure ASCII text averages ~4 chars/token.  CJK characters (Chinese,
    Japanese, Korean) are denser — roughly 1.5 chars/token — because each
    logogram is a distinct token unit.  Mixing the two ratios gives a much
    better estimate than the naive ``len // 4`` for bilingual content.
    """
    def _count_tokens(text: str) -> int:
        cjk = sum(1 for c in text if '一' <= c <= '鿿' or '㐀' <= c <= '䶿')
        non_cjk = len(text) - cjk
        # CJK: ~1.5 chars/token, non-CJK: ~4 chars/token
        return max(1, int(cjk / 1.5 + non_cjk / 4))

    prompt_tokens = _count_tokens(user_text)
    completion_tokens = _count_tokens(model_output)
    total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens

def _format_conversation(conversation: list[dict]) -> str:
    """Format a multi-turn conversation (including tool calls/results) for prompt injection."""
    parts: list[str] = []
    for turn in conversation:
        role = turn.get("role", "")
        if role == "user":
            parts.append(f"【用户消息】\n{turn.get('content', '')}")
        elif role == "assistant" and "tool_calls" in turn:
            tcs = turn["tool_calls"]
            for tc in tcs:
                parts.append(
                    f"【工具调用】\n"
                    f"调用工具: {tc.get('name', 'unknown')}\n"
                    f"参数: {json.dumps(tc.get('arguments', {}), ensure_ascii=False)}"
                )
        elif role == "tool":
            from app.services.tool_executor import tool_executor
            parts.append(tool_executor.build_tool_result_context(turn.get("results", [])))
        elif role == "assistant":
            parts.append(f"【助手回复】\n{turn.get('content', '')}")
    return "\n\n".join(parts)


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

    async def _loop_body() -> tuple[str, dict, dict]:
        nonlocal final_text, usage, selected, adapter
        for iteration in range(executor.MAX_ITERATIONS):
            # ── Respect cancellation token ────────────────────────────
            if token and token.cancelled:
                logger.info("tool_loop cancelled at iteration %d", iteration)
                return "流式响应已被中断。", usage, selected

            conv_text = _format_conversation(conversation)
            symbolic = generate_symbolic_message(
                conv_text, "text", session_id,
                sender_role=agent["agent_id"],
                intent_type=_intent_from_domain(domain, conv_text),
                risk_level=agent.get("risk_level", "L1"),
            )
            prompt = await build_prompt(
                agent["agent_id"], domain, conv_text, symbolic,
                models[0].get("prompt", "") if models else "",
                collab_ctx, history, memory_ctx,
                tools_enabled=True, available_tools=available_tools,
            )

            logger.info(
                "tool_loop iter=%d: prompt_len=%d has_tool_section=%s",
                iteration, len(prompt),
                "tool_calls" in prompt.lower(),
            )

            # Try each model
            result = ""
            errors: list[str] = []
            for model in models:
                if token and token.cancelled:
                    return "流式响应已被中断。", usage, selected
                selected = model
                adapter = adapter_manager.get_adapter(model.get("provider", "mock"))
                started = time.perf_counter()
                try:
                    # Native OpenAI-format tools for iteration 0 only.
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
                    result = await adapter.execute_prompt(
                        prompt,
                        model.get("model_name", "mock"),
                        decrypt_secret(model.get("api_key", "")),
                        model.get("base_url", ""),
                        tools=native_tools if native_tools else None,
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
                tool_calls = executor.parse_tool_calls(result)
                logger.info(
                    "tool_loop iter=%d: parsed %d tool_calls: %s",
                    iteration, len(tool_calls),
                    [tc.get("name", "?") for tc in tool_calls],
                )
                if tool_calls:
                    if token and token.cancelled:
                        return "流式响应已被中断。", usage, selected

                    # Notify frontend
                    if on_tool_event:
                        try:
                            await on_tool_event("calling", tool_calls, None)
                        except Exception:
                            pass

                    # Execute tools
                    # ── Guardrail: classify each tool's risk before execution ──
                    from app.services.guardrails import classify_tool_risk as _ctr
                    high_risk_tools: list[dict] = []
                    for tc in tool_calls:
                        risk = _ctr(tc.get("name", ""), tc.get("arguments", {}))
                        if risk.requires_confirmation:
                            high_risk_tools.append({
                                "name": tc.get("name"),
                                "arguments": tc.get("arguments", {}),
                                "risk": risk.to_dict(),
                            })
                    if high_risk_tools and on_tool_event:
                        try:
                            await on_tool_event("risk_warning", high_risk_tools, None)
                        except Exception:
                            pass

                    if streaming_executor is not None:
                        for tc in tool_calls:
                            name = tc.get("name", "")
                            streaming_executor.enqueue(
                                name=name,
                                arguments=tc.get("arguments", {}),
                                is_concurrency_safe=tool_registry.get_concurrency_safety(name),
                            )
                        tool_results = await streaming_executor.process_queue()
                    else:
                        tool_results = await executor.execute_all(tool_calls)

                    if on_tool_event:
                        try:
                            await on_tool_event("done", tool_calls, tool_results)
                        except Exception:
                            pass

                    # Log tool calls (best-effort)
                    try:
                        for tc, tr in zip(tool_calls, tool_results):
                            await _log_tool_call(session_id, agent["agent_id"], tc["name"], tc.get("arguments", {}), tr)
                    except Exception:
                        pass

                    conversation.append({"role": "assistant", "tool_calls": tool_calls})
                    conversation.append({"role": "tool", "results": tool_results})

                    if iteration >= executor.MAX_ITERATIONS - 1:
                        final_text = executor.build_tool_result_context(tool_results)
                        break

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
                    prompt,
                    model.get("model_name", "mock"),
                    decrypt_secret(model.get("api_key", "")),
                    model.get("base_url", ""),
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

    return final_text, usage_dict, selected


def _remove_repeated_text(text: str) -> str:
    if not text:
        return text

    # 0. Detect full-text duplication: model sometimes echoes the entire
    #    response twice back-to-back.  We scan a sliding midpoint ±20 %
    #    to handle header prefixes like 【正式回复】 that offset alignment.
    n = len(text)
    if n >= 60:
        best_ratio = 0.0
        best_left = text
        # Sample every 4th position; full scan is unnecessary for this heuristic
        start = max(n // 3, 30)
        end = min(n * 2 // 3, n - 30)
        for mid in range(start, end, 4):
            left = text[:mid].strip()
            right = text[mid:].strip()
            if not left or not right:
                continue
            # Fast path: exact match
            if left == right:
                return left
            # Containment: the shorter half is substantially inside the longer
            # one (≥80 % length ratio prevents matching on shared-phrase overlap).
            if len(left) < len(right) and len(left) >= len(right) * 0.8 and left in right:
                return right
            if len(right) < len(left) and len(right) >= len(left) * 0.8 and right in left:
                return left
            # Fuzzy: longest common prefix ratio
            min_len = min(len(left), len(right))
            match_len = 0
            for j in range(min_len):
                if left[j] == right[j]:
                    match_len += 1
                else:
                    break
            ratio = match_len / max(len(left), len(right))
            if ratio > best_ratio:
                best_ratio = ratio
                best_left = left
        if best_ratio > 0.95:
            text = best_left

    # 1. Remove consecutive duplicate lines
    lines = text.split('\n')
    unique_lines = []
    prev_line = ""
    for line in lines:
        stripped = line.strip()
        if stripped and stripped != prev_line:
            unique_lines.append(line)
            prev_line = stripped
        elif not stripped:
            unique_lines.append(line)
    text = '\n'.join(unique_lines)

    # 2. Remove adjacent repeated phrases (12-80 char windows)
    text = _remove_repeated_phrases(text)

    # 3. Remove non-adjacent repeated paragraphs (30+ chars, same content
    #    appearing again later in the output regardless of intervening text)
    paragraphs = re.split(r'\n\n+', text)
    seen: set[str] = set()
    unique_paras: list[str] = []
    for p in paragraphs:
        stripped = p.strip()
        if len(stripped) >= 30:
            if stripped in seen:
                continue  # duplicate paragraph — drop it
            seen.add(stripped)
        unique_paras.append(p)
    text = '\n\n'.join(unique_paras)

    return text


def _remove_repeated_phrases(text: str) -> str:
    """Detect and remove consecutively repeated phrases (12+ chars) within text."""
    n = len(text)
    if n < 24:
        return text
    # Scan with decreasing window sizes to catch both long and short repetitions
    for window in range(min(n // 2, 80), 11, -1):
        i = 0
        while i + window * 2 <= n:
            phrase = text[i:i + window]
            # Check if this phrase immediately repeats
            if text[i + window:i + window * 2] == phrase:
                # Remove the duplicate and restart scan from this position
                text = text[:i + window] + text[i + window * 2:]
                n = len(text)
                continue
            i += 1
    return text


def _strip_kimi_thinking(text: str) -> str:
    """Clean up Kimi native thinking markers that leak into the reply.

    Kimi K2.6 may output multiple 💭 + 【思考分析】 thinking blocks followed by
    a final 【正式回复】 marker.  When the final marker is present, everything
    before it is discarded; otherwise we strip the thinking markers inline.
    """
    # Strategy 1: locate the last 【正式回复】 and keep only what follows it
    parts = re.split(r"【正式回复】\s*", text)
    if len(parts) > 1 and parts[-1].strip():
        return parts[-1].strip()
    # Strategy 2: no final marker — strip thinking patterns inline
    text = re.sub(r"💭\s*", "", text)
    text = re.sub(r"【思考分析】已完成\s*\(\d+字\)", "", text)
    text = re.sub(r"^(回复策略：.*|思考内容：.*|注意：用户消息.*|核心需求是.*)\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> tags injected by the adapter for reasoning_content.

    These tags drive the frontend ThinkingPanel but pollute saved messages and
    conversation history — strip them before persistence.
    """
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()


def _strip_codegen_prefix(text: str) -> str:
    """Remove decorative prefixes models sometimes add before JSON."""
    return re.sub(r"^(【[^】]*】\s*)+", "", text.strip())


def _is_codegen_json_response(text: str) -> bool:
    """Check if text is a CodeGen-style JSON file manifest."""
    try:
        data = json.loads(text)
        return isinstance(data, dict) and isinstance(data.get("files"), list)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False


def _is_code_request(text: str) -> bool:
    """Check whether the user is asking for code generation."""
    keywords = [
        "生成", "创建", "实现", "写", "编写", "修改", "添加", "改", "开发",
        "code", "fastapi", "react", "api", "页面", "组件", "路由", "接口",
        "帮我做", "帮我写", "做一个", "写一个", "改一下", "加一个",
    ]
    return any(w in text.lower() for w in keywords)


def _latex_to_unicode(text: str) -> str:
    """Convert common LaTeX math commands to Unicode symbols.

    Models (especially Kimi K2.6) often output LaTeX like \\div, \\times
    which the frontend cannot render.  Map them to proper Unicode glyphs.
    Longer patterns are replaced first so that e.g. \\rightarrow is handled
    before the shorter \\to contained within it.
    """
    replacements = [
        ("\\textdegree", "°"),
        ("\\Leftrightarrow", "⇔"),
        ("\\rightarrow", "→"),
        ("\\leftarrow", "←"),
        ("\\Rightarrow", "⇒"),
        ("\\subseteq", "⊆"),
        ("\\notin", "∉"),
        ("\\subset", "⊂"),
        ("\\approx", "≈"),
        ("\\equiv", "≡"),
        ("\\propto", "∝"),
        ("\\infty", "∞"),
        ("\\ldots", "…"),
        ("\\cdots", "⋯"),
        ("\\degree", "°"),
        ("\\angle", "∠"),
        ("\\triangle", "△"),
        ("\\forall", "∀"),
        ("\\exists", "∃"),
        ("\\emptyset", "∅"),
        ("\\times", "×"),
        ("\\cdot", "·"),
        ("\\leq", "≤"),
        ("\\geq", "≥"),
        ("\\neq", "≠"),
        ("\\sim", "∼"),
        ("\\sum", "∑"),
        ("\\prod", "∏"),
        ("\\int", "∫"),
        ("\\div", "÷"),
        ("\\pm", "±"),
        ("\\mp", "∓"),
        ("\\sqrt", "√"),
        ("\\alpha", "α"),
        ("\\beta", "β"),
        ("\\gamma", "γ"),
        ("\\delta", "δ"),
        ("\\epsilon", "ε"),
        ("\\theta", "θ"),
        ("\\lambda", "λ"),
        ("\\mu", "μ"),
        ("\\pi", "π"),
        ("\\sigma", "σ"),
        ("\\omega", "ω"),
        ("\\land", "∧"),
        ("\\lor", "∨"),
        ("\\neg", "¬"),
        ("\\cup", "∪"),
        ("\\cap", "∩"),
        ("\\to", "→"),
        ("\\in", "∈"),
        ("\\%", "%"),
        ("\\_", "_"),
        ("\\&", "&"),
        ("\\#", "#"),
    ]
    for latex, uni in replacements:
        text = text.replace(latex, uni)
    return text


def normalize_agent_output(agent_id: str, model_output: str, original: str) -> str:
    if agent_id == "CodeGen":
        # Case A: Real model response (not mock, not failure)
        is_codegen_mock = (
            not model_output
            or model_output.startswith("本地 Mock 模型响应")
        )
        is_codegen_failure = model_output.startswith("模型调用失败")

        if not is_codegen_mock and not is_codegen_failure:
            stripped = _latex_to_unicode(_strip_think_tags(_strip_kimi_thinking(_strip_codegen_prefix(model_output))))
            # Safety net: model may ignore prompt and still output JSON for a
            # conversational question. If so, replace with a text reply.
            if _is_codegen_json_response(stripped) and not _is_code_request(original):
                return "我是 AgentHub 平台的代码生成专家，基于大规模语言模型构建。对于编程任务，我可以生成完整的可执行代码；如果你有代码相关的具体需求，请直接告诉我！"
            return stripped

        # Case B: Model was called but ALL failed → show actual error
        if is_codegen_failure:
            error_detail = model_output.replace("模型调用失败，已降级为本地响应：", "").strip()
            return (
                f"⚠️ 模型调用失败\n\n"
                f"错误详情：{error_detail}\n\n"
                f"可能原因：\n"
                f"1. 请求超时（当前超时：{REQUEST_TIMEOUT_SECONDS:.0f} 秒）— 复杂代码生成时间较长\n"
                f"2. Prompt 超出模型上下文窗口限制\n"
                f"3. 模型 API 返回错误（key 无效、限流、余额不足等）\n\n"
                f"建议：重试、简化需求、或检查管理后台的模型配置。"
                f"查看服务端日志获取完整错误堆栈（grep 'llm_fail' 或 'tool_loop'）。"
            )

        # Case C: Mock / no-model — fallback JSON or text
        if _is_code_request(original):
            return json.dumps(
                {
                    "files": [
                        {
                            "path": "backend/health_router.py" if "fastapi" in original.lower() or "路由" in original else "frontend/GeneratedPanel.jsx",
                            "content": "from fastapi import APIRouter\n\nrouter = APIRouter(prefix=\"/generated\", tags=[\"generated\"])\n\n\n@router.get(\"/health\")\nasync def generated_health() -> dict[str, str]:\n    return {\"status\": \"ok\", \"module\": \"agenthub-generated\"}\n",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        return "Mock 运行模式下 CodeGen 不可用，请前往管理后台为 CodeGen Agent 配置真实的大模型 API Key。配置后，我可以根据你的需求生成完整的可执行代码。"
    # ── Model failure fallback ──────────────────────────────────────
    # Two distinct error cases, handled very differently:
    #
    # Case A: "模型调用失败" — the model WAS called but ALL candidates
    #   threw exceptions (timeout, context overflow, API error, etc.).
    #   The error text includes the real exception — SHOW IT so the
    #   developer can diagnose the root cause.  Hiding it behind a
    #   generic "API unreachable" message is misleading and wastes time.
    #
    # Case B: "本地 Mock 模型响应" / empty — MockAdapter was used,
    #   meaning the model was NEVER called (no API key, ENABLE_REAL_LLM
    #   is false, or the provider is literally "mock").  Use domain-
    #   specific fallbacks or a graceful degradation message.
    #
    is_mock = (
        not model_output
        or model_output.startswith("本地 Mock 模型响应")
    )
    is_model_failure = model_output.startswith("模型调用失败")

    if not is_mock and not is_model_failure:
        return _remove_repeated_text(_latex_to_unicode(_strip_think_tags(_strip_kimi_thinking(model_output))))

    # ── Case A: Real model error — surface the actual failure reason ──
    if is_model_failure:
        error_detail = model_output.replace("模型调用失败，已降级为本地响应：", "").strip()
        return (
            f"⚠️ 模型调用失败\n\n"
            f"错误详情：{error_detail}\n\n"
            f"可能原因：\n"
            f"1. 请求超时（当前超时：{REQUEST_TIMEOUT_SECONDS:.0f} 秒）— 复杂任务生成时间较长，可尝试简化需求\n"
            f"2. Prompt 超出模型上下文窗口限制（skill 上下文 + 系统提示词 + 历史消息）\n"
            f"3. 模型 API 返回错误（key 无效、限流、余额不足等）\n"
            f"4. max_tokens 不足导致输出被截断\n\n"
            f"建议：\n"
            f"- 重试当前请求（可能是临时网络波动）\n"
            f"- 简化输入或拆分任务（如分步生成页面结构、样式、脚本）\n"
            f"- 在管理后台检查模型配置（API Key / Base URL / max_tokens）\n"
            f"- 查看服务端日志获取完整错误堆栈（grep 'llm_fail' 或 'tool_loop'）"
        )

    # ── Case B: Mock / no-model — graceful degradation ─────────────
    if agent_id == "CodeGen":
        if _is_code_request(original):
            return json.dumps(
                {
                    "files": [
                        {
                            "path": "backend/health_router.py" if "fastapi" in original.lower() or "路由" in original else "frontend/GeneratedPanel.jsx",
                            "content": "from fastapi import APIRouter\n\nrouter = APIRouter(prefix=\"/generated\", tags=[\"generated\"])\n\n\n@router.get(\"/health\")\nasync def generated_health() -> dict[str, str]:\n    return {\"status\": \"ok\", \"module\": \"agenthub-generated\"}\n",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        return "Mock 运行模式下 CodeGen 不可用，请前往管理后台为 CodeGen Agent 配置真实的大模型 API Key。配置后，我可以根据你的需求生成完整的可执行代码。"

    if agent_id == "Review":
        return "Review 完成：结构符合 FastAPI + Next.js 分层方案，建议生产环境收紧 CORS、加入鉴权、限流和审计。"
    if agent_id == "Test":
        return "Test 完成：请验证 /api/health、/api/admin/model-config、/ws/session-1、DAG 状态机和 Git 接口。"
    if agent_id == "Deploy":
        return "Deploy 准备完成：前端 http://localhost:3000，后端 http://localhost:8000。高风险发布需管理员确认。"

    # Default fallback — a useful response acknowledging the degradation
    return (
        "⚠️ 当前模型 API 暂时不可达，系统已降级为本地响应模式。\n\n"
        "你的需求已记录，以下是基于本地规则的建议：\n\n"
        "1. 请检查管理后台的模型配置是否正确（API Key / Base URL / 端点可达性）\n"
        "2. 如果是代码相关需求，CodeGen Agent 配置真实模型后可自动生成代码\n"
        "3. 当前会话的消息已保存，模型恢复后可继续处理\n\n"
        "如有紧急需求，请通过管理后台切换至可用模型或联系系统管理员。"
    )


# ═══════════════════════════════════════════════════════════════════════
# CloudCode subprocess-based streaming
# ═══════════════════════════════════════════════════════════════════════

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
    from app.services.event_mapper import is_diff_event, is_terminal_event
    from app.services.websocket_manager import manager as ws_manager

    adapter = adapter_manager.get_adapter("cloud_code")
    message_id = str(uuid.uuid4())

    # Build attachment / quote context (same as normal stream path)
    attachment_context, _ = _build_attachment_context(attachments)
    quote_context = _build_quote_context(quote_references)
    llm_input = content
    if quote_context:
        llm_input = f"{quote_context}\n\n[用户当前问题]\n{content}"
    if attachment_context:
        llm_input = f"{llm_input}\n\n[用户上传附件上下文]\n{attachment_context}"

    full_text: list[str] = []

    async def stream():
        nonlocal full_text

        try:
            async for json_line in adapter.stream_prompt(llm_input, "cloud-code"):
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

                evt_type = obj.get("type", "")

                # ── Text event → yield for SSE + collect ──────────
                if evt_type == "text":
                    chunk = obj.get("content", "")
                    if chunk:
                        full_text.append(chunk)
                        yield chunk

                # ── Tool use → broadcast to frontend ──────────────
                elif evt_type == "tool_use":
                    tool_name = obj.get("name", "unknown")
                    tool_args = obj.get("args", obj.get("arguments", {}))

                    # Broadcast tool_call event
                    await ws_manager.broadcast(
                        session_id,
                        {
                            "event": "tool_call",
                            "sessionId": session_id,
                            "messageId": message_id,
                            "toolCalls": [
                                {
                                    "name": tool_name,
                                    "arguments": tool_args,
                                    "status": "calling",
                                }
                            ],
                        },
                    )

                    # Diff events → also broadcast as diff_update
                    if is_diff_event(obj):
                        await ws_manager.broadcast(
                            session_id,
                            {
                                "event": "diff_update",
                                "sessionId": session_id,
                                "messageId": message_id,
                                "path": obj.get("path", ""),
                                "diff": obj.get("diff", ""),
                                "timestamp": now(),
                            },
                        )

                    # Terminal events → broadcast terminal_output
                    if is_terminal_event(obj):
                        cmd_output = obj.get("output", obj.get("stdout", ""))
                        if cmd_output:
                            await ws_manager.broadcast(
                                session_id,
                                {
                                    "event": "terminal_output",
                                    "sessionId": session_id,
                                    "messageId": message_id,
                                    "content": cmd_output,
                                    "sender": agent["agent_id"],
                                    "timestamp": now(),
                                },
                            )

                # ── End event → finalise ──────────────────────────
                elif evt_type == "end":
                    final_text = obj.get("content", "") or "\n".join(full_text)
                    if final_text:
                        # Broadcast final message
                        await ws_manager.broadcast(
                            session_id,
                            {
                                "event": "message",
                                "sessionId": session_id,
                                "messageId": message_id,
                                "content": final_text,
                                "sender": agent["agent_id"],
                                "timestamp": now(),
                                "type": "text",
                            },
                        )

                    # Persist the message
                    try:
                        pt = max(1, len(llm_input) // 4)
                        ct = max(1, len(final_text) // 4)
                        await save_message(
                            session_id, agent["agent_id"], final_text, "text", 0.0,
                            public_symbolic(
                                generate_symbolic_message(
                                    llm_input, "text", session_id,
                                    sender_role=agent["agent_id"],
                                    intent_type=_intent_from_domain(agent.get("domain", ""), content),
                                    risk_level=agent.get("risk_level", "L1"),
                                )
                            ),
                            pt, ct, pt + ct,
                        )
                    except Exception:
                        logger.debug("save_message failed in cloudcode stream", exc_info=True)

                    # Trigger post-agent pipeline (background)
                    _asyncio.create_task(
                        _run_cloudcode_post_hooks(session_id, agent["agent_id"])
                    )
                    return

        except Exception as exc:
            logger.exception("CloudCode stream crashed session=%s agent=%s", session_id, agent["agent_id"])
            yield f"\n[CloudCode 执行异常：{exc}]"

    return stream()


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
    """Read a file's content from the workspace."""
    from pathlib import Path
    from app.config import PROJECT_ROOT
    try:
        full = Path(PROJECT_ROOT) / file_path if not Path(file_path).is_absolute() else Path(file_path)
        if full.exists() and full.is_file():
            return full.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return None
