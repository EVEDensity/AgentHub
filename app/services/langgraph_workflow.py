from __future__ import annotations

from typing import Any, TypedDict

from app.services.agent_service import call_agent, save_message
from app.services.task_state_machine import task_state_machine
from app.services.websocket_manager import manager

try:
    from langgraph.graph import END, StateGraph
except ImportError:  # pragma: no cover - fallback keeps local dev runnable before dependency install
    END = "__end__"
    StateGraph = None


class AgentGraphState(TypedDict, total=False):
    session_id: str
    content: str
    sender: str
    user_id: str
    task: dict[str, Any]
    response: dict[str, Any]


async def persist_user_message_node(state: AgentGraphState) -> AgentGraphState:
    save_message(state["session_id"], state.get("sender", "user"), state["content"], "text")
    return state


async def create_task_node(state: AgentGraphState) -> AgentGraphState:
    task = task_state_machine.create_task(state["session_id"], state["content"])
    await manager.broadcast(state["session_id"], {"event": "task_update", **task["dagProgress"]})
    return {**state, "task": task}


async def run_dag_node(state: AgentGraphState) -> AgentGraphState:
    task = state["task"]
    await task_state_machine.run_dag(task["taskId"], state["session_id"])
    return state


async def call_agent_node(state: AgentGraphState) -> AgentGraphState:
    response = await call_agent(state["session_id"], state["content"], user_id=state.get("user_id", "local-admin"))
    return {**state, "response": response}


class LangGraphAgentWorkflow:
    def __init__(self) -> None:
        self._graph = self._build_graph() if StateGraph else None

    def _build_graph(self):
        workflow = StateGraph(AgentGraphState)
        workflow.add_node("persist_user_message", persist_user_message_node)
        workflow.add_node("create_task", create_task_node)
        workflow.add_node("run_dag", run_dag_node)
        workflow.add_node("call_agent", call_agent_node)
        workflow.set_entry_point("persist_user_message")
        workflow.add_edge("persist_user_message", "create_task")
        workflow.add_edge("create_task", "run_dag")
        workflow.add_edge("run_dag", "call_agent")
        workflow.add_edge("call_agent", END)
        return workflow.compile()

    async def run(self, session_id: str, content: str, sender: str = "user", user_id: str = "local-admin") -> dict[str, Any]:
        initial_state: AgentGraphState = {
            "session_id": session_id,
            "content": content,
            "sender": sender,
            "user_id": user_id,
        }
        if self._graph:
            final_state = await self._graph.ainvoke(initial_state)
            return final_state["response"]

        state = await persist_user_message_node(initial_state)
        state = await create_task_node(state)
        state = await run_dag_node(state)
        state = await call_agent_node(state)
        return state["response"]


agent_workflow = LangGraphAgentWorkflow()
