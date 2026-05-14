from __future__ import annotations

from pydantic import BaseModel, Field


class DAGNode(BaseModel):
    id: str
    domain: str
    agent: str
    description: str
    dependencies: list[str] = Field(default_factory=list)
    status: str = "PENDING"


class DAGConfig(BaseModel):
    total: int
    completed: int = 0
    nodes: list[DAGNode]
    templateId: int | None = None
    templateName: str | None = None
    similarity: float | None = None


class TaskState(BaseModel):
    taskId: str
    sessionId: str
    status: str
    dagProgress: DAGConfig
