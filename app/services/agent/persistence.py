from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from app.db.init_db import now
from app.db.session import afetch_all, aexecute
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


logger = logging.getLogger("agenthub.agent.persistence")

# ── Default agents seeded for every new user ──────────────────────────

DEFAULT_AGENTS: list[tuple[str, str, str, str, str, list[str]]] = [
    (
        "Orchestrator", "orchestrator", "L2",
        "元调度器：接收用户意图，拆解任务并分派给领域 Agent，汇总结果。"
        "输入：用户原始需求 | 输出：任务分派方案、Agent 协同调度 | 约束：不替代领域 Agent 产出",
        "编排调度器",
        ["任务拆解", "Agent调度", "结果汇总"],
    ),
    (
        "Architect", "architect", "L1",
        "架构师：分析用户意图与项目结构，输出技术方案与文件影响范围。"
        "输入：用户意图、项目结构摘要 | 输出：技术方案、文件影响范围 | 约束：不直接写代码",
        "架构设计师",
        ["架构设计", "技术选型", "方案输出"],
    ),
    (
        "CodeGen", "codegen", "L2",
        "代码生成器：根据架构方案和上下文索引生成代码文件与 Diff 草案。"
        "输入：架构方案、上下文索引 | 输出：代码文件、Diff 草案 | 约束：不直接提交 Git",
        "代码生成器",
        ["代码生成", "文件创建", "多语言支持"],
    ),
    (
        "Review", "review", "L1",
        "代码审查员：审查 Diff 变更，对照规范与风险策略输出审查意见。"
        "输入：Diff、规范、风险策略 | 输出：审查意见、风险等级 | 约束：不修改部署配置",
        "代码审查员",
        ["代码审查", "安全审计", "规范检查"],
    ),
    (
        "Test", "test", "L1",
        "测试工程师：根据代码变更和测试策略生成测试用例与验证结果。"
        "输入：代码变更、测试策略 | 输出：测试结果、失败原因 | 约束：不绕过 Review 直接修改代码",
        "测试工程师",
        ["测试用例", "验证策略", "边界测试"],
    ),
    (
        "Implement", "implement", "L2",
        "实施工程师：将 CodeGen 生成的 Diff 落盘到工作区，处理合并冲突并跟踪落盘结果。"
        "输入：已审查 Diff | 输出：落盘文件清单、冲突报告 | 约束：不修改未审查代码",
        "实施工程师",
        ["文件落盘", "冲突解决", "变更跟踪"],
    ),
    (
        "Deploy", "deploy", "L3",
        "部署工程师：在 Review 通过后执行部署，生成预览 URL 和部署状态报告。"
        "输入：已确认 Diff、部署目标 | 输出：预览 URL、部署状态 | 约束：不部署未审查代码",
        "部署工程师",
        ["部署预览", "环境配置", "回滚预案"],
    ),
]

_SESSION_MGRS: dict[str, Any] = {}

# ── PM state machine ───────────────────────────────────────────────────
# Tracks the PM agent's current phase during a session.
_PM_STATES: dict[str, str] = {}  # session_id → PMState

# ── Degradation tracking ──────────────────────────────────────────────
_DEGRADATION: dict[str, dict] = {}  # session_id → degradation info
_RECOVERY_CHECK_INTERVAL = 60  # seconds between recovery probes

async def seed_default_agents_for_user(user_id: str) -> None:
    """Create the 6 foundational agents for a newly registered user.

    Uses ON CONFLICT DO NOTHING so it is safe to call repeatedly — existing
    agents are never overwritten.  After inserting, copies any avatar data
    from the system-level agents (``user_id=''``) so new users automatically
    inherit previously uploaded agent avatars.
    """
    for agent_id, domain, risk, duty_note, display_name, capability_tags in DEFAULT_AGENTS:
        await aexecute(
            "INSERT INTO agent_registry(agent_id,user_id,domain,status,adapter_type,"
            "risk_level,duty_note,display_name,capability_tags) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) "
            "ON CONFLICT(agent_id, user_id) DO NOTHING",
            agent_id, user_id, domain, "sleeping", "", risk, duty_note,
            display_name, json.dumps(capability_tags, ensure_ascii=False),
        )

    # ── Copy avatar data from system agents (user_id='') to this user's agents ──
    # This ensures avatars uploaded for the shared/system agents are visible to
    # every user without re-uploading.  Only copies when the target has no avatar.
    for agent_id, *_ in DEFAULT_AGENTS:
        await aexecute(
            "UPDATE agent_registry AS target "
            "SET avatar_data = source.avatar_data, "
            "    avatar_mime = source.avatar_mime, "
            "    avatar_url   = source.avatar_url "
            "FROM agent_registry AS source "
            "WHERE target.agent_id = source.agent_id "
            "  AND target.user_id = $1 "
            "  AND source.user_id = '' "
            "  AND source.avatar_data IS NOT NULL "
            "  AND target.avatar_data IS NULL",
            user_id,
        )

def _get_session_mgr_singleton(user_id: str = ""):
    """Return a cached per-user SessionMemoryManager."""
    global _SESSION_MGRS
    uid = user_id or "local-admin"
    if uid not in _SESSION_MGRS:
        from app.config import MEMORY_DIR
        from app.services.memory.session_memory import SessionMemoryManager
        from app.services.memory.storage import MemoryStorage
        user_dir = MEMORY_DIR / "users" / uid
        _SESSION_MGRS[uid] = SessionMemoryManager(MemoryStorage(user_dir))
    return _SESSION_MGRS[uid]

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
            # ── Record degradation exit for monitoring ──────────
            try:
                from app.services.performance_monitor import monitor
                monitor.record_degradation_exit(session_id, info.get("recovery_attempts", 0))
            except Exception:
                pass
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
    # ── Record degradation for performance monitoring ──────────
    try:
        from app.services.performance_monitor import monitor
        monitor.record_degradation_enter(session_id, reason, failed_models)
    except Exception:
        pass
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
    user_id: str = "",
) -> None:
    ts = now()
    await aexecute(
        "INSERT INTO messages(id,session_id,sender,content,type,fidelity_score,symbolic_json,prompt_tokens,completion_tokens,total_tokens,created_at,user_id) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
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
        user_id,
    )
    await aexecute("UPDATE sessions SET last_message_at=$1 WHERE id=$2", ts, session_id)

    # Invalidate the conversation history cache so the next prompt build
    # picks up the newly saved message.
    prompt_cache.invalidate_history(session_id)

async def list_messages(session_id: str) -> list[dict]:
    items = await afetch_all(
        "SELECT id,session_id AS \"sessionId\",sender,content,type,fidelity_score AS \"fidelityScore\",symbolic_json,created_at AS timestamp FROM messages WHERE session_id=$1 ORDER BY created_at",
        session_id,
    )
    for item in items:
        item["symbolic"] = json.loads(item.pop("symbolic_json") or "{}")
    return items
