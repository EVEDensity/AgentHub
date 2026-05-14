from __future__ import annotations

from app.services.agent_service import call_agent, save_message
from app.services.task_state_machine import task_state_machine
from app.services.websocket_manager import manager


async def route_message(session_id: str, content: str, sender: str = "user", user_id: str = "local-admin") -> dict:
    save_message(session_id, sender, content, "text")
    task = task_state_machine.create_task(session_id, content)
    await manager.broadcast(session_id, {"event": "task_update", **task["dagProgress"]})
    await task_state_machine.run_dag(task["taskId"], session_id)
    return await call_agent(session_id, content, user_id=user_id)
