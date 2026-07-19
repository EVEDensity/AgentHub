from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


DAG_NODE_TYPES = Literal[
    "start", "agent", "tool", "ifelse", "end", "code", "http", "knowledge", "human"
]


class DAGEdge(BaseModel):
    """Stable editor edge contract; aliases preserve the existing React shape."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = ""
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    label: str = ""
    condition: str = ""


class DAGNode(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: DAG_NODE_TYPES = "agent"
    name: str = ""
    domain: str = ""                  # canvas flow nodes may not have this
    agent: str = ""                   # canvas flow nodes may not have this
    description: str = ""             # canvas flow nodes may not have this
    dependencies: list[str] = Field(default_factory=list)
    x: float = 0
    y: float = 0
    config: dict[str, Any] = Field(default_factory=dict)
    status: str = "PENDING"
    priority: int = 1                # 1=highest, 3=lowest
    estimated_effort: str = "medium"  # low|medium|high


class DAGConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = Field(default=1, alias="schemaVersion")
    version: int = 1
    total: int = 0
    completed: int = 0
    nodes: list[DAGNode]
    edges: list[DAGEdge] = Field(default_factory=list)
    templateId: int | None = None
    templateName: str | None = None
    similarity: float | None = None
    execution_strategy: str = "sequential"  # sequential|parallel|mixed
    analysis: str = ""                       # Architect's reasoning
    solution_context: dict | None = None     # Selected solution (id, name, tech_stack, architecture)

    def get_ready_nodes(self) -> list[DAGNode]:
        """Return nodes whose dependencies are all satisfied."""
        completed_ids = {n.id for n in self.nodes if n.status == "SUCCESS"}
        return [
            n for n in self.nodes
            if n.status == "PENDING"
            and all(d in completed_ids for d in n.dependencies)
        ]

    def is_complete(self) -> bool:
        return all(n.status in ("SUCCESS", "FAILED") for n in self.nodes)

    def set_node_status(self, node_id: str, status: str) -> None:
        for n in self.nodes:
            if n.id == node_id:
                n.status = status
                if status == "SUCCESS":
                    self.completed += 1
                return

    def reset_node(self, node_id: str) -> None:
        for n in self.nodes:
            if n.id == node_id:
                n.status = "PENDING"
                return

    @property
    def failed_ids(self) -> set[str]:
        """Return the set of node IDs that have failed (for deadlock detection)."""
        return {n.id for n in self.nodes if n.status == "FAILED"}


class TaskState(BaseModel):
    taskId: str
    sessionId: str
    status: str
    dagProgress: DAGConfig
