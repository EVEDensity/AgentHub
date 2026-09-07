from __future__ import annotations

import logging
import re
import uuid

from pydantic import BaseModel, Field

from app.db.init_db import now
from app.db.session import afetch_all, afetch_one, aexecute
from app.services.auth_service import get_current_user
from app.services.agent_service import list_messages
from app.services.auto_name_prompt import build_auto_name_prompt, extract_local_title

logger = logging.getLogger("agenthub.chat")

class SessionCreateRequest(BaseModel):
    name: str = "新建会话"
    visibility: str = "private"  # 'private' | 'public'


class InviteRequest(BaseModel):
    model_config = {"populate_by_name": True}
    user_id: str = Field(default="", validation_alias="userId")       # direct user ID (preferred)
    user_name: str = Field(default="", validation_alias="userName")   # alternative: resolve by username
    role: str = "member"                                              # 'member' | 'viewer'


class RoleChangeRequest(BaseModel):
    role: str  # 'member' | 'viewer'


GENERIC_SESSION_NAMES = {'新建会话', '默认会话', 'new session', 'untitled'}


def is_generic_name(name: str) -> bool:
    """Check whether a session name is a default/placeholder that should be auto-named."""
    stripped = (name or "").strip().lower()
    if not stripped:
        return True
    for pattern in GENERIC_SESSION_NAMES:
        if stripped.startswith(pattern):
            return True
    return False


def _build_auto_name_prompt(messages: list[dict]) -> str:
    """Build a prompt for the LLM to generate a session title from the first interaction.

    The title is determined primarily by the user's first message — this ensures
    the conversation name reflects what the user originally asked about, not
    whatever tangent the conversation may have drifted into.
    """
    # Separate user messages from agent responses
    user_msgs = [m for m in messages if m.get("sender") not in ("system", "agent", "orchestrator")]
    agent_msgs = [m for m in messages if m.get("sender") in ("agent", "orchestrator", "system")]

    # The first user message is the primary signal
    first_user = (user_msgs[0].get("content") or "").strip() if user_msgs else ""

    if not first_user:
        return ""

    # Truncate if needed
    if len(first_user) > 300:
        first_user = first_user[:300] + "..."

    # Optionally include the first agent response for context
    first_reply = ""
    if agent_msgs:
        reply_content = (agent_msgs[0].get("content") or "").strip()
        if reply_content:
            if len(reply_content) > 200:
                reply_content = reply_content[:200] + "..."
            first_reply = f"\n助手回复摘要：{reply_content}"

    return (
        "你是一个对话标题生成器。请根据用户的第一条消息（对话的初始交互）生成一个简洁的标题。\n\n"
        "要求：\n"
        "1. 标题必须为中文，3-15字\n"
        "2. 准确概括用户的核心意图或问题主题\n"
        "3. 有区分度，方便日后查找\n"
        "4. 不要生成\"新建会话\"、\"未命名\"、\"对话\"、\"聊天\"等无意义标题\n"
        "5. 只输出标题文本，不要加引号、编号或任何额外说明\n\n"
        f"用户第一条消息：{first_user}{first_reply}\n\n标题："
    )


def _extract_local_title(first_message: str) -> str:
    """Local fallback: extract a meaningful Chinese title from the first user message.

    Uses keyword pattern matching to generate a concise title (3-15 chars)
    without calling any LLM. Handles common patterns like:
    - \"@Agent do something\" → \"do something\"
    - \"帮我实现XXX\" → \"实现XXX\"
    - \"Generate a FastAPI...\" → translates common English intents
    """
    import re
    text = first_message.strip()

    # Strip @mentions and leading/trailing noise
    text = re.sub(r'@\w+\s*', '', text).strip()
    if not text:
        return ""

    # Common action patterns → Chinese title keywords
    patterns = [
        (r'(?:生成|创建|写|编写|实现|开发|搭建)\s*(?:一个?\s*)?(.{2,30}?)(?:文件|代码|页面|模块|功能|路由|接口|API|组件)?$', ''),
        (r'(?:帮我|请|麻烦|帮忙)\s*(.{2,30}?)(?:谢谢|感谢)?$', ''),
        (r'(?:如何|怎么|怎样)\s*(.{2,30}?)(?:\?|？)?$', ''),
        (r'(?:修复|修改|优化|调整|更新)\s*(.{2,30}?)$', ''),
        (r'(?:分析|审查|检查|review|analyze)\s*(.{2,30}?)$', ''),
    ]

    for pattern, _ in patterns:
        m = re.search(pattern, text)
        if m:
            keyword = m.group(1).strip().rstrip('。！？.?！，,')
            if 2 <= len(keyword) <= 20:
                return keyword

    # English intent mapping (common dev commands)
    eng_patterns = [
        (r'[Gg]enerate\s+(?:a\s+)?(.{2,40}?)(?:\s+(?:file|route|code|page|module))?$', '生成'),
        (r'[Cc]reate\s+(?:a\s+)?(.{2,40}?)$', '创建'),
        (r'[Ff]ix\s+(?:the\s+)?(.{2,40}?)$', '修复'),
        (r'[Ii]mplement\s+(?:a\s+)?(.{2,40}?)$', '实现'),
        (r'[Cc]ode\s+(?:review|check)\s+(?:of\s+)?(.{2,40}?)$', '审查'),
    ]

    for pattern, prefix in eng_patterns:
        m = re.search(pattern, text)
        if m:
            keyword = m.group(1).strip().rstrip('.!?')
            # Translate common English dev terms
            translations = {
                'health route': '健康检查路由', 'health check': '健康检查',
                'api': 'API接口', 'rest api': 'REST接口',
                'login': '登录功能', 'auth': '认证功能',
                'database': '数据库', 'config': '配置管理',
                'test': '测试用例', 'component': '组件开发',
                'middleware': '中间件', 'docker': 'Docker部署',
                'frontend': '前端页面', 'backend': '后端服务',
                'pipeline': 'CI/CD流水线', 'deploy': '部署流程',
            }
            keyword_lower = keyword.lower()
            for eng, chn in translations.items():
                if eng in keyword_lower:
                    return f'{prefix}{chn}'
            # Generic prefix + English keyword (limited to 15 chars)
            title = f'{prefix}{keyword[:10]}'
            return title[:15]

    # Last resort: take the first meaningful segment
    # Split on common delimiters and take the first meaningful chunk
    parts = re.split(r'[,，。！？\n!?]', text)
    for part in parts:
        part = part.strip()
        # Remove pure punctuation / short fragments
        clean = re.sub(r'[^\w一-鿿]', '', part)
        if len(clean) >= 3:
            if len(part) <= 15:
                return part
            return part[:15]

    # Absolute fallback
    return text[:15] if len(text) >= 3 else ""


async def _call_llm_for_name(prompt: str) -> str | None:
    """Call the best available LLM to generate a session name. Returns name or None."""
    if not prompt:
        return None

    from app.services.adapter_manager import adapter_manager
    from app.services.secret_service import decrypt_secret

    candidates: list[dict] = []

    # 1) Try model_configs table
    try:
        rows = await afetch_all(
            "SELECT provider, model_name, api_key, base_url "
            "FROM model_configs WHERE is_active=1 ORDER BY id DESC LIMIT 5"
        )
        for row in rows:
            key = decrypt_secret(row.get("api_key") or "")
            if key and (row.get("provider") or "").lower() != "mock":
                candidates.append({**row, "api_key": key})
    # noqa: BLE001 - best-effort, never block main path
    except Exception:
        pass

    # 2) Try agent_registry
    try:
        agent_rows = await afetch_all(
            "SELECT DISTINCT adapter_type AS provider, base_model_name AS model_name, "
            "api_key, base_url "
            "FROM agent_registry WHERE api_key IS NOT NULL AND api_key != '' "
            "AND adapter_type != '' AND adapter_type IS NOT NULL"
        )
        for row in agent_rows:
            key = decrypt_secret(row.get("api_key") or "")
            if key and (row.get("provider") or "").lower() != "mock":
                candidates.append({**row, "api_key": key})
    # noqa: BLE001 - best-effort, never block main path
    except Exception:
        pass

    # 3) Fallback to env vars
    from app.config import OPENAI_API_KEY, ANTHROPIC_API_KEY
    if OPENAI_API_KEY:
        candidates.append({"provider": "openai", "model_name": "gpt-4o-mini", "api_key": OPENAI_API_KEY, "base_url": ""})
    if ANTHROPIC_API_KEY:
        candidates.append({"provider": "anthropic", "model_name": "claude-sonnet-4-6", "api_key": ANTHROPIC_API_KEY, "base_url": ""})

    for c in candidates:
        try:
            adapter = adapter_manager.get_adapter(c["provider"])
            result = await adapter.execute_prompt(
                prompt,
                model=(c.get("model_name") or ""),
                api_key=(c.get("api_key") or ""),
                base_url=(c.get("base_url") or ""),
            )
            if result and result.strip():
                name = result.strip()
                for prefix in ("标题：", "标题:", "Title：", "Title:"):
                    if name.startswith(prefix):
                        name = name[len(prefix):].strip()
                if len(name) >= 2 and name[0] == name[-1] and name[0] in ('"', "'", "「", "『"):
                    name = name[1:-1].strip()
                if 2 <= len(name) <= 50:
                    return name
        except Exception as exc:
            logger.debug("auto-name LLM candidate %s/%s failed: %s", c.get("provider"), c.get("model_name"), exc)
            continue

    return None


async def try_auto_name_session(session_id: str) -> str | None:
    """Non-blocking helper: generate and apply an auto-name if the session name is generic.
    Returns the new name if one was set, None otherwise.
    """
    try:
        session = await afetch_one("SELECT id, name FROM sessions WHERE id=$1", session_id)
        if not session:
            return None

        current_name = session.get("name") or ""
        if not is_generic_name(current_name):
            return None

        msgs = await list_messages(session_id)
        if not msgs or len(msgs) < 1:
            return None

        prompt = build_auto_name_prompt(msgs)
        if not prompt:
            # No prompt could be built — try local extraction directly
            user_msgs = [m for m in msgs if m.get("sender") not in ("system", "agent", "orchestrator")]
            first_msg = (user_msgs[0].get("content") or "").strip() if user_msgs else ""
            if first_msg:
                name = extract_local_title(first_msg)
                if name:
                    await aexecute("UPDATE sessions SET name=$1 WHERE id=$2", name, session_id)
                    return name
            return None

        name = await _call_llm_for_name(prompt)
        if not name:
            # LLM failed — use local keyword extraction from first user message
            user_msgs = [m for m in msgs if m.get("sender") not in ("system", "agent", "orchestrator")]
            first_msg = (user_msgs[0].get("content") or "").strip() if user_msgs else ""
            if first_msg:
                name = _extract_local_title(first_msg)
        if not name:
            return None

        await aexecute("UPDATE sessions SET name=$1 WHERE id=$2", name, session_id)
        return name
    except Exception:
        logger.debug("auto-name background task failed for %s", session_id, exc_info=True)
        return None