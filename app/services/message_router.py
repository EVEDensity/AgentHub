from __future__ import annotations

from collections.abc import AsyncGenerator

from app.services.agent_service import call_agent, stream_agent_response

# Legacy LangGraph orchestration was decommissioned in the P0 Mission/SSE
# migration (T0-4 debt cleanup). route_message and stream_message now always
# use agent_service directly — the bounded tool loop and enterprise
# Mission/WorkUnit path are the execution-of-record.  The
# AGENTHUB_ENABLE_LEGACY_LANGGRAPH env var is retained in config.py only so
# existing deployments do not crash; it is intentionally ignored.


async def route_message(
    session_id: str,
    content: str,
    sender: str = "user",
    user_id: str = "local-admin",
    attachments: list[dict] | None = None,
    on_tool_event=None,
) -> dict:
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
