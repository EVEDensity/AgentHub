from __future__ import annotations

from app.services.langgraph_workflow import agent_workflow


async def route_message(session_id: str, content: str, sender: str = "user", user_id: str = "local-admin") -> dict:
    return await agent_workflow.run(session_id=session_id, content=content, sender=sender, user_id=user_id)

