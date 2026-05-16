from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    name: str
    password: str


class RegisterRequest(BaseModel):
    name: str
    password: str
    role: str = "developer"


class AuthOut(BaseModel):
    accessToken: str
    tokenType: str = "bearer"
    user: dict


class ChatTaskRequest(BaseModel):
    sessionId: str = "session-1"
    message: str


class ChatMessageIn(BaseModel):
    sessionId: str = "session-1"
    content: str
    sender: str = "user"
    type: str = "text"


class MessageOut(BaseModel):
    event: str = "message"
    sessionId: str
    content: str
    sender: str
    timestamp: str
    type: str = "text"
    fidelityScore: float = 0.95
    symbolic: dict | None = None


class ModelConfigRequest(BaseModel):
    provider: str = Field(pattern="^(openai|anthropic|ollama|mock|deepseek|minimax|zhipu|qwen|doubao|custom_openai)$")
    modelName: str
    apiKey: str = ""
    baseUrl: str = ""


class RoleBindRequest(BaseModel):
    role: str
    modelConfigId: int
    prompt: str = ""


class GitBranchRequest(BaseModel):
    branchName: str
    sessionId: str = "session-1"


class GitCommitRequest(BaseModel):
    sessionId: str = "session-1"
    message: str = "AgentHub auto commit"
    paths: list[str] | None = None


class AuditConfirmRequest(BaseModel):
    agentId: str = "unknown"
    action: str = "confirm"
    riskLevel: str = "L2"
    decision: str = "approve"
    payload: dict = Field(default_factory=dict)


class AgentRouteRequest(BaseModel):
    name: str
    description: str = ""
    triggerKeywords: list[str] = Field(default_factory=list)
    nodes: list[dict]
    isDefault: bool = False


class AgentRouteActiveRequest(BaseModel):
    active: bool


class AgentCreateRequest(BaseModel):
    agentId: str
    domain: str
    adapterType: str = "mock"
    riskLevel: str = "L1"
