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
    baseModelName: str = ""
    rankLevel: str = "L1"
    dutyNote: str = ""
    baseUrl: str = ""
    apiKey: str = ""


class AgentUpdateRequest(BaseModel):
    agentId: str
    domain: str
    adapterType: str = "mock"
    baseModelName: str = ""
    rankLevel: str = "L1"
    dutyNote: str = ""
    baseUrl: str = ""
    apiKey: str = ""


class DefaultChatAgentRequest(BaseModel):
    agentId: str


# ── Tool-calling schemas ────────────────────────────────────────────

class ToolParameterSchema(BaseModel):
    name: str
    type: str = "string"
    required: bool = False
    description: str = ""


class ToolCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., min_length=1)
    category: str = "integration"
    parameters: list[ToolParameterSchema] = []
    return_type: str = "json"
    examples: list[dict] = []
    risk_level: str = "L1"
    enabled: bool = True


class ToolUpdateRequest(BaseModel):
    description: str | None = None
    category: str | None = None
    parameters: list[ToolParameterSchema] | None = None
    return_type: str | None = None
    examples: list[dict] | None = None
    risk_level: str | None = None
    enabled: bool | None = None


class AgentToolBindRequest(BaseModel):
    agent_id: str
    tool_ids: list[int]


# ── Permission rule schemas ─────────────────────────────────────────

class PermissionRuleCreateRequest(BaseModel):
    agent_id: str = "*"
    tool_pattern: str = Field(..., min_length=1)
    path_pattern: str = "*"
    behavior: str = Field(pattern="^(allow|deny|ask)$")
    priority: int = 0


class PermissionRuleUpdateRequest(BaseModel):
    tool_pattern: str | None = None
    path_pattern: str | None = None
    behavior: str | None = Field(default=None, pattern="^(allow|deny|ask)$")
    priority: int | None = None
    enabled: bool | None = None


class ToolPermissionResponse(BaseModel):
    requestId: str = Field(..., min_length=1)
    decision: str = Field(pattern="^(allow|deny)$")
