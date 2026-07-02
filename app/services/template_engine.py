from __future__ import annotations

import json

from app.db.session import afetch_all, aexecute
from app.schemas.dag import DAGConfig


class TemplateEngine:
    async def list_templates(self) -> list[dict]:
        rows = await afetch_all("SELECT id,name,category,keywords,dag_json,usage_count,created_at FROM dag_templates ORDER BY id")
        for row in rows:
            row["keywords"] = json.loads(row["keywords"])
            row["dag"] = json.loads(row.pop("dag_json"))
        return rows

    async def match_template(self, intent: str) -> tuple[DAGConfig, int | None]:
        best: tuple[float, dict | None] = (0.0, None)
        intent_lower = intent.lower()
        for template in await afetch_all("SELECT id,name,keywords,dag_json FROM dag_templates"):
            keywords = json.loads(template["keywords"])
            hits = sum(1 for keyword in keywords if keyword.lower() in intent_lower or keyword in intent)
            score = hits / max(len(keywords), 1)
            if score > best[0]:
                best = (score, template)
        if best[1] and best[0] >= 0.2:
            await aexecute("UPDATE dag_templates SET usage_count=usage_count+1 WHERE id=$1", best[1]["id"])
            dag = json.loads(best[1]["dag_json"])
            dag["templateId"] = best[1]["id"]
            dag["templateName"] = best[1]["name"]
            dag["similarity"] = round(best[0], 2)
            return DAGConfig(**dag), best[1]["id"]
        return self.default_dag(intent), None

    def default_dag(self, intent: str) -> DAGConfig:
        nodes = [
            {"id": "1", "domain": "architect", "agent": "Architect", "description": "分析需求并生成实现方案", "dependencies": [], "status": "PENDING"},
            {"id": "2", "domain": "codegen", "agent": "CodeGen", "description": "生成或修改前后端代码", "dependencies": ["1"], "status": "PENDING"},
            {"id": "3", "domain": "review", "agent": "Review", "description": "审查代码与风险点", "dependencies": ["2"], "status": "PENDING"},
            {"id": "4", "domain": "test", "agent": "Test", "description": "给出测试建议与验证结果", "dependencies": ["2"], "status": "PENDING"},
        ]
        if "deploy" in intent.lower() or "部署" in intent:
            nodes.append({"id": "5", "domain": "deploy", "agent": "Deploy", "description": "执行部署并生成预览地址", "dependencies": ["3", "4"], "status": "PENDING"})
        return DAGConfig(total=len(nodes), completed=0, nodes=nodes)

    def validate(self, dag: DAGConfig) -> None:
        ids = {node.id for node in dag.nodes}
        visiting: set[str] = set()
        visited: set[str] = set()
        graph = {node.id: node.dependencies for node in dag.nodes}
        for node in dag.nodes:
            for dep in node.dependencies:
                if dep not in ids:
                    raise ValueError(f"DAG 依赖不存在：{dep}")

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError("DAG 存在循环依赖")
            if node_id in visited:
                return
            visiting.add(node_id)
            for dep_id in graph[node_id]:
                visit(dep_id)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in ids:
            visit(node_id)


template_engine = TemplateEngine()
