"""WebSocket business processing lane (R4 split).

Holds the two large orchestrators (_process_and_stream, _invoke_agent) and
their memory/auto-name/deploy-card/broadcast helpers. The WebSocket endpoint
shell in websocket.py imports these; this module owns no transport state and
reads/writes session state exclusively through websocket_state.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import uuid
from typing import Any

from app.db.init_db import now
from app.db.session import afetch_all, afetch_one
from app.api import websocket_state as ws_state
from app.api import websocket_message_flow as message_flow
from app.schemas.dag import DAGConfig
from app.services.agent_service import (
    extract_mentions,
    get_direct_chat_agent,
    lookup_agent,
    save_message,
)
from app.services.guardrails import scan_input as _guardrails_scan
from app.services.guardrails import scan_output as _guardrails_scan_output
from app.services.message_router import route_message, stream_message
from app.services.websocket_manager import manager

logger = logging.getLogger("agenthub.websocket")

# ── lazy per-user memory singletons (owned by this lane) ─────────────
_memory_extractors: dict[str, object] = {}
_session_mgrs: dict[str, object] = {}
_session_stores: dict[str, object] = {}

async def _auto_name_and_broadcast(session_id: str) -> None:
    """Background task: generate an auto-name and broadcast it to connected clients."""
    try:
        from app.api.chat import is_generic_name, try_auto_name_session

        # Quick pre-check: skip if already has a meaningful name
        row = await afetch_one("SELECT name FROM sessions WHERE id=$1", session_id)
        if row and not is_generic_name(row.get("name") or ""):
            return  # already named

        new_name = await try_auto_name_session(session_id)
        if new_name:
            # Reset attempts: we found a good name, no need to keep trying
            ws_state._auto_name_state.pop(session_id, None)

            await manager.broadcast(
                session_id,
                {
                    "event": "session_renamed",
                    "sessionId": session_id,
                    "name": new_name,
                    "timestamp": now(),
                },
            )
    except Exception:
        logger.debug("auto-name background task failed for %s", session_id, exc_info=True)

def _get_memory_extractor(user_id: str = ""):
    """Return a per-user MemoryExtractor backed by the user's memory directory."""
    global _memory_extractors
    uid = user_id or "local-admin"
    if uid not in _memory_extractors:
        from app.config import MEMORY_DIR
        from app.services.memory import MemoryExtractor
        from app.services.memory.storage import MemoryStorage
        user_dir = MEMORY_DIR / "users" / uid
        _memory_extractors[uid] = MemoryExtractor(MemoryStorage(user_dir))
    return _memory_extractors[uid]

def _get_session_mgr(user_id: str = ""):
    """Return a per-user SessionMemoryManager backed by the user's memory directory."""
    global _session_mgrs
    uid = user_id or "local-admin"
    if uid not in _session_mgrs:
        from app.config import MEMORY_DIR
        from app.services.memory.session_memory import SessionMemoryManager
        from app.services.memory.storage import MemoryStorage
        user_dir = MEMORY_DIR / "users" / uid
        _session_mgrs[uid] = SessionMemoryManager(MemoryStorage(user_dir))
    return _session_mgrs[uid]

def _get_session_store(user_id: str = ""):
    """Return a per-user SessionMemoryStore backed by the user's memory directory."""
    global _session_stores
    uid = user_id or "local-admin"
    if uid not in _session_stores:
        from app.config import MEMORY_DIR
        from app.services.memory.session_store import SessionMemoryStore
        user_dir = MEMORY_DIR / "users" / uid
        _session_stores[uid] = SessionMemoryStore(user_dir)
    return _session_stores[uid]

async def _append_turn_to_session_memory(
    session_id: str,
    user_message: str,
    agent_response: str,
    user_id: str = "",
    sender: str = "",
    agent_name: str = "",
) -> None:
    """Append a conversation turn (user message + agent response) to the
    per-session memory file.  Fire-and-forget — errors are logged but never
    propagated, so memory persistence failures never block chat responses.
    """
    try:
        store = _get_session_store(user_id)
        turn_count = await store.append_turn(
            session_id=session_id,
            user_message=user_message,
            agent_response=agent_response,
            sender=sender or "user",
            agent_name=agent_name or "assistant",
        )
        if turn_count >= 10 and turn_count % 10 == 0:
            try:
                from app.services.memory_summary_consumer import memory_summary_consumer
                await memory_summary_consumer.request_compaction(
                    session_id, user_id or "local-admin",
                )
            except Exception:
                logger.debug("memory compaction request failed", exc_info=True)
        # Invalidate the memory context cache so the next agent call
        # picks up the updated session memory.
        try:
            from app.services.agent_service import _invalidate_memory_cache
            _invalidate_memory_cache()
        except Exception:
            pass
    except Exception:
        logger.debug(
            "append_turn_to_session_memory failed session=%s", session_id,
            exc_info=True,
        )

def _build_safety_block_message(result) -> str:
    """Build a human-readable safety block message from guardrail flags."""
    lines = [
        "🚫 **安全护栏检测 — 消息已被阻断**\n",
        "您的输入触发了以下安全红线，消息未被发送给 Agent：\n",
    ]
    for f in result.flags:
        cat_label = {
            "pii": "🔒 隐私信息泄露",
            "injection": "🛡️ 注入攻击检测",
            "harmful": "⚠️ 有害内容检测",
        }.get(f.category.value, f.category.value)
        lines.append(f"- {cat_label}：{f.message}")
    lines.append("\n---\n")
    lines.append("请移除敏感信息后重试。如有疑问，请联系系统管理员。")
    return "\n".join(lines)

async def _process_and_stream(
    session_id: str,
    content: str,
    sender: str,
    user_id: str,
    attachments: list[dict] | None = None,
    quote_references: list[dict] | None = None,
    auto_reply: bool = True,
) -> None:
    """Process one user message with the per-session lock held.

    @mentions always take priority. When no agent is @mentioned:
      - *auto_reply=True*   → the default chat agent responds automatically
      - *auto_reply=False*  → no agent is invoked (message is saved only)
    """
    # ── Activate per-user per-session workspace ──────────────────────────
    from app.services.workspace_context import set_workspace_context
    set_workspace_context(user_id=user_id, session_id=session_id)

    lock = manager.get_session_lock(session_id)
    async with lock:
        token = manager.create_token(session_id)

        try:
            await save_message(session_id, sender, content, "text", user_id=user_id)

            # ── Immediate ack: confirm message receipt to ALL clients ──
            # This lets other connected users see the message in real-time
            # without a page reload.  The sender's frontend already has the
            # message via handleSend() and will dedup by DB id.
            await manager.broadcast(
                session_id,
                {
                    "event": "message_ack",
                    "sessionId": session_id,
                    "timestamp": now(),
                },
            )

            # ── Safety guardrail scan (Tier 1: auto-block) ──────────
            guard_result = _guardrails_scan(content)
            if guard_result.blocked:
                block_msg = _build_safety_block_message(guard_result)
                await manager.broadcast(
                    session_id,
                    {
                        "event": "message",
                        "sessionId": session_id,
                        "content": block_msg,
                        "sender": "system",
                        "timestamp": now(),
                        "type": "system",
                        "guardrailResult": guard_result.to_dict(),
                    },
                )
                logger.warning(
                    "ws guardrail blocked session=%s flags=%s",
                    session_id,
                    [f"{f.rule}:{f.message[:60]}" for f in guard_result.flags],
                )
                return  # stop — don't route to agent

            # ── Skill invocation detection ──────────────────────────
            # Detect /skill-name patterns and inject SKILL.md body as
            # system prompt context before routing to agents.
            from app.services.agent_service import extract_skill_calls, load_skill_prompt

            skill_names = extract_skill_calls(content)
            skill_context = ""
            if skill_names:
                loaded_skills: list[str] = []
                for sn in skill_names:
                    body = await load_skill_prompt(sn)
                    if body:
                        loaded_skills.append(
                            f"## 技能：{sn}\n\n{body}"
                        )
                        logger.info(
                            "ws skill loaded session=%s skill=%s body_len=%d",
                            session_id, sn, len(body),
                        )
                    else:
                        logger.debug(
                            "ws skill not found session=%s skill=%s",
                            session_id, sn,
                        )
                if loaded_skills:
                    skill_context = (
                        "你正在执行以下技能指令。请严格遵循每个技能的定义、规则和输出格式。\n\n"
                        + "\n\n---\n\n".join(loaded_skills)
                        + "\n\n---\n\n## 用户消息\n\n"
                    )
                    # Prepend skill context to the user content
                    content = skill_context + content

            # ── #route: workflow detection ─────────────────────────
            # Detect explicit workflow route selection via #route:name
            # or #路线:name.  When a user selects a route, its
            # predefined agent nodes are used directly — no LLM-driven
            # task decomposition needed.
            route_dag: DAGConfig | None = None  # type: ignore[name-defined]
            from app.services.agent_route_service import agent_route_service

            matched_route, content = await agent_route_service.extract_route_ref(content, user_id)
            if matched_route and matched_route.get("nodes"):
                logger.info(
                    "ws #route matched session=%s route=%s nodes=%d",
                    session_id, matched_route["name"], len(matched_route["nodes"]),
                )
                # Build DAGConfig from the route's predefined nodes.
                route_dag = DAGConfig(
                    total=len(matched_route["nodes"]),
                    completed=0,
                    nodes=copy.deepcopy(matched_route["nodes"]),
                    execution_strategy="sequential",
                    analysis=f"Route: {matched_route['name']} — {matched_route.get('description', '')}",
                )

            # ── Extract @mentions — only if no route was selected ──
            # When a #route is explicitly chosen, the route's agent
            # nodes define the workflow.  @mentions in the same message
            # are treated as conversational references, not routing
            # directives.
            mentioned: list[str] = []
            if route_dag is not None:
                # Build target_agents from route nodes
                target_agents: list[dict] = []
                route_seen: set[str] = set()
                for node in route_dag.nodes:
                    agent_name = node.agent if hasattr(node, 'agent') else node.get("agent", "")
                    if not agent_name or agent_name in route_seen:
                        continue
                    route_seen.add(agent_name)
                    row = await lookup_agent(agent_name, user_id)
                    if row:
                        target_agents.append(row)
                    else:
                        logger.warning(
                            "ws #route agent not found session=%s agent=%s",
                            session_id, agent_name,
                        )
                if len(target_agents) < 2:
                    # Route has only 1 valid agent — fall through to
                    # single-agent path (no DAG preview needed).
                    route_dag = None
            else:
                mentioned = extract_mentions(content)
                target_agents: list[dict] = []
                seen: set[str] = set()
                for name in mentioned:
                    if name in seen:
                        continue
                    seen.add(name)
                    row = await lookup_agent(name, user_id)
                    if row:
                        target_agents.append(row)

            # ── Guard: parallel broadcast for multi-@mention greetings ──
            # When a user @mentions multiple agents but the actual message
            # is just a greeting (e.g. "@A @B 大家好🤩" or
            # "@A @B 介绍一下自己"), skip the full DAG task-plan preview
            # (which would be jarring UX for a simple greeting) BUT still
            # fan out to every mentioned agent in parallel so each one can
            # respond independently.  No task preview, no confirmation
            # wait, no result synthesis — each agent replies on its own.
            # NOTE: This guard only applies to @mention-triggered DAGs.
            # When the user explicitly selects a route via #route:name,
            # the greeting guard is skipped — they clearly intended the
            # workflow to run.
            await message_flow.run_message_flow(
                session_id=session_id,
                content=content,
                sender=sender,
                user_id=user_id,
                token=token,
                attachments=attachments or [],
                quote_references=quote_references,
                auto_reply=auto_reply,
                target_agents=target_agents,
                route_dag=route_dag,
                mentioned=mentioned,
                invoke_agent=_invoke_agent,
            )
            return

            is_greeting_broadcast = False
            if route_dag is None and len(target_agents) >= 2:
                # Strip @mentions to inspect the real message content
                msg_without_mentions = content
                for name in mentioned:
                    msg_without_mentions = re.sub(
                        rf'@{re.escape(name)}', '', msg_without_mentions
                    )
                msg_without_mentions = msg_without_mentions.strip()

                # Check if the remaining text is a greeting / non-task message
                if _is_multi_mention_greeting(msg_without_mentions):
                    logger.info(
                        "ws multi-@mention greeting detected, broadcasting to "
                        "all mentioned agents in parallel. mentioned=%s stripped=%r",
                        mentioned, msg_without_mentions,
                    )
                    is_greeting_broadcast = True
                    # Remove @mentions from content so each agent sees a clean
                    # message (e.g. "大家好，介绍一下自己") instead of a string
                    # of bare @names.
                    content = msg_without_mentions if msg_without_mentions else content

            # ── Parallel broadcast for multi-@mention greetings ──────────
            # When the user @mentions multiple agents with a greeting-level
            # message (e.g. "大家好，介绍一下自己"), fan the same message
            # out to ALL mentioned agents concurrently.  Each agent replies
            # independently — no DAG, no task preview, no confirmation
            # wait, no result synthesis.  The user sees every agent's
            # self-introduction as a separate message.
            if is_greeting_broadcast and len(target_agents) >= 2:

                async def _broadcast_to_agent(agent_row: dict):
                    try:
                        await _invoke_agent(
                            session_id, content, agent_row, user_id, token,
                            attachments or [],
                            sender_override=sender,
                            quote_references=quote_references,
                        )
                    except Exception:
                        logger.exception(
                            "ws greeting broadcast agent failed session=%s agent=%s",
                            session_id, agent_row.get("agent_id", "?"),
                        )

                # Fire all agents concurrently — each one saves and
                # broadcasts its own response independently.
                await asyncio.gather(
                    *(_broadcast_to_agent(a) for a in target_agents),
                    return_exceptions=True,
                )
                return  # done — don't fall through to single-agent path

            # ── Multi-agent path (Architect-driven DAG execution) ──────
            if len(target_agents) >= 2:
                from app.services.agent_service import CollaborationContext
                from app.services.task_decomposer import task_decomposer
                from app.services.dag_executor import DAGExecutor
                from app.services.result_synthesizer import result_synthesizer

                collab = CollaborationContext(content)
                for a in target_agents:
                    collab.register(a)

                # ── Step 1: Build DAG ────────────────────────────────────
                # When a #route explicitly selects a workflow, use its
                # predefined nodes directly — no LLM decomposition.
                # Otherwise, use the Architect LLM to decompose.
                if route_dag is not None:
                    dag_config = route_dag
                    logger.info(
                        "ws using predefined route DAG session=%s nodes=%d",
                        session_id, len(dag_config.nodes),
                    )
                else:
                    try:
                        dag_config = await task_decomposer.decompose(
                            content=content,
                            session_id=session_id,
                            agents=target_agents,
                        )
                    except Exception:
                        # Last-resort fallback
                        dag_config = DAGConfig(
                            total=len(target_agents),
                            completed=0,
                            nodes=[{
                                "id": f"n{i}", "domain": a.get("domain", "general"),
                                "agent": a["agent_id"],
                                "description": f"执行 {a['agent_id']} 的任务",
                                "dependencies": [f"n{j}" for j in range(i)] if i > 0 else [],
                            } for i, a in enumerate(target_agents)],
                            execution_strategy="sequential",
                            analysis=f"自动分解为 {len(target_agents)} 个节点",
                        )

                # ── 🟡 Trigger 1: Task Preview with real DAG ────────
                task_preview_msg_id = str(uuid.uuid4())
                task_items = message_flow.build_dag_task_items(list(dag_config.nodes))
                await manager.broadcast_task_preview(
                    session_id, task_preview_msg_id, task_items,
                    eta_seconds=sum(t.get("estimatedSeconds", 45) for t in task_items),
                )

                # ── Wait for user confirmation before executing DAG ──
                if not token.cancelled:
                    multi_decision, multi_modifications = await ws_state.wait_for_task_confirmation(
                        session_id, task_preview_msg_id, token,
                    )
                    if token.cancelled:
                        return
                    if multi_decision == "cancel":
                        await manager.broadcast(session_id, {
                            "event": "message", "sessionId": session_id,
                            "content": "❌ 协作任务已取消",
                            "sender": "system", "timestamp": now(), "type": "system",
                        })
                        return
                    if multi_decision == "modify":
                        # Re-process with user modifications
                        modified_content = f"[用户修改了任务计划]\n{multi_modifications}"
                        await _process_and_stream(
                            session_id, modified_content, sender, user_id,
                            attachments, quote_references, auto_reply,
                        )
                        return
                    # "confirm" or "timeout" → proceed

                # ── Step 2: Execute DAG with real agent invocations ──
                async def _dag_invoke(sid, agent_id, content, extra_context=""):
                    agent_row = await lookup_agent(agent_id, user_id, columns="*")
                    if not agent_row:
                        return f"[错误] Agent '{agent_id}' 未在注册表中找到"
                    collab_ctx = collab.context_for(agent_id) if collab else ""
                    full = f"{collab_ctx}\n\n{content}"
                    if extra_context:
                        full = f"{extra_context}\n\n{full}"
                    result = await _invoke_agent(
                        sid, full, agent_row, user_id, token,
                        attachments or [],
                        collab_ctx=collab_ctx,
                        quote_references=quote_references,
                    )
                    if result:
                        collab.record(agent_id, agent_row.get("domain", ""), result)
                    return result or ""

                executor = DAGExecutor(
                    session_id=session_id,
                    manager=manager,
                    invoke_fn=_dag_invoke,
                    on_node_update=None,
                )

                try:
                    node_results = await executor.execute(dag_config, collab)
                except Exception as exc:
                    logger.warning("DAG execution error: %s", exc)

                # ── Step 3: Synthesize results ────────────────────────
                if not token.cancelled:
                    async def _synthesize_invoke(prompt):
                        architect_row = await lookup_agent("Architect", user_id, columns="*")
                        if not architect_row:
                            return None
                        return await _invoke_agent(
                            session_id, prompt, architect_row, user_id, token,
                            attachments or [],
                            collab_ctx="",
                            quote_references=None,
                        )

                    final_response = await result_synthesizer.synthesize(
                        dag=dag_config,
                        node_results=executor.node_results,
                        original_request=content,
                        invoke_fn=_synthesize_invoke,
                    )

                    if final_response and not token.cancelled:
                        # Save final synthesized message — attach the user who
                        # triggered this DAG so the frontend can show the
                        # owner label "X 的 Architect" on the message.
                        await save_message(
                            session_id, final_response, "Architect",
                            "text", None, None,
                            user_id=user_id or "",
                        )
                        await manager.broadcast(
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

                # ── 🟡 Trigger 5: Agent Todo ────────────────────────────
                # After all agents complete, broadcast a follow-up todo
                # list suggesting next steps based on which agents ran.
                if not token.cancelled:
                    todo_items = []
                    agent_domains = {a.get("domain", "") for a in target_agents}
                    if "codegen" in agent_domains or "CodeGen" in {a["agent_id"] for a in target_agents}:
                        todo_items.append({
                            "id": "todo_test",
                            "label": "运行测试验证生成的代码",
                            "intent": "approve",
                            "description": "建议运行单元测试和集成测试确保代码正确性",
                        })
                    if "review" in agent_domains or "Review" in {a["agent_id"] for a in target_agents}:
                        todo_items.append({
                            "id": "todo_fix",
                            "label": "根据审查意见修改代码",
                            "intent": "approve",
                            "description": "Review Agent 已提出修改建议，请检查并应用",
                        })
                    if "deploy" in agent_domains or "Deploy" in {a["agent_id"] for a in target_agents}:
                        todo_items.append({
                            "id": "todo_verify_deploy",
                            "label": "验证部署结果",
                            "intent": "approve",
                            "description": "检查生产环境是否正常运行，监控日志和指标",
                        })
                    # Always include a generic follow-up
                    todo_items.append({
                        "id": "todo_feedback",
                        "label": "提供反馈或继续迭代",
                        "intent": "approve",
                        "description": "如果结果不满意可以提出修改意见，或开启新一轮协作",
                    })
                    await manager.broadcast_agent_todo(
                        session_id,
                        str(uuid.uuid4()),
                        "PM",
                        "协作完成 — 建议后续步骤",
                        f"以下 Agent 已完成本轮协作：{', '.join(a['agent_id'] for a in target_agents)}。建议检查结果并继续推进：",
                        todo_items,
                        priority="medium",
                    )

                return

            # ── Resolve target agent ──────────────────────────────────
            # Behavior matrix:
            #   ┌──────────────────────────┬──────────────────────────────────┐
            #   │ Scenario                 │ Behavior                         │
            #   ├──────────────────────────┼──────────────────────────────────┤
            #   │ @A @B @C do X            │ DAG task preview → wait for      │
            #   │ (multi-agent, handled    │ confirm → execute (user sees     │
            #   │  earlier at line ~836)   │ plan and approves it)            │
            #   ├──────────────────────────┼──────────────────────────────────┤
            #   │ @Agent do X              │ Direct execution, no preview,    │
            #   │                          │ no wait (user chose the agent)   │
            #   ├──────────────────────────┼──────────────────────────────────┤
            #   │ do X + autoReply ON      │ Direct execution, no preview,    │
            #   │ (default chat)           │ no wait — immediate streaming    │
            #   ├──────────────────────────┼──────────────────────────────────┤
            #   │ do X + autoReply OFF     │ Save message only, no agent call │
            #   └──────────────────────────┴──────────────────────────────────┘
            is_direct_mention = bool(target_agents)  # user explicitly @mentioned an agent
            if target_agents:
                # Single @mentioned agent → use it directly, no preview
                agent = target_agents[0]
            elif auto_reply:
                # No @mention + auto-reply ON → fall back to default chat agent
                logger.info(
                    "ws auto_reply mode session=%s user=%s",
                    session_id, user_id,
                )
                agent = await get_direct_chat_agent(user_id)
            else:
                # No @mention + auto-reply OFF → message saved only, no agent
                logger.info(
                    "ws no_agent mode session=%s user=%s (message saved, no agent invoked)",
                    session_id, user_id,
                )
                return

            # ── Default chat path: invoke agent directly ──────────────────
            # No task preview, no confirmation wait — the user expects
            # an immediate response, not a multi-step approval workflow.
            # Task previews are reserved exclusively for multi-agent
            # (@A @B) DAG workflows where the user needs to review a
            # complex plan before execution.

            await _invoke_agent(
                session_id, content, agent, user_id, token, attachments or [],
                sender_override=sender,
                quote_references=quote_references,
            )

            # ── Agent Todo: only for multi-agent workflows ────────────
            # Single-agent responses don't need a follow-up todo list;
            # the user can naturally continue the conversation.  The
            # multi-agent DAG path (above) still broadcasts task todos
            # where they add real value.

        except Exception:
            logger.exception("ws _process_and_stream failed session=%s", session_id)
            if not token.cancelled:
                # Include the actual error so the user can see what went wrong,
                # rather than a generic "模型调用失败" that hides the root cause.
                import traceback as _tb
                error_detail = _tb.format_exc()
                # Only show the last 3 lines (the actual exception) — not the full traceback
                error_lines = [L for L in error_detail.strip().split('\n') if L.strip()]
                short_error = '\n'.join(error_lines[-4:]) if len(error_lines) > 4 else '\n'.join(error_lines)
                await manager.broadcast(
                    session_id,
                    {
                        "event": "message",
                        "sessionId": session_id,
                        "content": f"模型调用失败：\n{short_error}",
                        "sender": "system",
                        "timestamp": now(),
                        "type": "system",
                    },
                )
        finally:
            manager.remove_token(session_id, token)

    # ── Auto memory tasks (background, non-blocking, throttled) ───
    # Both extraction and summarization fire in background after a message.
    # Throttled: only run once every _THROTTLE_SECONDS per session to avoid
    # firing expensive LLM calls on every single message.
    if ws_state.should_run_memory_tasks(session_id):
        try:
            from app.config import AUTO_MEMORY_ENABLED
            if AUTO_MEMORY_ENABLED:
                extractor = _get_memory_extractor(user_id)
                asyncio.create_task(extractor.extract_from_session(session_id))
        except Exception:
            logger.debug("auto-memory extraction init failed", exc_info=True)

        try:
            session_mgr = _get_session_mgr(user_id)
            asyncio.create_task(session_mgr.update_session_summary(session_id))
        except Exception:
            logger.debug("session-memory update init failed", exc_info=True)

    # ── Auto session naming (background, non-blocking) ────────
    # Fire after every message for sessions with generic names.
    # Runs on its own throttle separate from memory tasks.
    if ws_state._should_auto_name(session_id):
        asyncio.create_task(_auto_name_and_broadcast(session_id))

async def _invoke_agent(
    session_id: str,
    content: str,
    agent: dict | None,
    user_id: str,
    token,
    attachments: list[dict],
    collab_ctx: str = "",
    sender_override: str | None = None,
    quote_references: list[dict] | None = None,
) -> str:
    """Invoke a single agent — streaming first, non-streaming fallback.

    Returns the agent's full response text (for collaboration context recording).
    """
    agent_id = agent["agent_id"] if agent else (sender_override or "Orchestrator")

    # ── Orchestrator pre-processing: analyze and restructure the user's
    #     question before it reaches the main LLM.  Uses a lightweight LLM
    #     call to produce intent analysis, sub-task decomposition, and a
    #     clarified question — the main LLM receives a well-structured
    #     prompt instead of raw user input.
    #
    #     For simple greetings / short non-technical messages, the preprocessor
    #     returns a synthetic "simple" result (no LLM call).  We thread
    #     ``simple_mode=True`` through the pipeline so ``build_prompt()`` can
    #     emit a minimal prompt — no tools, no workflow, no decomposition.
    #     This prevents the LLM from calling web_search / skill_list / etc.
    #     chaotically for trivial messages like "你好".
    preprocess_context = ""
    simple_mode = False
    if agent_id == "Orchestrator":
        try:
            from app.services.orchestrator_preprocessor import orchestrator_preprocessor
            preprocess_result = await orchestrator_preprocessor.process(
                content=content,
                agent=agent,
                user_id=user_id,
            )
            if preprocess_result:
                if preprocess_result.get("is_simple"):
                    # Signal to build_prompt: use minimal prompt, no tools
                    if preprocess_result.get("_no_tools"):
                        simple_mode = True
                        logger.info(
                            "ws orchestrator preprocessor: simple_mode enabled intent=%s session=%s",
                            preprocess_result.get("intent_type", "?"),
                            session_id,
                        )
                else:
                    preprocess_context = orchestrator_preprocessor.format_for_prompt(
                        preprocess_result
                    )
                    if preprocess_context:
                        logger.info(
                            "ws orchestrator preprocessor: intent=%s sub_tasks=%d session=%s",
                            preprocess_result.get("intent_type", "?"),
                            len(preprocess_result.get("sub_tasks", [])),
                            session_id,
                        )
        except Exception:
            logger.debug("ws orchestrator preprocessor failed", exc_info=True)

    # ── Solution proposal handling ─────────────────────────────────────
    # When the preprocessor identifies multiple solution approaches, we
    # broadcast them to the frontend and let the user choose (or auto-
    # confirm after a timeout).  The selected solution's tech stack is
    # then injected into the DAG nodes so downstream agents know exactly
    # what to use.
    solution_context: dict[str, Any] | None = None
    if agent_id == "Orchestrator" and preprocess_result and not preprocess_result.get("is_simple"):
        solutions = preprocess_result.get("solutions", [])
        if solutions and len(solutions) >= 2:
            _solution_msg_id = str(uuid.uuid4())
            _auto_confirm_sec = 15

            # Normalize solution dicts for the frontend
            _frontend_solutions = []
            for s in solutions:
                _frontend_solutions.append({
                    "id": s.get("id", ""),
                    "name": s.get("name", ""),
                    "techStack": s.get("tech_stack", []),
                    "architecture": s.get("architecture", ""),
                    "pros": s.get("pros", []),
                    "cons": s.get("cons", []),
                    "estimatedEffort": s.get("estimated_effort", ""),
                    "riskLevel": s.get("risk_level", "medium"),
                    "score": s.get("score", 80),
                })

            recommended_id = preprocess_result.get("recommended_solution_id", "")
            recommendation_reason = preprocess_result.get("recommendation_reason", "")

            logger.info(
                "ws orchestrator: broadcasting solution_proposal session=%s solutions=%d recommended=%s",
                session_id, len(_frontend_solutions), recommended_id,
            )

            # ── Set up async wait for user selection ──────────────────
            _sel_event = asyncio.Event()
            ws_state._solution_selection_events[session_id] = _sel_event
            ws_state._solution_selection_results.pop(session_id, None)

            await manager.broadcast_solution_proposal(
                session_id=session_id,
                message_id=_solution_msg_id,
                intent_type=preprocess_result.get("intent_type", "technical_development"),
                requirements=preprocess_result.get("requirements", []),
                non_functional_requirements=preprocess_result.get("non_functional_requirements", []),
                constraints=preprocess_result.get("constraints", []),
                solutions=_frontend_solutions,
                recommended_solution_id=recommended_id,
                recommendation_reason=recommendation_reason,
                auto_confirm_seconds=_auto_confirm_sec,
            )

            # Wait for user selection or auto-confirm timeout
            try:
                await asyncio.wait_for(_sel_event.wait(), timeout=_auto_confirm_sec)
                _selection = ws_state._solution_selection_results.get(session_id, {})
                logger.info(
                    "ws orchestrator: solution selected session=%s solution=%s",
                    session_id, _selection.get("solutionId", "?"),
                )
            except asyncio.TimeoutError:
                # Auto-confirm the recommended solution
                _selection = {"solutionId": recommended_id, "autoSelected": True}
                logger.info(
                    "ws orchestrator: solution auto-confirmed session=%s solution=%s",
                    session_id, recommended_id,
                )
            finally:
                ws_state._solution_selection_events.pop(session_id, None)
                ws_state._solution_selection_results.pop(session_id, None)

            # Resolve the selected solution to its full context
            selected_id = _selection.get("solutionId", recommended_id)
            for s in solutions:
                if s.get("id") == selected_id:
                    solution_context = s
                    break
            if not solution_context and solutions:
                # Fallback to recommended if selection not found
                for s in solutions:
                    if s.get("id") == recommended_id:
                        solution_context = s
                        break
                if not solution_context:
                    solution_context = solutions[0]

            # Store selection in preprocess_result for format_for_prompt
            preprocess_result["_selected_solution"] = solution_context

    # ── DAG-based execution path ──────────────────────────────────────
    # When the Orchestrator preprocessor produces a sub-task decomposition
    # with routing, execute the tasks directly via DAGExecutor instead of
    # relying on the LLM to serialize invoke_agent calls through the
    # tool-call loop.  This gives us:
    #   • True parallelism for independent nodes
    #   • Real-time node status broadcasts to the frontend
    #   • Dependency-aware execution order
    #   • Built-in retry and fallback chains
    #
    # Falls through to the standard prompt-based path when:
    #   - preprocess_result has no sub_tasks (simple/greeting message)
    #   - DAG construction fails (only 1 node, etc.)
    #   - Any node lookup fails (agent not found)
    if agent_id == "Orchestrator" and preprocess_result and not preprocess_result.get("is_simple"):
        try:
            dag_config = orchestrator_preprocessor.build_dag_from_preprocess(
                preprocess_result, content, solution_context=solution_context,
            )
            if dag_config is not None and len(dag_config.nodes) >= 2:
                # Look up all agents referenced in the DAG
                dag_agents: dict[str, dict] = {}
                dag_agent_missing = False
                for node in dag_config.nodes:
                    if node.agent not in dag_agents:
                        row = await lookup_agent(node.agent, user_id)
                        if row:
                            dag_agents[node.agent] = row
                        else:
                            logger.warning(
                                "ws orchestrator DAG: agent not found agent=%s — "
                                "falling through to prompt-based path", node.agent,
                            )
                            dag_agent_missing = True
                            break

                if not dag_agent_missing:
                    logger.info(
                        "ws orchestrator DAG: executing %d nodes strategy=%s session=%s",
                        len(dag_config.nodes), dag_config.execution_strategy, session_id,
                    )

                    # ── Broadcast task preview ──────────────────────────
                    _dag_preview_id = str(uuid.uuid4())
                    _dag_task_items = message_flow.build_dag_task_items(list(dag_config.nodes))
                    await manager.broadcast_task_preview(
                        session_id, _dag_preview_id, _dag_task_items,
                        eta_seconds=sum(t.get("estimatedSeconds", 45) for t in _dag_task_items),
                    )

                    # ── Execute DAG ─────────────────────────────────────
                    from app.services.dag_executor import DAGExecutor

                    async def _dag_invoke(sid, agent_name, task_content, extra_context=""):
                        agent_row = dag_agents.get(agent_name)
                        if not agent_row:
                            return f"[错误] Agent '{agent_name}' 未在注册表中找到"
                        full = task_content
                        if extra_context:
                            full = f"{extra_context}\n\n{full}"
                        result = await _invoke_agent(
                            sid, full, agent_row, user_id, token,
                            attachments or [],
                            collab_ctx="",
                            quote_references=quote_references,
                        )
                        return result or ""

                    dag_exec = DAGExecutor(
                        session_id=session_id,
                        manager=manager,
                        invoke_fn=_dag_invoke,
                        on_node_update=None,
                    )

                    try:
                        node_results = await dag_exec.execute(dag_config)
                    except Exception as dag_exc:
                        logger.warning("ws orchestrator DAG execution failed: %s", dag_exc)

                    # ── Synthesize results ──────────────────────────────
                    if not token.cancelled:
                        synthesis_prompt = (
                            f"你收到了以下子任务执行结果的汇总。请综合各 Agent 的输出，"
                            f"生成一个连贯的最终回复给用户。\n\n"
                            f"## 用户原始问题\n{content}\n\n"
                            f"## 预处理分析\n{preprocess_context}\n\n"
                            f"## 子任务执行结果\n\n"
                        )
                        for node_id, node_result in node_results.items():
                            node = next((n for n in dag_config.nodes if n.id == node_id), None)
                            agent_label = node.agent if node else node_id
                            synthesis_prompt += (
                                f"### {agent_label} ({node_id})\n{node_result[:3000]}\n\n"
                            )
                        synthesis_prompt += (
                            "\n请综合以上结果，给出完整、连贯的最终回复。"
                            "标注每个关键结论的来源 Agent。"
                        )

                        # Use a lightweight agent for synthesis — the
                        # Orchestrator itself (without tools) is perfect
                        synth_agent = agent if agent else await lookup_agent("Orchestrator", user_id)
                        if synth_agent:
                            synth_result = await _invoke_agent(
                                session_id, synthesis_prompt, synth_agent, user_id,
                                token, attachments or [],
                                collab_ctx="",
                                quote_references=quote_references,
                            )
                            if synth_result:
                                return synth_result

                    # If synthesis failed, return a raw summary
                    raw_summary_parts = ["## 📋 任务执行结果\n"]
                    for node_id, node_result in node_results.items():
                        node = next((n for n in dag_config.nodes if n.id == node_id), None)
                        agent_label = node.agent if node else node_id
                        raw_summary_parts.append(
                            f"### {agent_label}\n{node_result[:2000]}\n"
                        )
                    summary_text = "\n".join(raw_summary_parts)
                    # Broadcast as a message
                    summary_msg_id = str(uuid.uuid4())
                    await manager.stream_broadcast(
                        session_id, summary_msg_id, summary_text, is_final=True,
                        sender=agent_id,
                    )
                    return summary_text

        except Exception as dag_setup_exc:
            logger.warning(
                "ws orchestrator DAG setup failed — falling through: %s", dag_setup_exc,
            )

    # ── Broadcast "agent_thinking" so the frontend shows a streaming
    #     indicator immediately, even during the tool-call loop phase
    #     where no message_chunks are emitted yet. This prevents the UI
    #     from appearing frozen while the agent reasons + calls tools.
    thinking_msg_id = str(uuid.uuid4())
    await manager.broadcast(
        session_id,
        {
            "event": "agent_thinking",
            "sessionId": session_id,
            "messageId": thinking_msg_id,
            "agentId": agent_id,
            "phase": "analyzing",
            "details": "正在分析需求，判断是否需要调用工具...",
            "timestamp": now(),
        },
    )

    # ── Tool event callback for WebSocket broadcast ─────────────────
    msg_id_for_tools = str(uuid.uuid4())
    thinking_sent_iterations = 0  # track tool loop iterations for progress

    async def _on_tool_event(status: str, tool_calls: list[dict], tool_results: list[dict] | None) -> None:
        """Broadcast tool call/result events and progress updates to the frontend."""
        nonlocal thinking_sent_iterations
        # ★ 改进3: "agent_thinking" status — 工具循环进度事件
        # When status is "agent_thinking", tool_calls is actually a dict payload
        # with messageId, agentId, phase, details fields.
        if status == "agent_thinking" and isinstance(tool_calls, dict):
            await manager.broadcast(session_id, {
                "event": "agent_thinking",
                "sessionId": session_id,
                **tool_calls,
            })
            return
        if status == "calling":
            # Send tool_call event so the frontend can render tool-call bubbles
            tool_names = [tc.get("name", "unknown") for tc in tool_calls]
            await manager.broadcast(
                session_id,
                {
                    "event": "tool_call",
                    "sessionId": session_id,
                    "messageId": msg_id_for_tools,
                    "toolCalls": [
                        {"name": tc.get("name", ""), "arguments": tc.get("arguments", {}), "status": "calling"}
                        for tc in tool_calls
                    ],
                    "timestamp": now(),
                },
            )
            # ── Stream progress text so the user sees tool activity in the chat ──
            tool_list = '、'.join(tool_names)
            await manager.stream_broadcast(
                session_id, msg_id_for_tools,
                f"\n\n🔧 正在调用工具：{tool_list} ...\n",
                is_final=False, sender=agent_id,
            )
            # Also update the thinking indicator to show tool execution phase
            thinking_sent_iterations += 1
            await manager.broadcast(
                session_id,
                {
                    "event": "agent_thinking",
                    "sessionId": session_id,
                    "messageId": thinking_msg_id,
                    "agentId": agent_id,
                    "phase": "executing",
                    "details": f"正在调用工具: {', '.join(tool_names)} (第{thinking_sent_iterations}轮)",
                    "timestamp": now(),
                },
            )

            # ── 🟡 Trigger 4: Risk Warning ──────────────────────────
            # When the tool-call loop detects high-risk operations, broadcast
            # a risk_warning event so the PM proactively alerts the user.
            # tool_calls here may contain risk-classified entries with
            # {"name", "arguments", "risk": {...}} from classify_tool_risk.
            for tc in tool_calls:
                risk = tc.get("risk")
                if risk and risk.get("flags"):
                    risk_flags = risk.get("flags", [])
                    risk_level = "critical" if any(
                        f.get("severity") == "block" for f in risk_flags
                    ) else "high" if any(
                        f.get("severity") == "confirm" for f in risk_flags
                    ) else "medium"
                    risk_msgs = [f.get("message", "") for f in risk_flags if f.get("message")]
                    await manager.broadcast_risk_warning(
                        session_id,
                        str(uuid.uuid4()),
                        agent_id,
                        risk_level,
                        f"高风险操作: {tc.get('name', 'unknown')}",
                        "; ".join(risk_msgs) or f"工具 {tc.get('name', 'unknown')} 需要确认",
                        [
                            {"id": "continue", "label": "继续执行", "intent": "continue"},
                            {"id": "cancel", "label": "取消操作", "intent": "cancel"},
                        ],
                    )

        elif status == "risk_warning":
            # ── 🟡 Trigger 4 (from tool loop): High-risk tool detected ──
            # The _run_tool_call_loop sends risk_warning status with
            # high_risk_tools list [{name, arguments, risk}].
            for rt in tool_calls:
                risk_info = rt.get("risk", {})
                risk_flags = risk_info.get("flags", [])
                risk_level = "critical" if any(
                    f.get("severity") == "block" for f in risk_flags
                ) else "high" if any(
                    f.get("severity") == "confirm" for f in risk_flags
                ) else "medium"
                risk_msgs = [f.get("message", "") for f in risk_flags if f.get("message")]
                await manager.broadcast_risk_warning(
                    session_id,
                    str(uuid.uuid4()),
                    agent_id,
                    risk_level,
                    f"⚠️ 高风险操作: {rt.get('name', 'unknown')}",
                    "; ".join(risk_msgs) or f"Agent 尝试执行高风险工具 {rt.get('name', 'unknown')}，需用户确认",
                    [
                        {"id": "continue", "label": "继续执行", "intent": "continue"},
                        {"id": "cancel", "label": "取消操作", "intent": "cancel"},
                    ],
                )

        elif status == "circuit_breaker":
            # ── 🟡 Trigger 6: Circuit Breaker — tool loop interrupted ──
            # The tool-call loop detected a dead-loop pattern (same tool
            # failing repeatedly) and was force-stopped to prevent infinite
            # retries.  Notify the user so they understand the interruption.
            breaker_tier = tool_results[0].get("tier", "unknown") if tool_results else "unknown"
            failed_tools = [tc.get("name", "?") for tc in tool_calls]
            tier_desc = {
                "tier1": f"工具 {'/'.join(failed_tools)} 连续失败（相同错误），已自动熔断",
                "tier2": f"工具 {'/'.join(failed_tools)} 连续缺少必要参数，已自动熔断",
                "tier3": f"工具 {'/'.join(failed_tools)} 连续多轮全部失败，已自动熔断",
            }.get(breaker_tier, f"工具调用循环异常中断（{breaker_tier}）")
            await manager.broadcast(
                session_id,
                {
                    "event": "message",
                    "sessionId": session_id,
                    "content": f"⚡ {tier_desc}\n\nAgent 将基于已有结果生成回复。您可以调整问题后重试，或 @ 指定其他 Agent 处理。",
                    "sender": "system",
                    "timestamp": now(),
                    "type": "system",
                },
            )
            # Update thinking: circuit breaker tripped
            await manager.broadcast(
                session_id,
                {
                    "event": "agent_thinking",
                    "sessionId": session_id,
                    "messageId": thinking_msg_id,
                    "agentId": agent_id,
                    "phase": "synthesizing",
                    "details": f"工具调用已熔断 — {tier_desc}",
                    "timestamp": now(),
                },
            )

        elif status == "done" and tool_results:
            await manager.broadcast(
                session_id,
                {
                    "event": "tool_result",
                    "sessionId": session_id,
                    "messageId": msg_id_for_tools,
                    "results": [
                        {
                            "tool_name": tr.get("tool_name", ""),
                            "success": tr.get("success", False),
                            "result": tr.get("result") if tr.get("success") else None,
                            "error": tr.get("error") if not tr.get("success") else None,
                        }
                        for tr in tool_results
                    ],
                    "timestamp": now(),
                },
            )
            # ── Stream completion text for tool results ────────────
            ok_count = sum(1 for tr in tool_results if tr.get("success"))
            total = len(tool_results)
            if total > 0:
                status_text = f"\n✅ 工具执行完成（{ok_count}/{total} 成功）\n"
                if ok_count < total:
                    failed_names = [
                        tr.get("tool_name", "?") for tr in tool_results
                        if not tr.get("success")
                    ]
                    status_text = f"\n⚠️ 工具执行完成（{ok_count}/{total} 成功，失败: {'、'.join(failed_names)}）\n"
                await manager.stream_broadcast(
                    session_id, msg_id_for_tools,
                    status_text, is_final=False, sender=agent_id,
                )
            # Update thinking: now synthesizing results
            await manager.broadcast(
                session_id,
                {
                    "event": "agent_thinking",
                    "sessionId": session_id,
                    "messageId": thinking_msg_id,
                    "agentId": agent_id,
                    "phase": "synthesizing",
                    "details": "工具执行完成，正在综合结果生成回复...",
                    "timestamp": now(),
                },
            )

            # ── 🟡 Trigger 3: Progress Update ───────────────────────
            # After each tool iteration completes, broadcast progress so
            # the frontend ProgressBubble updates in real time.
            # Use the actual iteration count for both completed & total to
            # avoid showing misleading estimates like "1/2" when the LLM
            # is about to generate text (not make more tool calls).
            current_round = thinking_sent_iterations
            await manager.broadcast_progress_update(
                session_id,
                str(uuid.uuid4()),
                agent_id,
                current_round,  # completed steps
                current_round,  # total = completed (don't guess future rounds)
                f"第 {current_round} 轮工具调用完成",
            )

            # ── 🟡 Trigger 2: Agent Question detection ──────────────
            # Detect when tool results indicate ambiguity or the agent
            # needs user clarification (e.g., multiple file matches,
            # ambiguous search results, conflicting configurations).
            for tr in tool_results:
                result_data = tr.get("result")
                if isinstance(result_data, dict):
                    # Check for explicit ask_user flag from tools
                    if result_data.get("ask_user"):
                        question_text = result_data.get("question", "需要您的确认")
                        opts = result_data.get("options", [])
                        await manager.broadcast_agent_question(
                            session_id,
                            str(uuid.uuid4()),
                            agent_id,
                            str(question_text),
                            opts if isinstance(opts, list) else [],
                            allow_custom=True,
                        )
                    # Check for ambiguity markers in tool output
                    ambiguity = result_data.get("ambiguous") or result_data.get("multiple_matches")
                    if ambiguity and isinstance(ambiguity, list) and len(ambiguity) > 1:
                        options = [
                            {"id": f"opt_{i}", "label": str(item.get("name", item)), "description": str(item.get("path", ""))}
                            for i, item in enumerate(ambiguity[:5])
                        ]
                        await manager.broadcast_agent_question(
                            session_id,
                            str(uuid.uuid4()),
                            agent_id,
                            f"找到 {len(ambiguity)} 个匹配项，请选择目标：",
                            options,
                            allow_custom=True,
                        )

    stream_result = await stream_message(
        session_id, content, agent_id, user_id, token,
        attachments, agent=agent, collab_ctx=collab_ctx,
        on_tool_event=_on_tool_event,
        quote_references=quote_references,
        preprocess_context=preprocess_context,
        simple_mode=simple_mode,
    )

    # Non-streaming fallback
    if stream_result is None:
        message_id = str(uuid.uuid4())
        await manager.stream_broadcast(
            session_id, message_id,
            f"<thinking>正在分析中...</thinking>\n\n",
            is_final=False,
        )
        # For the non-streaming path, prepend preprocess_context to the
        # content so the LLM still receives the pre-analysis.
        fallback_content = content
        if preprocess_context:
            fallback_content = (
                f"[系统预处理分析]\n{preprocess_context}\n\n---\n\n"
                f"[用户原始问题]\n{content}"
            )
        if agent:
            from app.services.agent_service import call_agent as _call
            response = await _call(session_id, fallback_content, user_id, attachments, agent=agent, collab_ctx=collab_ctx, token=token, on_tool_event=_on_tool_event, quote_references=quote_references, simple_mode=simple_mode)
        else:
            response = await route_message(session_id, content, sender_override or "user", user_id, attachments, on_tool_event=_on_tool_event)

        if token.cancelled:
            return
        text = str(response.get("content", ""))
        for piece in ws_state.chunk_text_for_streaming(text):
            if token.cancelled:
                return
            await manager.stream_broadcast(session_id, message_id, piece, is_final=False)
            await asyncio.sleep(0.004)
        if not token.cancelled:
            await manager.stream_broadcast(session_id, message_id, "", is_final=True)
            await _broadcast_final_message(session_id, message_id, response)
        # ── Append turn to session memory ─────────────────────────
        try:
            await _append_turn_to_session_memory(
                session_id=session_id,
                user_message=content,
                agent_response=text,
                user_id=user_id,
                sender=sender_override or "",
                agent_name=agent_id,
            )
        except Exception:
            logger.debug("append turn failed (non-streaming)", exc_info=True)
        return text

    # Streaming path — real SSE chunks from the adapter
    # ── Close any tool-progress streaming message before starting the
    #     real response stream (uses a different messageId).
    await manager.stream_broadcast(
        session_id, msg_id_for_tools, "", is_final=True, sender=agent_id,
    )
    message_id = str(uuid.uuid4())
    full_response: list[str] = []
    batch: list[str] = []
    last_flush = 0.0

    try:
        async for chunk in stream_result:
            if token.cancelled:
                return ""
            if chunk:
                full_response.append(chunk)
                batch.append(chunk)
            # Flush frequently so the frontend sees tokens as they arrive.
            # Real SSE chunks come at natural pacing (every few hundred ms),
            # so a short batch window adds minimal overhead while keeping
            # latency low.
            now_ts = asyncio.get_event_loop().time()
            joined = "".join(batch)
            if batch and (now_ts - last_flush > 0.02 or len(joined) >= 20):
                await manager.stream_broadcast(
                    session_id, message_id, joined, is_final=False,
                    sender=agent_id,
                )
                batch.clear()
                last_flush = now_ts
                await asyncio.sleep(0)  # yield to event loop

        # Final flush
        if batch:
            await manager.stream_broadcast(
                session_id, message_id, "".join(batch), is_final=False,
                sender=agent_id,
            )

        if not token.cancelled:
            await manager.stream_broadcast(session_id, message_id, "", is_final=True, sender=agent_id)
            await _broadcast_final_db_message(session_id, message_id)

        response_text = "".join(full_response)

        # ── Auto-generate deploy_card when Deploy agent completes ──
        # Uses in-memory full_response so it works regardless of DB sync timing.
        if agent_id == "Deploy" and response_text:
            await _maybe_broadcast_deploy_card(session_id, message_id, response_text, agent_id)
        # ── Append turn to session memory ─────────────────────────
        try:
            await _append_turn_to_session_memory(
                session_id=session_id,
                user_message=content,
                agent_response=response_text,
                user_id=user_id,
                sender=sender_override or "",
                agent_name=agent_id,
            )
        except Exception:
            logger.debug("append turn failed (streaming)", exc_info=True)
        return response_text

    except Exception as _stream_exc:
        logger.exception("ws _invoke_agent stream failed session=%s agent=%s", session_id, agent_id)
        # Flush any remaining chunks before reporting the error
        if batch and not token.cancelled:
            await manager.stream_broadcast(
                session_id, message_id, "".join(batch), is_final=False,
                sender=agent_id,
            )
        # Send the actual error so the user can see what went wrong
        if not token.cancelled:
            error_text = f"\n\n⚠️ 流式响应处理异常：{_stream_exc}"
            await manager.stream_broadcast(
                session_id, message_id, error_text, is_final=False,
                sender=agent_id,
            )
            await manager.stream_broadcast(session_id, message_id, "", is_final=True, sender=agent_id)
        return "".join(full_response)

async def _maybe_broadcast_deploy_card(
    session_id: str, message_id: str, content: str, sender: str,
) -> None:
    """Broadcast a deploy_card event with real project data from git.

    The primary data source is the ACTUAL git repository — version hash,
    last commit message, and changed files.  The Deploy agent may optionally
    supply a `` ```deploy-card `` fenced block to override / supplement the
    auto-detected fields, but the card always reflects real project state.
    """
    import re as _re
    import time as _time

    # ── 1. Extract optional deploy-card block (agent overrides) ──────────
    agent_version = ""
    agent_description = ""
    agent_files: list[str] = []

    m = _re.search(
        r'```deploy-card\s*\n(.*?)```',
        content,
        _re.DOTALL | _re.IGNORECASE,
    )
    if m:
        block = m.group(1).strip()
        in_files = False
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("files:"):
                in_files = True
                continue
            if in_files:
                file_match = _re.match(r'^\s*-\s+(.+)$', line)
                if file_match:
                    agent_files.append(file_match.group(1).strip())
                else:
                    in_files = False
            if not in_files:
                if stripped.startswith("version:"):
                    agent_version = stripped[len("version:"):].strip()
                elif stripped.startswith("description:"):
                    agent_description = stripped[len("description:"):].strip()

    # ── 2. Gather real git data ─────────────────────────────────────────
    git_version = ""
    git_description = ""
    git_files: list[str] = []

    try:
        from app.services.git_service import git_service
        git_service.ensure_repo()

        # Version: short commit hash
        try:
            raw = git_service._run(["rev-parse", "--short", "HEAD"])
            git_version = raw.strip()[:8]
        except Exception:
            git_version = ""

        # Description: last commit message (first line = subject)
        try:
            raw = git_service._run(["log", "-1", "--pretty=%B"])
            lines = raw.strip().splitlines()
            git_description = lines[0].strip() if lines else ""
        except Exception:
            git_description = ""

        # Files: changed in last commit, or tracked files if no commits yet
        try:
            raw = git_service._run(["diff", "--name-only", "HEAD~1"])
            git_files = [f.strip() for f in raw.splitlines() if f.strip()]
        except Exception:
            try:
                raw = git_service._run(["ls-files", "--others", "--exclude-standard", "--cached"])
                git_files = [f.strip() for f in raw.splitlines() if f.strip()][:30]
            except Exception:
                git_files = []
    except Exception:
        logger.debug("deploy_card: unable to query git", exc_info=True)

    # ── 3. Merge: agent overrides > git data > fallback ──────────────────
    version = agent_version or git_version or "unknown"
    description = agent_description or git_description or content[:200].strip()
    files = agent_files if agent_files else git_files
    completed_at = _time.strftime("%Y-%m-%dT%H:%M:%S")

    logger.info(
        "deploy_card generated session=%s version=%s files=%d (git_version=%s)",
        session_id, version, len(files), git_version,
    )

    await manager.broadcast_deploy_card(
        session_id=session_id,
        message_id=message_id,
        version=version,
        completed_at=completed_at,
        description=description,
        affected_files=files,
        agent_id=sender,
    )

async def _broadcast_final_message(session_id: str, message_id: str, response: dict) -> None:
    rows = await afetch_all(
        "SELECT id,session_id AS \"sessionId\",sender,content,type,fidelity_score AS \"fidelityScore\",symbolic_json,created_at AS timestamp FROM messages WHERE session_id=$1 ORDER BY created_at DESC, id DESC LIMIT 1",
        session_id,
    )

    async def _scan_and_broadcast(msg: dict) -> None:
        """Run output guardrail scan on content before broadcasting to frontend."""
        content = msg.get("content", "")
        if content and isinstance(content, str):
            guard_result = _guardrails_scan_output(content)
            if guard_result.blocked:
                logger.warning(
                    "ws output guardrail blocked session=%s flags=%s",
                    session_id,
                    [f"{f.rule}:{f.message[:60]}" for f in guard_result.flags],
                )
                msg["content"] = (
                    "⚠️ _Agent 输出已被安全过滤器拦截。_ 输出中包含敏感信息。\n\n"
                    + "---\n"
                    + "\n".join(
                        f"- **{f.rule}**: {f.message}" for f in guard_result.flags
                    )
                )
                msg["guardrailResult"] = guard_result.to_dict()
                msg["type"] = "system"
        await manager.broadcast(session_id, msg)

    if rows:
        final = rows[0]
        final["event"] = "message"
        final["messageId"] = message_id
        try:
            final["symbolic"] = json.loads(final.pop("symbolic_json", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            final["symbolic"] = {}
        await _scan_and_broadcast(final)

        # ── Auto-generate deploy_card when Deploy agent completes ──
        sender = final.get("sender", "")
        content = final.get("content", "")
        if sender == "Deploy" and content:
            await _maybe_broadcast_deploy_card(session_id, message_id, content, sender)
    else:
        response["messageId"] = message_id
        await _scan_and_broadcast(response)

        # ── Auto-generate deploy_card for response path too ──
        sender = response.get("sender", "")
        content = response.get("content", "")
        if sender == "Deploy" and content and isinstance(content, str):
            await _maybe_broadcast_deploy_card(session_id, message_id, content, sender)

async def _broadcast_final_db_message(session_id: str, message_id: str) -> None:
    rows = await afetch_all(
        "SELECT id,session_id AS \"sessionId\",sender,content,type,fidelity_score AS \"fidelityScore\",symbolic_json,user_id AS \"userId\",created_at AS timestamp FROM messages WHERE session_id=$1 ORDER BY created_at DESC, id DESC LIMIT 1",
        session_id,
    )
    if rows:
        final = rows[0]
        final["event"] = "message"
        final["messageId"] = message_id
        try:
            final["symbolic"] = json.loads(final.pop("symbolic_json", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            final["symbolic"] = {}

        # ── Output guardrail scan ───────────────────────────────────
        content = final.get("content", "")
        if content and isinstance(content, str):
            guard_result = _guardrails_scan_output(content)
            if guard_result.blocked:
                logger.warning(
                    "ws output guardrail blocked (streaming) session=%s flags=%s",
                    session_id,
                    [f"{f.rule}:{f.message[:60]}" for f in guard_result.flags],
                )
                final["content"] = (
                    "⚠️ _Agent 输出已被安全过滤器拦截。_ 输出中包含敏感信息。\n\n"
                    + "---\n"
                    + "\n".join(
                        f"- **{f.rule}**: {f.message}" for f in guard_result.flags
                    )
                )
                final["guardrailResult"] = guard_result.to_dict()
                final["type"] = "system"

        await manager.broadcast(session_id, final)

        # ── Auto-generate deploy_card when Deploy agent completes (streaming path) ──
        sender = final.get("sender", "")
        content = final.get("content", "")
        if sender == "Deploy" and content:
            await _maybe_broadcast_deploy_card(session_id, message_id, content, sender)


# ── Multi-@mention greeting detection ──────────────────────────────────

_MULTI_MENTION_GREETING_PATTERNS = [
    r'^(大家|各位|大伙|朋友们|伙伴们|同学们|大家好|各位好|大家早上好|大家下午好|大家晚上好|大家好呀|大家好啊|大家早|大家晚安|各位早|各位晚安|hi\s*all|hello\s*all|hey\s*all|hello\s*everyone|hi\s*everyone|hey\s*everyone)',
    r'^(你好|hi|hello|hey|嗨|早上好|下午好|晚上好|晚安|再见|bye|谢谢|thanks?|thank\s*you|3q|ok|好的|嗯|哦|知道了|收到|明白)',
    r"^(今天|最近|最近怎么样|how\s*are\s*you|what'?s?\s*up|干嘛呢|在吗|在不在|我来了|我回来了|我走了)",
    r'^(你是谁|你的名字|你能做什么|你会什么|介绍一下你自己)',
]

_MULTI_MENTION_TECH_KEYWORDS = [
    '开发', '实现', '写', '代码', '生成', '创建', '设计', '架构',
    '部署', '发布', '上线', '测试', '审查', '修复', 'bug', '错误',
    '优化', '重构', '配置', '安装', '集成', '迁移', '升级',
    'api', '接口', '页面', '组件', '模块', '功能', '系统', '数据库',
    '前端', '后端', '全栈', 'react', 'vue', 'angular', 'node',
    'python', 'java', 'go', 'rust', 'docker', 'k8s', 'ci/cd',
    'develop', 'implement', 'create', 'build', 'design', 'deploy',
    'code', 'function', 'feature', 'component', 'module',
    'crud', 'rest', 'graphql', 'sql', 'nosql', 'redis',
    '帮我', '做个', '写个', '搞个', '弄个', '帮我写', '帮我做',
    '分析', '检查', '审查', '排查', '修复', '重构',
]


def _is_multi_mention_greeting(content: str) -> bool:
    """Check if a message (with @mentions already stripped) is a greeting/chat."""
    stripped = content.strip()
    if not stripped:
        return True
    stripped_lower = stripped.lower()
    for kw in _MULTI_MENTION_TECH_KEYWORDS:
        if kw in stripped_lower:
            return False
    for pattern in _MULTI_MENTION_GREETING_PATTERNS:
        if re.match(pattern, stripped, re.IGNORECASE):
            return True
    if len(stripped) <= 15:
        return True
    return False
