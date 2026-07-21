from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentRouteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=4000)
    triggerKeywords: list[str] = Field(default_factory=list, max_length=100)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    isDefault: bool = False
    active: bool = True
    schemaVersion: int = Field(default=1, ge=1)
    version: int = Field(default=0, ge=0)


class WorkflowValidationRequest(BaseModel):
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    schemaVersion: int = Field(default=1, ge=1)


class DAGValidationIssue(BaseModel):
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"
    nodeId: str | None = None
    edgeId: str | None = None


class DAGValidationResult(BaseModel):
    valid: bool
    normalized: dict[str, Any] | None = None
    issues: list[DAGValidationIssue] = Field(default_factory=list)


class WorkflowDraftRequest(AgentRouteRequest):
    name: str = Field(default="", max_length=128)
    workflowId: int | None = None
    baseVersion: int = Field(default=0, ge=0)
    draftVersion: int = Field(default=0, ge=0)
