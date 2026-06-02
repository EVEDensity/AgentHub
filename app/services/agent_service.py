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
from app.db.session import dict_rows, get_connection, one_row
from app.services.adapter_manager import adapter_manager
from app.services.auth.service import AuthService
from app.services.codegen_service import write_generated_files
from app.services.secret_service import decrypt_secret
from app.services.symbolic import (
    FIDELITY_HIGH,
    FIDELITY_LOW,
    FIDELITY_WARN,
    evaluate_contribution,
    fidelity_action,
    fidelity_grade,
    generate_symbolic_message,
    public_symbolic,
    requires_redistill,
)

logger = logging.getLogger("agenthub.agent_service")


AGENTS = {"Orchestrator", "Architect", "CodeGen", "Review", "Test", "Deploy"}
_RUNTIME: dict[str, dict] = {}

# ── Memory context cache (avoid scanning 200 files on every message) ──
_MEMORY_CACHE: dict[str, Any] = {"context": "", "ts": 0.0, "ttl": 60.0}
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


class CollaborationContext:
    """Shared memory for one multi-agent collaboration turn — with fidelity tracking."""

    def __init__(self, user_content: str):
        self.user_content = user_content
        self.participants: list[dict] = []
        self.contributions: list[dict] = []
        self._fidelity_scores: list[float] = []

    def register(self, agent: dict) -> None:
        self.participants.append(agent)

    def record(self, agent_id: str, domain: str, content: str) -> dict:
        """Record a contribution with fidelity evaluation.

        Returns the fidelity assessment dict so callers can decide whether to
        block, warn, or enrich downstream.
        """
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

        # Evaluate fidelity of this contribution
        fidelity = evaluate_contribution(self.user_content, clean, agent_id, domain)
        self._fidelity_scores.append(fidelity["fidelity_score"])

        self.contributions.append({
            "agent_id": agent_id,
            "domain": domain,
            "summary": summary[:300],
            "key_points": key_points[:3],
            "fidelity": fidelity,
        })
        return fidelity

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
            fid = c.get("fidelity", {})
            fid_note = ""
            if fid and fid.get("fidelity_score", 1.0) < FIDELITY_HIGH:
                fid_note = f" [保真度: {fid['fidelity_score']:.2f} — 请交叉验证]"
            peer_blocks.append(
                f"### {c['agent_id']}（{_ROLE_LABELS.get(c['domain'], 'general')}）{fid_note}\n"
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
    def overall_fidelity(self) -> float:
        """Average fidelity across all contributions in this turn."""
        if not self._fidelity_scores:
            return 1.0
        return round(sum(self._fidelity_scores) / len(self._fidelity_scores), 3)

    @property
    def summary(self) -> str:
        if not self.contributions:
            return ""
        overall = self.overall_fidelity
        fid_tag = f" [整体保真度: {overall:.2f}]" if overall < FIDELITY_HIGH else ""
        lines = [f"【本轮协作摘要】{fid_tag}"]
        for i, c in enumerate(self.contributions, 1):
            fid = c.get("fidelity", {})
            fid_str = f" (保真度: {fid['fidelity_score']:.2f})" if fid and fid.get("fidelity_score", 1.0) < FIDELITY_HIGH else ""
            lines.append(f"{i}. {c['agent_id']}（{_ROLE_LABELS.get(c['domain'], 'general')}）{fid_str}：{c['summary'][:120]}")
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


def extract_mentions(content: str) -> list[str]:
    return re.findall(r"@(\w+)", content)


def extract_skill_calls(content: str) -> list[str]:
    """Extract skill invocations like ``/skill-name`` from message content."""
    return re.findall(r"(?:^|\s)/(\w[\w-]*)", content)


def load_skill_prompt(skill_name: str) -> str | None:
    """Load a skill's SKILL.md body for prompt injection."""
    from pathlib import Path

    def _find_skill_dir(base: Path) -> Path | None:
        if not base.is_dir():
            return None
        candidate = base / skill_name
        if candidate.is_dir():
            return candidate
        try:
            for d in base.iterdir():
                if d.is_dir() and d.name.lower() == skill_name.lower():
                    return d
        except OSError:
            pass
        return None

    # User skills
    skill_dir = _find_skill_dir(Path.home() / ".claude" / "skills")
    # Project skills
    if not skill_dir:
        cwd = Path.cwd()
        for parent in [cwd, *cwd.parents]:
            skill_dir = _find_skill_dir(parent / ".claude" / "skills")
            if skill_dir:
                break
        if not skill_dir:
            import os
            proj_env = os.environ.get("AGENTHUB_PROJECT_DIR", "")
            if proj_env:
                skill_dir = _find_skill_dir(Path(proj_env) / ".claude" / "skills")
    if not skill_dir:
        return None
    try:
        skill_dir = skill_dir.resolve() if skill_dir.is_symlink() else skill_dir
    except OSError:
        pass
    for filename in ("SKILL.md", "skill.md"):
        skill_file = skill_dir / filename
        if skill_file.exists():
            try:
                raw = skill_file.read_text(encoding="utf-8")
                fm_match = re.match(r"^---\s*\n.*?\n---\s*\n", raw, re.DOTALL)
                if fm_match:
                    return raw[fm_match.end():].strip()
                return raw.strip()
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


def resolve_all_agents(content: str) -> list[dict]:
    """Return ALL valid agents @mentioned in the content.

    If no valid mention is found, falls back to the default chat agent.
    """
    agents: list[dict] = []
    seen: set[str] = set()
    for name in extract_mentions(content):
        if name in seen:
            continue
        seen.add(name)
        agent = one_row(
            "SELECT agent_id,domain,status,adapter_type,risk_level FROM agent_registry WHERE agent_id=?",
            (name,),
        )
        if agent:
            agents.append(agent)

    if agents:
        return agents

    # No valid mention — fall back to user-configured default, then Orchestrator
    default_row = one_row("SELECT value FROM system_config WHERE key='default_chat_agent'")
    default_agent_id = default_row["value"] if default_row else "Orchestrator"
    agent = one_row("SELECT agent_id,domain,status,adapter_type,risk_level FROM agent_registry WHERE agent_id=?", (default_agent_id,))
    return [agent] if agent else [{"agent_id": "Orchestrator", "domain": "orchestrator", "adapter_type": "mock", "risk_level": "L2"}]


def resolve_agent(content: str) -> dict:
    """Resolve a single agent from @mentions (kept for backward compatibility)."""
    return resolve_all_agents(content)[0]


def candidate_models_for_role(role: str) -> list[dict]:
    # 1) Explicit role bindings (role_bindings JOIN model_configs)
    rows = dict_rows(
        "SELECT mc.id,mc.provider,mc.model_name AS model_name,mc.api_key,mc.base_url,rb.prompt FROM role_bindings rb JOIN model_configs mc ON rb.model_config_id=mc.id WHERE rb.role=? AND mc.is_active=1 ORDER BY mc.id DESC",
        (role,),
    )
    if rows:
        return rows
    # 2) Agent's own config in agent_registry (adapter_type + base_model_name + base_url + api_key)
    agent_row = one_row("SELECT adapter_type,base_model_name,base_url,api_key FROM agent_registry WHERE agent_id=?", (role,))
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
    rows = dict_rows("SELECT id,provider,model_name,api_key,base_url,'' AS prompt FROM model_configs WHERE is_active=1 ORDER BY id DESC")
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


def save_message(
    session_id: str,
    sender: str,
    content: str,
    msg_type: str,
    score: float = 0.95,
    symbolic: dict | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    ts = now()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages(id,session_id,sender,content,type,fidelity_score,symbolic_json,prompt_tokens,completion_tokens,total_tokens,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
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
            ),
        )
        conn.execute("UPDATE sessions SET last_message_at=? WHERE id=?", (ts, session_id))


def list_messages(session_id: str) -> list[dict]:
    items = dict_rows(
        "SELECT id,session_id AS sessionId,sender,content,type,fidelity_score AS fidelityScore,symbolic_json,created_at AS timestamp FROM messages WHERE session_id=? ORDER BY created_at",
        (session_id,),
    )
    for item in items:
        item["symbolic"] = json.loads(item.pop("symbolic_json") or "{}")
    return items


async def call_agent(session_id: str, content: str, user_id: str, attachments: list[dict[str, Any]] | None = None, agent: dict | None = None, collab_ctx: str = "", token: Any = None, on_tool_event: Any = None) -> dict:
    if agent is None:
        agent = resolve_agent(content)
    domain = agent["domain"]
    msg_type = "code" if domain == "codegen" or any(word in content.lower() for word in ["code", "fastapi", "react", "代码", "实现"]) else "text"

    attachment_context, attachment_meta = _build_attachment_context(attachments)
    llm_input = content
    if attachment_context:
        llm_input = f"{content}\n\n[用户上传附件上下文]\n{attachment_context}"

    symbolic = generate_symbolic_message(
        llm_input, msg_type, session_id,
        sender_role=agent["agent_id"],
        intent_type=_intent_from_domain(domain, content),
        risk_level=agent.get("risk_level", "L1"),
    )
    models = choose_models(candidate_models_for_role(agent["agent_id"]))
    history = _build_conversation_history(session_id)
    memory_ctx = _build_memory_context()

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
    generated = write_generated_files(content_out, content) if agent["agent_id"] == "CodeGen" else None
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
        "fidelityScore": symbolic["fidelity_score"],
        "symbolic": public,
    }
    save_message(
        session_id,
        message["sender"],
        message["content"],
        message["type"],
        message["fidelityScore"],
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
) -> AsyncGenerator[str, None] | None:
    """Stream an agent response with full tool-calling support.

    Uses the same proven ``_run_tool_call_loop`` as ``call_agent()``,
    then streams the final synthesized result to the frontend in chunks.
    """
    if agent is None:
        agent = resolve_agent(content)
    models = choose_models(candidate_models_for_role(agent["agent_id"]))
    if not models:
        return None

    attachment_context, _ = _build_attachment_context(attachments)
    llm_input = content
    if attachment_context:
        llm_input = f"{content}\n\n[用户上传附件上下文]\n{attachment_context}"

    async def stream():
        # ── Phase 1: Run the proven tool-call loop ──────────────────
        # Wrap in try/except so that any exception in the tool-call loop
        # becomes a visible error message instead of crashing the generator
        # and producing a generic "模型调用失败" from the outer handler.
        try:
            result, usage, selected = await _run_tool_call_loop(
                session_id, content, user_id, agent, agent["domain"], llm_input,
                models, _build_conversation_history(session_id), _build_memory_context(),
                collab_ctx, token=token, on_tool_event=on_tool_event,
                streaming_executor=_get_streaming_executor(),
            )
        except Exception as _loop_exc:
            logger.exception(
                "stream_agent_response: _run_tool_call_loop crashed session=%s agent=%s",
                session_id, agent["agent_id"],
            )
            result = (
                f"模型调用异常：{_loop_exc}\n\n"
                "请检查：\n"
                "1. 模型 API Key 是否正确配置\n"
                "2. API 端点是否可达（网络/GFW）\n"
                "3. 模型适配器是否正常加载"
            )
            usage = {}
            selected = models[0] if models else {
                "provider": "unknown", "model_name": "unknown",
            }

        content_out = normalize_agent_output(agent["agent_id"], result, content)

        # ── Phase 2: Stream final content ──────────────────────────
        yield "<thinking>正在分析中...</thinking>\n\n"

        chunk_buf = ""
        separators = set("，。！？；：\n")
        for ch in content_out:
            if token and token.cancelled:
                return
            chunk_buf += ch
            if len(chunk_buf) >= 120 or ch in separators:
                yield chunk_buf
                chunk_buf = ""
        if chunk_buf:
            yield chunk_buf

        # ── Persist message & audit (best-effort, non-fatal) ────────
        try:
            usage_dict = usage or {}
            pt = usage_dict.get("prompt_tokens", max(1, len(llm_input) // 4))
            ct = usage_dict.get("completion_tokens", max(1, len(content_out) // 4))
            tt = usage_dict.get("total_tokens", pt + ct)
            from app.services.symbolic import compute_fidelity as _cf
            fid_score = _cf(llm_input, content_out, intent_type=_intent_from_domain(agent["domain"], content), has_code=("```" in content_out))
            symbolic_out = generate_symbolic_message(
                llm_input, "text", session_id,
                sender_role=agent["agent_id"],
                intent_type=_intent_from_domain(agent["domain"], content),
                risk_level=agent.get("risk_level", "L1"),
            )
            save_message(session_id, agent["agent_id"], content_out, "text", fid_score, public_symbolic(symbolic_out), pt, ct, tt)
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


def _build_conversation_history(session_id: str, max_chars: int = 2000) -> str:
    """Fetch recent messages from this session and format as a transcript.

    Gives every agent called in the session full awareness of what was
    discussed before — the foundation of long-term conversational memory.

    Messages are processed newest-first so that the most recent (and most
    relevant) context is always included when the char limit is hit.
    """
    try:
        rows = dict_rows(
            "SELECT sender,content FROM messages WHERE session_id=? AND type!='system' ORDER BY created_at DESC LIMIT 30",
            (session_id,),
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
        line = f"{r['sender']}：{content}"
        total += len(line)
        lines.append(line)
        if total > max_chars:
            break

    lines.reverse()  # chronological order for readability
    return "【会话历史记录 — 以下是本会话中之前的对话内容，请基于此上下文理解用户的后续问题】\n" + "\n".join(lines)


def _build_memory_context(max_chars: int = 3000, force: bool = False) -> str:
    """Load persistent memories and global summary and format as a prompt block.

    Cached with a 60-second TTL to avoid scanning 200+ files from disk on every
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
        global_summary = session_mgr.get_global_summary()
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
        headers = scanner.scan(max_files=200)
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


def _load_settings() -> dict[str, Any]:
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
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
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

def _get_agent_tools(agent_id: str) -> list[str] | None:
    """Get the list of tool names available to an agent.

    Queries agent_tool_bindings. If no bindings exist, returns None
    (meaning all tools are available by default).
    """
    try:
        rows = dict_rows(
            "SELECT td.name FROM tool_definitions td "
            "JOIN agent_tool_bindings atb ON td.id = atb.tool_id "
            "WHERE atb.agent_id=? AND atb.enabled=1 AND td.enabled=1",
            (agent_id,),
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




def build_prompt(agent_id: str, domain: str, content: str, symbolic: dict, role_prompt: str, collab_ctx: str = "", history: str = "", memory_context: str = "", tools_enabled: bool = True, available_tools: list[str] | None = None) -> str:
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
    settings = _load_settings()
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
    return (
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


def _estimate_token_usage(user_text: str, model_output: str) -> tuple[int, int, int]:
    prompt_tokens = max(1, len(user_text) // 4)
    completion_tokens = max(1, len(model_output) // 4)
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


def _log_tool_call(session_id: str, agent_id: str, tool_name: str, arguments: dict, result: dict) -> None:
    """Log a tool call to the audit table (best-effort, non-fatal)."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO tool_call_log (id, session_id, agent_id, tool_name, arguments_json, result_json, success, duration_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    session_id,
                    agent_id,
                    tool_name,
                    json.dumps(arguments, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    1 if result.get("success") else 0,
                    int(result.get("duration_ms", 0)),
                    now(),
                ),
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
) -> tuple[str, dict, dict]:
    """Execute the tool-calling loop for a single user message.

    1. Call LLM with tool-enabled prompt.
    2. Check response for tool_calls JSON.
    3. Execute tools, append results, re-call LLM.
    4. If no tool calls: return final text.
    5. Max iterations: 5. Overall timeout: 180s.
    """
    import asyncio as _asyncio
    from app.services.tool_executor import tool_executor
    from app.services.tool_registry import tool_registry

    executor = tool_executor
    conversation: list[dict] = [{"role": "user", "content": llm_input}]
    available_tools = _get_agent_tools(agent["agent_id"])

    LOOP_TIMEOUT = 180  # 3-minute overall safety cap

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
            prompt = build_prompt(
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
                            _log_tool_call(session_id, agent["agent_id"], tc["name"], tc.get("arguments", {}), tr)
                    except Exception:
                        pass

                    conversation.append({"role": "assistant", "tool_calls": tool_calls})
                    conversation.append({"role": "tool", "results": tool_results})

                    if iteration >= executor.MAX_ITERATIONS - 1:
                        final_text = executor.build_tool_result_context(tool_results)
                        break

                    continue  # loop back for synthesis

            # No tool calls found.
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
        # Real model response (not mock)
        if model_output and not model_output.startswith("本地 Mock 模型响应") and not model_output.startswith("模型调用失败"):
            stripped = _latex_to_unicode(_strip_think_tags(_strip_kimi_thinking(_strip_codegen_prefix(model_output))))
            # Safety net: model may ignore prompt and still output JSON for a
            # conversational question. If so, replace with a text reply.
            if _is_codegen_json_response(stripped) and not _is_code_request(original):
                return "我是 AgentHub 平台的代码生成专家，基于大规模语言模型构建。对于编程任务，我可以生成完整的可执行代码；如果你有代码相关的具体需求，请直接告诉我！"
            return stripped
        # Mock fallback: only return JSON for actual code-generation requests
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
    if model_output and not model_output.startswith("本地 Mock 模型响应"):
        return _remove_repeated_text(_latex_to_unicode(_strip_think_tags(_strip_kimi_thinking(model_output))))
    if agent_id == "Review":
        return "Review 完成：结构符合 FastAPI + Next.js 分层方案，建议生产环境收紧 CORS、加入鉴权、限流和审计。"
    if agent_id == "Test":
        return "Test 完成：请验证 /api/health、/api/admin/model-config、/ws/session-1、DAG 状态机和 Git 接口。"
    if agent_id == "Deploy":
        return "Deploy 准备完成：前端 http://localhost:3000，后端 http://localhost:8000。高风险发布需管理员确认。"
    return (
        "【多智能体身份卡片】\n\n"
        "一、模型基础信息\n"
        "- 模型定位：实习生协作代理（代码支持方向）\n"
        "- 输出风格：结构化、可执行、可审计\n"
        "- 典型应用：代码修改建议、缺陷定位、功能实现、重构与测试补充\n\n"
        "二、平台角色信息\n"
        "- 平台角色：AgentHub 多智能体执行单元\n"
        "- 岗位能力：需求理解、代码分析、变更建议、结果校验\n"
        "- 协作方式：按任务路由接入对应专业 Agent 联合处理\n\n"
        "三、交互引导\n"
        "请直接提交以下任一内容以开始执行：\n"
        "1) 需要分析或修改的代码片段/文件\n"
        "2) 当前遇到的报错现象与复现步骤\n"
        "3) 目标功能与验收标准\n"
        "我将基于你的输入给出分步方案与可落地结果。"
    )

