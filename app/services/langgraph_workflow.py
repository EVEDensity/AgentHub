from __future__ import annotations

from typing import Any, TypedDict

from app.services.agent_service import call_agent
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
    attachments: list[dict[str, Any]]
    task: dict[str, Any]
    response: dict[str, Any]
    on_tool_event: Any  # callable for WebSocket tool event broadcast


async def persist_user_message_node(state: AgentGraphState) -> AgentGraphState:
    # User message is already persisted by _process_and_stream before the
    # workflow runs — skipping the duplicate save here avoids two rows with
    # the same content that can confuse ORDER BY created_at DESC LIMIT 1.
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
    response = await call_agent(
        state["session_id"],
        state["content"],
        user_id=state.get("user_id", "local-admin"),
        attachments=state.get("attachments", []),
        on_tool_event=state.get("on_tool_event"),
    )
    return {**state, "response": response}


class LangGraphAgentWorkflow:
    def __init__(self) -> None:
        self._graph = self._build_graph() if StateGraph else None
        self._graph_info = self._build_graph_info()

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

    def _build_graph_info(self) -> dict[str, Any]:
        return {
            "nodes": [
                {"id": "persist_user_message", "label": "持久化用户消息", "description": "将用户消息保存到数据库"},
                {"id": "create_task", "label": "创建任务", "description": "创建任务记录和DAG结构"},
                {"id": "run_dag", "label": "执行DAG", "description": "运行任务的DAG流程"},
                {"id": "call_agent", "label": "调用Agent", "description": "调用具体的Agent执行任务"},
                {"id": "END", "label": "结束", "description": "任务完成"},
            ],
            "edges": [
                {"from": "persist_user_message", "to": "create_task"},
                {"from": "create_task", "to": "run_dag"},
                {"from": "run_dag", "to": "call_agent"},
                {"from": "call_agent", "to": "END"},
            ],
        }

    def get_graph_visualization(self) -> dict[str, Any]:
        """获取LangGraph工作流的可视化信息"""
        result = {
            "graph_info": self._graph_info,
            "ascii_diagram": self.get_ascii_diagram(),
            "is_langgraph_available": self._graph is not None,
        }
        return result

    def get_ascii_diagram(self) -> str:
        """生成ASCII格式的工作流图"""
        if self._graph:
            try:
                return self._graph.draw_ascii()
            except Exception:
                pass
        
        # Fallback ASCII diagram
        return """
┌─────────────────────────────────────────────────────────────┐
│                AgentHub LangGraph Workflow                  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
               ┌──────────────────────────────┐
               │    persist_user_message      │
               │      (持久化用户消息)         │
               └──────────────────────────────┘
                              │
                              ▼
               ┌──────────────────────────────┐
               │        create_task           │
               │         (创建任务)           │
               └──────────────────────────────┘
                              │
                              ▼
               ┌──────────────────────────────┐
               │          run_dag             │
               │         (执行DAG)            │
               └──────────────────────────────┘
                              │
                              ▼
               ┌──────────────────────────────┐
               │        call_agent            │
               │        (调用Agent)           │
               └──────────────────────────────┘
                              │
                              ▼
                         ┌──────┐
                         │ END  │
                         └──────┘
"""

    async def run(self, session_id: str, content: str, sender: str = "user", user_id: str = "local-admin", attachments: list[dict[str, Any]] | None = None, on_tool_event=None) -> dict[str, Any]:
        initial_state: AgentGraphState = {
            "session_id": session_id,
            "content": content,
            "sender": sender,
            "user_id": user_id,
            "attachments": attachments or [],
            "on_tool_event": on_tool_event,
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
