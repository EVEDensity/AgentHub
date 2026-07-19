from __future__ import annotations

import re
from typing import Any


def build_auto_name_prompt(messages: list[dict[str, Any]]) -> str:
    """Build a compact prompt for session auto-naming."""
    user_msgs = [m for m in messages if m.get("sender") not in ("system", "agent", "orchestrator")]
    agent_msgs = [m for m in messages if m.get("sender") in ("agent", "orchestrator", "system")]

    first_user = (user_msgs[0].get("content") or "").strip() if user_msgs else ""
    if not first_user:
        return ""
    if len(first_user) > 180:
        first_user = first_user[:180] + "..."

    first_reply = ""
    if agent_msgs:
        reply_content = (agent_msgs[0].get("content") or "").strip()
        if reply_content:
            if len(reply_content) > 120:
                reply_content = reply_content[:120] + "..."
            first_reply = f"\n助手回复：{reply_content}"

    return (
        "请根据首轮对话生成一个中文会话标题。\n"
        "要求：3-15字，概括核心意图，避免空泛词如“新建会话”“聊天”“对话”，不要加引号或编号。\n"
        f"用户首条消息：{first_user}"
        f"{first_reply}\n"
        "标题："
    )


def extract_local_title(first_message: str) -> str:
    """Fallback title extraction without an LLM."""
    text = first_message.strip()
    text = re.sub(r'@\w+\s*', '', text).strip()
    if not text:
        return ""

    patterns = [
        r'(?:生成|创建|编写|实现|开发|搭建)\s*(?:一个\s*)?(.{2,30}(?:文件|代码|页面|模块|功能|路由|接口|API|组件)?)$',
        r'(?:帮我|请帮我|麻烦)\s*(.{2,30}?)(?:谢谢|一下)?$',
        r'(?:如何|怎么|怎样)\s*(.{2,30}?)(?:\?|？)?$',
        r'(?:修复|修改|优化|调整|更新)\s*(.{2,30}?)$',
        r'(?:分析|审查|检查|review|analyze)\s*(.{2,30}?)$',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            keyword = m.group(1).strip().rstrip('。！？!?，,')
            if 2 <= len(keyword) <= 20:
                return keyword

    eng_patterns = [
        (r'[Gg]enerate\s+(?:a\s+)?(.{2,40}?)(?:\s+(?:file|route|code|page|module))?$', '生成'),
        (r'[Cc]reate\s+(?:a\s+)?(.{2,40}?)$', '创建'),
        (r'[Ff]ix\s+(?:the\s+)?(.{2,40}?)$', '修复'),
        (r'[Ii]mplement\s+(?:a\s+)?(.{2,40}?)$', '实现'),
        (r'[Cc]ode\s+(?:review|check)\s+(?:of\s+)?(.{2,40}?)$', '审查'),
    ]
    text_lower = text.lower()
    if "health route" in text_lower:
        return "生成健康检查路由"
    if "health check" in text_lower:
        return "生成健康检查"
    if "rest api" in text_lower:
        return "生成REST接口"
    translations = {
        'health route': '健康检查路由',
        'health check': '健康检查',
        'rest api': 'REST接口',
        'login': '登录功能',
        'auth': '认证功能',
        'database': '数据库',
        'config': '配置管理',
        'test': '测试用例',
        'component': '组件开发',
        'middleware': '中间件',
        'docker': 'Docker部署',
        'frontend': '前端页面',
        'backend': '后端服务',
        'pipeline': 'CI/CD流水线',
        'deploy': '部署流程',
        'health route': '健康检查路由',
        'api': 'API接口',
    }
    for pattern, prefix in eng_patterns:
        m = re.search(pattern, text)
        if m:
            keyword = m.group(1).strip().rstrip('.!?')
            keyword_lower = keyword.lower()
            if "health route" in keyword_lower:
                return f"{prefix}健康检查路由"
            if "health check" in keyword_lower:
                return f"{prefix}健康检查"
            if "rest api" in keyword_lower:
                return f"{prefix}REST接口"
            for eng, chn in translations.items():
                if eng in keyword_lower:
                    return f"{prefix}{chn}"
            title = f"{prefix}{keyword[:10]}"
            return title[:15]

    parts = re.split(r'[,，。！？\n!?]', text)
    for part in parts:
        part = part.strip()
        clean = re.sub(r'[^\w\u4e00-\u9fff]', '', part)
        if len(clean) >= 3:
            return part[:15] if len(part) > 15 else part

    return text[:15] if len(text) >= 3 else ""
