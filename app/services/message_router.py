from __future__ import annotations

from collections.abc import AsyncGenerator

from app.core.config import get_settings
from app.services.agent_service import call_agent, stream_agent_response

# Legacy LangGraph orchestration is gated by AGENTHUB_ENABLE_LEGACY_LANGGRAPH
# (default False, R2 decommission). When disabled, route_message calls the
# bounded tool loop directly and the legacy DAG engines are not invoked; the
# enterprise Mission/WorkUnit path remains the execution-of-record.
_use_legacy_langgraph = get_settings().enable_legacy_langgraph


async def route_message(
    session_id: str,
    content: str,
    sender: str = "user",
    user_id: str = "local-admin",
    attachments: list[dict] | None = None,
    on_tool_event=None,
) -> dict:
    if _use_legacy_langgraph:
        from app.services.langgraph_workflow import agent_workflow

        return await agent_workflow.run(
            session_id=session_id,
            content=content,
            sender=sender,
            user_id=user_id,
            attachments=attachments or [],
            on_tool_event=on_tool_event,
        )
    return await call_agent(
        session_id,
        content,
        user_id=user_id,
        attachments=attachments or [],
        on_tool_event=on_tool_event,
    )


async def stream_message(
    session_id: str,
    content: str,
    sender: str = "user",
    user_id: str = "local-admin",
    token=None,
    attachments: list[dict] | None = None,
    agent: dict | None = None,
    collab_ctx: str = "",
    on_tool_event=None,
    quote_references: list[dict] | None = None,
    preprocess_context: str = "",
    simple_mode: bool = False,
) -> AsyncGenerator[str, None] | None:
    return await stream_agent_response(
        session_id, content, user_id, token, attachments or [],
        agent=agent, collab_ctx=collab_ctx, on_tool_event=on_tool_event,
        quote_references=quote_references,
        preprocess_context=preprocess_context,
        simple_mode=simple_mode,
    )