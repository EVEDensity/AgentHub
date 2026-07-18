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
    "orchestrator": "orchestrator",
    "architect": "architect",
    "codegen": "codegen",
    "review": "review",
    "test": "test",
    "deploy": "deploy",
}

_ESTIMATED_SECONDS = {"low": 20, "medium": 45, "high": 90}

_GREETING_PATTERNS = [
    "^(\u5927\u5bb6|\u5404\u4f4d|\u670b\u53cb\u4eec|\u4f19\u4f34\u4eec|\u540c\u5b66\u4eec|hello\\s*all|hi\\s*all|hey\\s*all|hello\\s*everyone|hi\\s*everyone|hey\\s*everyone)",
    "^(\u4f60\u597d|hi|hello|hey|\u518d\u89c1|\u8c22\u8c22|thanks?|thank\\s*you|3q|ok|\u597d\u7684|\u77e5\u9053\u4e86|\u660e\u767d)",
    "^(\u4eca\u5929|\u6700\u8fd1|how\\s*are\\s*you|what'?s?\\s*up|\u5e72\u5417\u5462|\u5728\u5417|\u6211\u6765\u4e86|\u6211\u56de\u6765\u4e86)",
    "^(\u4f60\u662f\u8c01|\u4f60\u7684\u540d\u5b57|\u4f60\u80fd\u505a\u4ec0\u4e48|\u4ecb\u7ecd\u4e00\u4e0b\u4f60\u81ea\u5df1)",
]

_TECH_KEYWORDS = [
    "\u5f00\u53d1",
    "\u5b9e\u73b0",
    "\u4fee\u6539",
    "\u4ee3\u7801",
    "\u751f\u6210",
    "\u521b\u5efa",
    "\u8bbe\u8ba1",
    "\u67b6\u6784",
    "\u90e8\u7f72",
    "\u53d1\u5e03",
    "\u4e0a\u7ebf",
    "\u6d4b\u8bd5",
    "\u5ba1\u67e5",
    "\u4fee\u590d",
    "bug",
    "\u9519\u8bef",
    "\u4f18\u5316",
    "\u91cd\u6784",
    "\u914d\u7f6e",
    "\u5b89\u88c5",
    "\u96c6\u6210",
    "\u8fc1\u79fb",
    "\u5347\u7ea7",
    "api",
    "\u63a5\u53e3",
    "\u9875\u9762",
    "\u7ec4\u4ef6",
    "\u6a21\u5757",
    "\u529f\u80fd",
    "\u7cfb\u7edf",
    "\u6570\u636e\u5e93",
    "\u524d\u7aef",
    "\u540e\u7aef",
    "\u5168\u6808",
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
    "\u5206\u6790",
    "\u68c0\u67e5",
    "\u6392\u67e5",
    "\u91cd\u6784",
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

    lowered = stripped.lower()
    for keyword in _TECH_KEYWORDS:
        if keyword in lowered:
            return False

    for pattern in _GREETING_PATTERNS:
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
                "label": "run tests",
                "intent": "approve",
                "description": "Run unit tests and integration tests before the next step.",
            }
        )
    if "review" in agent_domains or "review" in agent_ids:
        todo_items.append(
            {
                "id": "todo_fix",
                "label": "apply review notes",
                "intent": "approve",
                "description": "Review feedback exists; verify and continue refining the change.",
            }
        )
    if "deploy" in agent_domains or "deploy" in agent_ids:
        todo_items.append(
            {
                "id": "todo_verify_deploy",
                "label": "verify deployment",
                "intent": "approve",
                "description": "Check deployment health, logs, and key metrics.",
            }
        )

    todo_items.append(
        {
            "id": "todo_feedback",
            "label": "continue iterating",
            "intent": "approve",
            "description": "Provide feedback or start the next collaboration round.",
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
                "description": f"Execute task for {a['agent_id']}",
                "dependencies": [f"n{j}" for j in range(i)] if i > 0 else [],
            }
            for i, a in enumerate(target_agents)
        ],
        execution_strategy="sequential",
        analysis=f"Auto split into {len(target_agents)} nodes",
    )


async def _resolve_collaborative_dag(
    *,
    session_id: str,
    cleaned_content: str,
    target_agents: list[dict[str, Any]],
    route_dag: DAGConfig | None,
) -> DAGConfig:
    from app.services.task_decomposer import task_decomposer

    if route_dag is not None:
        logger.info(
            "ws using predefined route DAG session=%s nodes=%d",
            session_id,
            len(route_dag.nodes),
        )
        return route_dag

    try:
        return await task_decomposer.decompose(
            content=cleaned_content,
            session_id=session_id,
            agents=target_agents,
        )
    except Exception:
        logger.exception("ws task decomposition failed session=%s", session_id)
        return _build_fallback_dag(target_agents)


async def _prepare_task_preview(
    *,
    session_id: str,
    dag_config: DAGConfig,
    token,
) -> tuple[str, str, str]:
    ws_manager = _manager()
    preview_msg_id = str(uuid.uuid4())
    task_items = build_dag_task_items(list(dag_config.nodes))
    await ws_manager.broadcast_task_preview(
        session_id,
        preview_msg_id,
        task_items,
        eta_seconds=sum(item.get("estimatedSeconds", 45) for item in task_items),
    )

    if token.cancelled:
        return preview_msg_id, "cancel", ""

    decision, modifications = await ws_state.wait_for_task_confirmation(
        session_id,
        preview_msg_id,
        token,
    )
    return preview_msg_id, decision, modifications


async def _run_dag_execution(
    *,
    session_id: str,
    cleaned_content: str,
    target_agents: list[dict[str, Any]],
    dag_config: DAGConfig,
    user_id: str,
    token,
    attachments: list[dict],
    quote_references: list[dict] | None,
    invoke_agent: Callable[..., Awaitable[str]],
) -> dict[str, str]:
    from app.services.agent_service import CollaborationContext, lookup_agent
    from app.services.dag_executor import DAGExecutor

    ws_manager = _manager()
    collab = CollaborationContext(cleaned_content)
    for agent_row in target_agents:
        collab.register(agent_row)

    async def _dag_invoke(
        sid: str,
        agent_id: str,
        task_content: str,
        extra_context: str = "",
    ) -> str:
        agent_row = await lookup_agent(agent_id, user_id, columns="*")
        if not agent_row:
            return f"[error] Agent '{agent_id}' not found"

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
        await executor.execute(dag_config, collab)
    except Exception as exc:
        logger.warning("DAG execution error: %s", exc)

    return executor.node_results


async def _run_result_synthesis(
    *,
    session_id: str,
    dag_config: DAGConfig,
    node_results: dict[str, str],
    cleaned_content: str,
    target_agents: list[dict[str, Any]],
    user_id: str,
    token,
    attachments: list[dict],
    invoke_agent: Callable[..., Awaitable[str]],
) -> None:
    from app.services.agent_service import lookup_agent, save_message
    from app.services.result_synthesizer import result_synthesizer

    if token.cancelled:
        return

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
        ws_manager = _manager()
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
        ws_manager = _manager()
        await ws_manager.broadcast_agent_todo(
            session_id,
            str(uuid.uuid4()),
            "PM",
            "collaboration complete - next steps",
            f"Agents finished this round: {', '.join(a['agent_id'] for a in target_agents)}.",
            build_followup_todos(target_agents),
            priority="medium",
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
    dag_config = await _resolve_collaborative_dag(
        session_id=session_id,
        cleaned_content=cleaned_content,
        target_agents=target_agents,
        route_dag=route_dag,
    )

    _preview_msg_id, decision, modifications = await _prepare_task_preview(
        session_id=session_id,
        dag_config=dag_config,
        token=token,
    )
    if decision == "cancel":
        ws_manager = _manager()
        await ws_manager.broadcast(
            session_id,
            {
                "event": "message",
                "sessionId": session_id,
                "content": "collaboration task cancelled.",
                "sender": "system",
                "timestamp": now(),
                "type": "system",
            },
        )
        return
    if decision == "modify":
        await run_message_flow(
            session_id=session_id,
            content=f"[user modified the task plan]\n{modifications}",
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

    node_results = await _run_dag_execution(
        session_id=session_id,
        cleaned_content=cleaned_content,
        target_agents=target_agents,
        dag_config=dag_config,
        user_id=user_id,
        token=token,
        attachments=attachments,
        quote_references=quote_references,
        invoke_agent=invoke_agent,
    )

    await _run_result_synthesis(
        session_id=session_id,
        dag_config=dag_config,
        node_results=node_results,
        cleaned_content=cleaned_content,
        target_agents=target_agents,
        user_id=user_id,
        token=token,
        attachments=attachments,
        invoke_agent=invoke_agent,
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
