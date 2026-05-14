from __future__ import annotations

import json
import uuid

from app.db.init_db import now
from app.db.session import dict_rows, get_connection, one_row
from app.schemas.dag import DAGConfig
from app.services.template_engine import template_engine
from app.services.websocket_manager import manager


class TaskStateMachine:
    valid = {
        "PENDING": {"RUNNING", "FAILED"},
        "RUNNING": {"SUCCESS", "FAILED"},
        "SUCCESS": set(),
        "FAILED": {"RUNNING"},
    }

    def create_task(self, session_id: str, message: str) -> dict:
        dag, template_id = template_engine.match_template(message)
        template_engine.validate(dag)
        task_id = str(uuid.uuid4())
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO tasks(id,session_id,status,dag_json,current_node_id,template_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (task_id, session_id, "PENDING", dag.model_dump_json(), None, template_id, now(), now()),
            )
        return {"taskId": task_id, "status": "PENDING", "dagProgress": dag.model_dump()}

    def get_task(self, task_id: str) -> dict | None:
        row = one_row("SELECT id,session_id,status,dag_json,current_node_id,template_id,created_at,updated_at FROM tasks WHERE id=?", (task_id,))
        if not row:
            return None
        row["dagProgress"] = json.loads(row.pop("dag_json"))
        return row

    def transition(self, task_id: str, new_status: str) -> None:
        task = self.get_task(task_id)
        if not task:
            raise ValueError("Task not found")
        current = task["status"]
        if new_status not in self.valid.get(current, set()):
            raise ValueError(f"Invalid transition: {current} -> {new_status}")
        with get_connection() as conn:
            conn.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=?", (new_status, now(), task_id))

    async def run_dag(self, task_id: str, session_id: str) -> dict:
        self.transition(task_id, "RUNNING")
        task = self.get_task(task_id)
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
                    await manager.broadcast(session_id, {"event": "task_update", **dag.model_dump()})
                    node.status = "SUCCESS"
                    completed.add(node.id)
                    dag.completed = len(completed)
                    await self.persist_progress(task_id, dag, node.id)
                    await manager.broadcast(session_id, {"event": "task_update", **dag.model_dump()})
            self.transition(task_id, "SUCCESS")
            return dag.model_dump()
        except Exception:
            with get_connection() as conn:
                conn.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=?", ("FAILED", now(), task_id))
            raise

    async def persist_progress(self, task_id: str, dag: DAGConfig, node_id: str | None) -> None:
        with get_connection() as conn:
            conn.execute("UPDATE tasks SET dag_json=?,current_node_id=?,updated_at=? WHERE id=?", (dag.model_dump_json(), node_id, now(), task_id))

    def list_tasks(self, session_id: str | None = None) -> list[dict]:
        if session_id:
            items = dict_rows("SELECT id,session_id,status,dag_json,current_node_id,template_id,created_at,updated_at FROM tasks WHERE session_id=? ORDER BY created_at DESC", (session_id,))
        else:
            items = dict_rows("SELECT id,session_id,status,dag_json,current_node_id,template_id,created_at,updated_at FROM tasks ORDER BY created_at DESC")
        for item in items:
            item["dagProgress"] = json.loads(item.pop("dag_json"))
        return items


task_state_machine = TaskStateMachine()
