from __future__ import annotations

import json
import uuid

from app.db.init_db import now
from app.db.session import afetch_all, afetch_one, aexecute, aexecute_insert
from app.schemas.dag import DAGConfig
from app.services.agent_route_service import agent_route_service
from app.services.template_engine import template_engine


class TaskStateMachine:
    valid = {
        "PENDING": {"RUNNING", "FAILED"},
        "RUNNING": {"SUCCESS", "FAILED"},
        "SUCCESS": set(),
        "FAILED": {"RUNNING"},
    }

    async def create_task(self, session_id: str, message: str, user_id: str = "") -> dict:
        dag, template_id, route = await agent_route_service.resolve_dag(message, user_id)
        template_engine.validate(dag)
        task_id = str(uuid.uuid4())
        route_id = route["id"] if route else None
        await aexecute_insert(
            "INSERT INTO tasks(id,session_id,status,dag_json,current_node_id,template_id,agent_route_id,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id",
            task_id, session_id, "PENDING", dag.model_dump_json(), None, template_id, route_id, now(), now(),
        )
        progress = dag.model_dump()
        if route:
            progress["route"] = {"id": route["id"], "name": route["name"], "description": route["description"]}
        return {"taskId": task_id, "status": "PENDING", "dagProgress": progress}

    async def get_task(self, task_id: str) -> dict | None:
        row = await afetch_one("SELECT id,session_id,status,dag_json,current_node_id,template_id,agent_route_id,created_at,updated_at FROM tasks WHERE id=$1", task_id)
        if not row:
            return None
        row["dagProgress"] = json.loads(row.pop("dag_json"))
        return row

    async def transition(self, task_id: str, new_status: str) -> None:
        task = await self.get_task(task_id)
        if not task:
            raise ValueError("Task not found")
        current = task["status"]
        if new_status not in self.valid.get(current, set()):
            raise ValueError(f"Invalid transition: {current} -> {new_status}")
        await aexecute("UPDATE tasks SET status=$1,updated_at=$2 WHERE id=$3", new_status, now(), task_id)

    async def run_dag(self, task_id: str, session_id: str) -> dict:
        await self.transition(task_id, "RUNNING")
        task = await self.get_task(task_id)
        dag = DAGConfig(**task["dagProgress"])
        completed: set[str] = set()
        try:
            while len(completed) < len(dag.nodes):
                ready = [node for node in dag.nodes if node.id not in completed and node.status == "PENDING" and all(dep in completed for dep in node.dependencies)]
                if not ready:
                    raise ValueError("DAG 无可执行节点，可能存在依赖错误")
                for node in ready:
                    node.status = "RUNNING"
                    await self.persist_progress(task_id, dag, node.id)
                    node.status = "SUCCESS"
                    completed.add(node.id)
                    dag.completed = len(completed)
                    await self.persist_progress(task_id, dag, node.id)
            await self.transition(task_id, "SUCCESS")
            return dag.model_dump()
        except Exception:
            await aexecute("UPDATE tasks SET status=$1,updated_at=$2 WHERE id=$3", "FAILED", now(), task_id)
            raise

    async def persist_progress(self, task_id: str, dag: DAGConfig, node_id: str | None) -> None:
        await aexecute("UPDATE tasks SET dag_json=$1,current_node_id=$2,updated_at=$3 WHERE id=$4", dag.model_dump_json(), node_id, now(), task_id)

    async def list_tasks(self, session_id: str | None = None) -> list[dict]:
        if session_id:
            items = await afetch_all("SELECT id,session_id,status,dag_json,current_node_id,template_id,agent_route_id,created_at,updated_at FROM tasks WHERE session_id=$1 ORDER BY created_at DESC", session_id)
        else:
            items = await afetch_all("SELECT id,session_id,status,dag_json,current_node_id,template_id,agent_route_id,created_at,updated_at FROM tasks ORDER BY created_at DESC")
        for item in items:
            item["dagProgress"] = json.loads(item.pop("dag_json"))
        return items


task_state_machine = TaskStateMachine()
