from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.api import websocket_state as ws_state
from app.db.init_db import now
from app.schemas.dag import DAGConfig

logger = logging.getLogger("agenthub.websocket")

_DOMAIN_LABELS = {
    "orchestrator": "协调调度",
    "architect": "架构设计",
    "codegen": "代码生成",
    "review": "代码审查",
    "test": "测试验证",
    "deploy": "部署发布",
}

_ESTIMATED_SECONDS = {"low": 20, "medium": 45, "high": 90}

_MULTI_MENTION_GREETING_PATTERNS = [
    r"^(大家|各位|朋友们|伙伴们|同学们|hello\s*all|hi\s*all|hey\s*all|hello\s*everyone|hi\s*everyone|hey\s*everyone)",
    r"^(你好|hi|hello|hey|再见|谢谢|thanks?|thank\s*you|3q|ok|好的|知道了|明白)",
    r"^(今天|最近|how\s*are\s*you|what'?s?\s*up|干嘛呢|在吗|我来了|我回来了)",
    r"^(你是谁|你的名字|你能做什么|介绍一下你自己)",
]

_MULTI_MENTION_TECH_KEYWORDS = [
    "开发",
    "实现",
    "修改",
    "代码",
    "生成",
    "创建",
    "设计",
    "架构",
    "部署",
    "发布",
    "上线",
    "测试",
    "审查",
    "修复",
    "bug",
    "错误",
    "优化",
    "重构",
    "配置",
    "安装",
    "集成",
    "迁移",
    "升级",
    "api",
    "接口",
    "页面",
    "组件",
    "模块",
    "功能",
    "系统",
    "数据库",
    "前端",
    "后端",
    "全栈",
    "react",
    "vue",
    "angular",
    "node",
    "python",
    "java",
    "go",
    "rust",
    "docker",
    "k8s",
    "ci/cd",
    "develop",
    "implement",
    "create",
    "build",
    "design",
    "deploy",
    "code",
    "function",
    "feature",
    "component",
    "module",
    "crud",
    "rest",
    "graphql",
    "sql",
    "nosql",
    "redis",
    "分析",
    "检查",
    "排查",
    "修复",
    "重构",
]


def _manager():
    from app.services.websocket_manager import manager as _manager_instance

    return _manager_instance


def _node_value(node: Any, key: str, default: Any = None) -> Any:
    if isinstance(node, dict):
        return node.get(key, default)
    return getattr(node, key, default)


def _strip_mentions(content: str, mentioned: list[str]) -> str:
    cleaned = content
    for name in mentioned:
        cleaned = re.sub(rf"@{re.escape(name)}", "", cleaned)
    return cleaned.strip()


def is_multi_mention_greeting(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True

    stripped_lower = stripped.lower()
    for kw in _MULTI_MENTION_TECH_KEYWORDS:
        if kw in stripped_lower:
            return False

    for pattern in _MULTI_MENTION_GREETING_PATTERNS:
        if re.match(pattern, stripped, re.IGNORECASE):
            return True

    return len(stripped) <= 15


def detect_multi_mention_greeting(
    content: str,
    mentioned: list[str],
    *,
    route_selected: bool,
) -> tuple[bool, str]:
    if route_selected or len(mentioned) < 2:
        return False, content

    cleaned = _strip_mentions(content, mentioned)
    if is_multi_mention_greeting(cleaned):
        return True, cleaned or content
    return False, content


def build_dag_task_items(dag_nodes: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for node in dag_nodes:
        domain = str(_node_value(node, "domain", ""))
        dependencies = _node_value(node, "dependencies", [])
        if not isinstance(dependencies, list):
            dependencies = list(dependencies) if dependencies else []
        items.append(
            {
                "id": str(_node_value(node, "id", "")),
                "description": (
                    f"{_node_value(node, 'agent', '')} "
                    f"({_DOMAIN_LABELS.get(domain, domain)}): "
                    f"{_node_value(node, 'description', '')}"
                ),
                "agent": str(_node_value(node, "agent", "")),
                "dependencies": dependencies,
                "estimatedSeconds": _ESTIMATED_SECONDS.get(
                    str(_node_value(node, "estimated_effort", "medium")),
                    45,
                ),
            }
        )
    return items


def build_followup_todos(target_agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    todo_items: list[dict[str, Any]] = []
    agent_domains = {str(a.get("domain", "")).lower() for a in target_agents}
    agent_ids = {str(a.get("agent_id", "")).lower() for a in target_agents}

    if "codegen" in agent_domains or "codegen" in agent_ids:
        todo_items.append(
            {
                "id": "todo_test",
                "label": "运行测试验证代码",
                "intent": "approve",
                "description": "建议先执行单元测试和集成测试，确认代码修改没有引入回归。",
            }
        )
    if "review" in agent_domains or "review" in agent_ids:
        todo_items.append(
            {
                "id": "todo_fix",
                "label": "按审查意见修正代码",
                "intent": "approve",
                "description": "Review Agent 已给出反馈，请核对并继续修正。",
            }
        )
    if "deploy" in agent_domains or "deploy" in agent_ids:
        todo_items.append(
            {
                "id": "todo_verify_deploy",
                "label": "验证部署结果",
                "intent": "approve",
                "description": "检查部署环境是否正常，重点关注日志和核心指标。",
            }
        )

    todo_items.append(
        {
            "id": "todo_feedback",
            "label": "提供反馈或继续迭代",
            "intent": "approve",
            "description": "如果结果还不够理想，可以补充反馈或开启下一轮协作。",
        }
    )
    return todo_items


def _build_fallback_dag(target_agents: list[dict[str, Any]]) -> DAGConfig:
    return DAGConfig(
        total=len(target_agents),
        completed=0,
        nodes=[
            {
                "id": f"n{i}",
                "domain": a.get("domain", "general"),
                "agent": a["agent_id"],
                "description": f"执行 {a['agent_id']} 的任务",
                "dependencies": [f"n{j}" for j in range(i)] if i > 0 else [],
            }
            for i, a in enumerate(target_agents)
        ],
        execution_strategy="sequential",
        analysis=f"自动拆解为 {len(target_agents)} 个节点",
    )


async def _broadcast_greeting(
    *,
    session_id: str,
    content: str,
    sender: str,
    user_id: str,
    token,
    attachments: list[dict],
    quote_references: list[dict] | None,
    target_agents: list[dict[str, Any]],
    invoke_agent: Callable[..., Awaitable[str]],
) -> None:
    async def _broadcast_to_agent(agent_row: dict[str, Any]) -> None:
        try:
            await invoke_agent(
                session_id,
                content,
                agent_row,
                user_id,
                token,
                attachments or [],
                sender_override=sender,
                quote_references=quote_references,
            )
        except Exception:
            logger.exception(
                "ws greeting broadcast agent failed session=%s agent=%s",
                session_id,
                agent_row.get("agent_id", "?"),
            )

    await asyncio.gather(
        *(_broadcast_to_agent(agent_row) for agent_row in target_agents),
        return_exceptions=True,
    )


async def _run_collaborative_flow(
    *,
    session_id: str,
    content: str,
    cleaned_content: str,
    sender: str,
    user_id: str,
    token,
    attachments: list[dict],
    quote_references: list[dict] | None,
    target_agents: list[dict[str, Any]],
    route_dag: DAGConfig | None,
    invoke_agent: Callable[..., Awaitable[str]],
) -> None:
    from app.services.agent_service import CollaborationContext, lookup_agent, save_message
    from app.services.dag_executor import DAGExecutor
    from app.services.result_synthesizer import result_synthesizer
    from app.services.task_decomposer import task_decomposer

    ws_manager = _manager()

    collab = CollaborationContext(cleaned_content)
    for agent_row in target_agents:
        collab.register(agent_row)

    if route_dag is not None:
        dag_config = route_dag
        logger.info(
            "ws using predefined route DAG session=%s nodes=%d",
            session_id,
            len(dag_config.nodes),
        )
    else:
        try:
            dag_config = await task_decomposer.decompose(
                content=cleaned_content,
                session_id=session_id,
                agents=target_agents,
            )
        except Exception:
            logger.exception("ws task decomposition failed session=%s", session_id)
            dag_config = _build_fallback_dag(target_agents)

    task_preview_msg_id = str(uuid.uuid4())
    task_items = build_dag_task_items(list(dag_config.nodes))
    await ws_manager.broadcast_task_preview(
        session_id,
        task_preview_msg_id,
        task_items,
        eta_seconds=sum(item.get("estimatedSeconds", 45) for item in task_items),
    )

    if token.cancelled:
        return

    decision, modifications = await ws_state.wait_for_task_confirmation(
        session_id,
        task_preview_msg_id,
        token,
    )
    if token.cancelled:
        return
    if decision == "cancel":
        await ws_manager.broadcast(
            session_id,
            {
                "event": "message",
                "sessionId": session_id,
                "content": "协作任务已取消。",
                "sender": "system",
                "timestamp": now(),
                "type": "system",
            },
        )
        return
    if decision == "modify":
        await run_message_flow(
            session_id=session_id,
            content=f"[用户修改后的任务计划]\n{modifications}",
            sender=sender,
            user_id=user_id,
            token=token,
            attachments=attachments,
            quote_references=quote_references,
            auto_reply=True,
            target_agents=target_agents,
            route_dag=route_dag,
            mentioned=[],
            invoke_agent=invoke_agent,
        )
        return

    async def _dag_invoke(
        sid: str,
        agent_id: str,
        task_content: str,
        extra_context: str = "",
    ) -> str:
        agent_row = await lookup_agent(agent_id, user_id, columns="*")
        if not agent_row:
            return f"[错误] Agent '{agent_id}' 未在注册表中找到"

        full = task_content
        if extra_context:
            full = f"{extra_context}\n\n{full}"

        result = await invoke_agent(
            sid,
            full,
            agent_row,
            user_id,
            token,
            attachments or [],
            collab_ctx="",
            quote_references=quote_references,
        )
        if result:
            collab.record(agent_id, agent_row.get("domain", ""), result)
        return result or ""

    executor = DAGExecutor(
        session_id=session_id,
        manager=ws_manager,
        invoke_fn=_dag_invoke,
        on_node_update=None,
    )

    try:
        node_results = await executor.execute(dag_config, collab)
    except Exception as exc:
        logger.warning("DAG execution error: %s", exc)
        node_results = executor.node_results

    if not token.cancelled:
        async def _synthesize_invoke(prompt: str) -> str | None:
            architect_row = await lookup_agent("Architect", user_id, columns="*")
            if not architect_row:
                return None
            return await invoke_agent(
                session_id,
                prompt,
                architect_row,
                user_id,
                token,
                attachments or [],
                collab_ctx="",
                quote_references=None,
            )

        final_response = await result_synthesizer.synthesize(
            dag=dag_config,
            node_results=node_results,
            original_request=cleaned_content,
            invoke_fn=_synthesize_invoke,
        )
        if final_response and not token.cancelled:
            await save_message(
                session_id,
                final_response,
                "Architect",
                "text",
                None,
                None,
                user_id=user_id or "",
            )
            await ws_manager.broadcast(
                session_id,
                {
                    "event": "message",
                    "sessionId": session_id,
                    "content": final_response,
                    "sender": "Architect",
                    "timestamp": now(),
                    "type": "text",
                    "userId": user_id or "",
                },
            )

    if not token.cancelled:
        await ws_manager.broadcast_agent_todo(
            session_id,
            str(uuid.uuid4()),
            "PM",
            "协作完成 - 建议后续步骤",
            f"以下 Agent 已完成本轮协作：{', '.join(a['agent_id'] for a in target_agents)}。",
            build_followup_todos(target_agents),
            priority="medium",
        )


async def _run_single_message_flow(
    *,
    session_id: str,
    content: str,
    sender: str,
    user_id: str,
    token,
    attachments: list[dict],
    quote_references: list[dict] | None,
    auto_reply: bool,
    target_agents: list[dict[str, Any]],
    invoke_agent: Callable[..., Awaitable[str]],
) -> None:
    from app.services.agent_service import get_direct_chat_agent

    agent = target_agents[0] if target_agents else None
    if agent is None:
        if auto_reply:
            logger.info("ws auto_reply mode session=%s user=%s", session_id, user_id)
            agent = await get_direct_chat_agent(user_id)
        else:
            logger.info(
                "ws no_agent mode session=%s user=%s (message saved, no agent invoked)",
                session_id,
                user_id,
            )
            return

    await invoke_agent(
        session_id,
        content,
        agent,
        user_id,
        token,
        attachments or [],
        sender_override=sender,
        quote_references=quote_references,
    )


async def run_message_flow(
    *,
    session_id: str,
    content: str,
    sender: str,
    user_id: str,
    token,
    attachments: list[dict],
    quote_references: list[dict] | None,
    auto_reply: bool,
    target_agents: list[dict[str, Any]],
    route_dag: DAGConfig | None,
    mentioned: list[str],
    invoke_agent: Callable[..., Awaitable[str]],
) -> None:
    is_greeting_broadcast, cleaned_content = detect_multi_mention_greeting(
        content,
        mentioned,
        route_selected=route_dag is not None,
    )

    if is_greeting_broadcast and len(target_agents) >= 2:
        await _broadcast_greeting(
            session_id=session_id,
            content=cleaned_content,
            sender=sender,
            user_id=user_id,
            token=token,
            attachments=attachments,
            quote_references=quote_references,
            target_agents=target_agents,
            invoke_agent=invoke_agent,
        )
        return

    if len(target_agents) >= 2:
        await _run_collaborative_flow(
            session_id=session_id,
            content=content,
            cleaned_content=cleaned_content,
            sender=sender,
            user_id=user_id,
            token=token,
            attachments=attachments,
            quote_references=quote_references,
            target_agents=target_agents,
            route_dag=route_dag,
            invoke_agent=invoke_agent,
        )
        return

    await _run_single_message_flow(
        session_id=session_id,
        content=cleaned_content,
        sender=sender,
        user_id=user_id,
        token=token,
        attachments=attachments,
        quote_references=quote_references,
        auto_reply=auto_reply,
        target_agents=target_agents,
        invoke_agent=invoke_agent,
    )
