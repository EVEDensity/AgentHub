from __future__ import annotations

from collections.abc import AsyncGenerator

from app.services.agent_service import call_agent, stream_agent_response
from app.services.langgraph_workflow import agent_workflow


async def route_message(session_id: str, content: str, sender: str = "user", user_id: str = "local-admin") -> dict:
    return await agent_workflow.run(session_id=session_id, content=content, sender=sender, user_id=user_id)


async def stream_message(session_id: str, content: str, sender: str = "user", user_id: str = "local-admin", token=None) -> AsyncGenerator[str, None] | None:
    return await stream_agent_response(session_id, content, user_id, token)
