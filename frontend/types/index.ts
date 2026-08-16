export type FileCategory = 'code' | 'document' | 'image' | 'archive' | 'spreadsheet' | 'presentation' | 'config' | 'unknown';

export interface AttachmentMeta {
  name: string;
  size: number;
  type: string;
  content?: string;
  category?: FileCategory;
  fileId?: string;
  uploadProgress?: number;
  uploadStatus?: 'pending' | 'uploading' | 'done' | 'error';
  uploadError?: string;
}

export interface UploadInitResponse {
  uploadId: string;
  chunkSizeHint: number;
}

export interface UploadChunkResponse {
  uploadId: string;
  chunkIndex: number;
  received: number;
  totalChunks: number;
}

export interface UploadCompleteResponse {
  fileId: string;
  fileName: string;
  size: number;
  category: string;
}

export interface GuardrailFlag {
  category: 'pii' | 'injection' | 'harmful' | 'high_risk_op';
  severity: 'block' | 'confirm' | 'warn';
  rule: string;
  message: string;
}

export interface GuardrailResult {
  passed: boolean;
  blocked: boolean;
  requiresConfirmation: boolean;
  warningCount: number;
  flags: GuardrailFlag[];
}

export interface Message {
  id?: string;
  event: string;
  sessionId: string;
  sender: string;
  content: string;
  type: 'text' | 'code' | 'system' | 'diff' | 'tool_call' | 'tool_result' | 'agent_question' | 'progress_update' | 'risk_warning' | 'agent_todo' | 'task_preview' | 'solution_proposal' | 'terminal' | 'deploy_card';
  timestamp: string;
  userId?: string;           // JWT-derived user ID (empty for agent messages)
  /** Multi-turn request tracking */
  turnId?: string;
  threadId?: string;
  guardrailResult?: GuardrailResult;
  symbolic?: SymbolicData & {
    generated?: GeneratedData;
  };
  messageId?: string;
  isStreaming?: boolean;
  toolCallData?: ToolCallData;
  toolResultData?: ToolResultData;
  attachments?: AttachmentMeta[];
  /** PM interaction payloads */
  questionData?: AgentQuestionEvent;
  progressData?: ProgressUpdateEvent;
  riskWarningData?: RiskWarningEvent;
  todoData?: AgentTodoEvent;
  taskPreviewData?: TaskPreviewEvent;
  solutionProposalData?: SolutionProposalEvent;
  deployCardData?: DeployCardEvent;
  /** Diff accept/reject */
  diffDecisionState?: 'pending' | 'accepted' | 'rejected';
  diffFilePath?: string;
  /** Optimistic thinking placeholder (replaced by real agent_thinking event) */
  _optimistic?: boolean;
}

export interface StreamChunk {
  event: 'message_chunk';
  messageId: string;
  sessionId: string;
  content: string;
  isFinal: boolean;
  turnId?: string;
  threadId?: string;
}

export interface StreamInterrupted {
  event: 'stream_interrupted';
  sessionId: string;
  reason: string;
  timestamp: string;
}

export interface SymbolicData {
  task_fingerprint_id: string;
  session_id: string;
  core_summary: string;
  extended_summaries: Array<{ id: string; text: string; vector_idx: string }>;
  key_params: Record<string, unknown>;
  knowledge_vector_idx: string[];
  confidence: number;
  distillation_model: string;
  source_trace?: { original_vector_idx: string; audit_hash: string };
}

export interface ModelConfig {
  id: number;
  provider: string;
  modelName: string;
  baseUrl: string;
  isActive: boolean;
  createdAt: string;
  apiKey?: string;
}

export interface Agent {
  agentId: string;
  domain: string;
  status: string;
  adapterType: string;
  baseModelName?: string;
  rankLevel?: string;
  dutyNote?: string;
  displayName?: string;
  avatarUrl?: string;
  capabilityTags?: string[];
  baseUrl?: string;
  /** Local agent fields */
  isLocal?: boolean;
  localStatus?: 'online' | 'offline' | 'unknown';
  installPath?: string;
  version?: string;
  /** Prompt template fields (Sprint F3) */
  systemPrompt?: string;
  userPrompt?: string;
  assistantPrompt?: string;
  promptVariables?: Record<string, string>;
  /** Public bot sharing config (stored in agent_registry.config column) */
  publicConfig?: PublicConfig;
}

/** Public bot sharing configuration — branding for the /app/[botId] page */
export interface PublicConfig {
  enabled: boolean;
  welcomeMessage: string;
  placeholder: string;
  themeColor: string;
  logoUrl: string;
  suggestedQuestions: string[];
}

// ── Platform labels & colors for agent adapter types ──────────────

export const PLATFORM_LABELS: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  ollama: 'Ollama',
  deepseek: 'DeepSeek',
  minimax: 'MiniMax',
  zhipu: '智谱AI',
  qwen: '通义千问',
  doubao: '字节豆包',
  kimi: 'Kimi',
  custom_openai: '自定义',
  cloud_code: 'CloudCode',
  local_claude: 'Claude Code',
  local_codex: 'Codex CLI',
  local_openclaw: 'OpenClaw',
  mock: 'Mock',
};

export const PLATFORM_COLORS: Record<string, string> = {
  openai: '#10a37f',
  anthropic: '#d97706',
  ollama: '#1a1a1a',
  deepseek: '#4f46e5',
  minimax: '#6366f1',
  zhipu: '#dc2626',
  qwen: '#6d28d9',
  doubao: '#0891b2',
  kimi: '#f59e0b',
  custom_openai: '#0ea5e9',
  cloud_code: '#8b5cf6',
  local_claude: '#d97706',
  local_codex: '#10a37f',
  local_openclaw: '#6366f1',
  mock: '#6b7280',
};

export interface Task {
  taskId: string;
  status: 'PENDING' | 'RUNNING' | 'SUCCESS' | 'FAILED';
  dagProgress: DAGProgress;
}

export interface DAGProgress {
  nodes: DAGNode[];
  completed: number;
}

export interface DAGNode {
  id: string;
  name?: string;
  agent?: string;
  description?: string;
  status: 'PENDING' | 'RUNNING' | 'SUCCESS' | 'FAILED' | string;
  dependencies: string[];
}

export interface ModelFormState {
  provider: string;
  modelName: string;
  apiKey: string;
  baseUrl: string;
}

export interface BindFormState {
  role: string;
  modelConfigId: string;
  prompt: string;
}

export interface TestState {
  [modelId: number]: {
    status: 'checking' | 'success' | 'failed';
    message: string;
    latencyMs?: number;
  };
}

export interface User {
  id?: string;
  name: string;
  role: string;
  created_at?: string;
}

export interface AuthFormState {
  name: string;
  password: string;
}

export interface QuoteReference {
  id: string;
  messageId: string;
  quotedText: string;
  originalSender: string;
  originalTimestamp: string;
  isFullMessage: boolean;
}

export interface PendingMessage {
  sessionId: string;
  content: string;
  sender: string;
  timestamp: string;
  type: 'text' | 'code' | 'system' | 'diff';
  attachments?: AttachmentMeta[];
  quoteReferences?: QuoteReference[];
  exec_permission?: number;  // 1=询问 2=跳过 3=计划
  auto_reply?: boolean;      // 无@Agent时是否自动使用默认Agent回复（默认 true）
}

export interface GeneratedFileDetail {
  path: string;
  content: string;
}

export interface GeneratedData {
  files?: string[];
  fileDetails: GeneratedFileDetail[];
  diff?: string;
}

export interface AuditLog {
  id: string | number;
  timestamp: string;
  userId: string;
  agentId: string;
  action: string;
  riskLevel: 'low' | 'medium' | 'high' | string;
  decision: string;
  contentHash?: string;
  payload?: string;
}

export interface AgentRouteNode {
  id: string;
  domain: string;
  agent: string;
  description: string;
  dependencies: string[];
  status: 'PENDING' | 'RUNNING' | 'SUCCESS' | 'FAILED' | string;
  layer?: 'meta' | 'domain' | 'micro' | string;
}

export interface AgentRoute {
  id: number;
  name: string;
  description: string;
  triggerKeywords: string[];
  nodes: AgentRouteNode[];
  isDefault: boolean;
  active: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface ChatSession {
  id: string;
  name: string;
  type?: string;
  active?: number;
  archived?: boolean;
  createdAt?: string;
  isPinned?: number;
  lastMessageAt?: string;
  lastMessage?: string;
  ownerId?: string;
  visibility?: string;
  myRole?: string;
  memberCount?: number;
  unreadCount?: number;
}

export interface DagState {
  total: number;
  completed: number;
  nodes: Array<{
    id?: string;
    name?: string;
    status?: string;
    agent?: string;
    domain?: string;
    description?: string;
    dependencies?: string[];
    priority?: number;
    estimated_effort?: string;
    error?: string;
    duration_ms?: number;
  }>;
}

export interface WorkflowSummary {
  routeId: number;
  name: string;
  description: string;
  triggerKeywords: string[];
}

// ── Workflow Enhancement Types (P1-4) ──────────────────────────────

/** Extended node types beyond the original 5 */
export type WorkflowNodeType = 'start' | 'agent' | 'tool' | 'ifelse' | 'end' | 'code' | 'http' | 'knowledge' | 'human';

/** Code execution config for 'code' nodes */
export interface CodeNodeConfig {
  language: 'python' | 'javascript' | 'bash' | 'sql';
  code: string;
  timeout?: number; // ms, default 30000
  env?: Record<string, string>;
}

/** HTTP call config for 'http' nodes */
export interface HttpNodeConfig {
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  url: string;
  headers?: Record<string, string>;
  body?: string;        // supports {{variable}} interpolation
  timeout?: number;
  retry?: number;
}

/** Knowledge retrieval config for 'knowledge' nodes */
export interface KnowledgeNodeConfig {
  collectionId: string;
  query: string;         // supports {{variable}} interpolation
  topK?: number;
  scoreThreshold?: number;
  fusion?: 'hybrid' | 'semantic' | 'keyword';
}

/** Human approval config for 'human' nodes */
export interface HumanNodeConfig {
  prompt: string;        // shown to the human reviewer
  options?: string[];    // predefined response options
  timeout?: number;      // auto-approve/reject after timeout (seconds)
  assignee?: string;     // specific user/role to assign
}

/** Variable reference in prompts/configs: {{node_id.output}} or {{node_id.field}} */
export interface VariableReference {
  raw: string;           // original {{...}} text
  nodeId: string;
  field: string;         // e.g., "output", "status", "result.code"
  isResolved: boolean;
  resolvedValue?: string;
}

/** Condition rule for ifelse branches */
export type ConditionOperator = 'eq' | 'neq' | 'contains' | 'not_contains' | 'gt' | 'gte' | 'lt' | 'lte' | 'regex' | 'exists' | 'empty';

export interface ConditionRule {
  id: string;
  left: string;          // variable reference or literal
  operator: ConditionOperator;
  right: string;         // comparison value
  label?: string;        // human-readable label for the edge
}

export interface BranchCondition {
  id: string;
  label: string;         // shown on the branch edge
  rules: ConditionRule[];
  logic: 'AND' | 'OR';   // how to combine multiple rules
}

/** Execution record for a workflow run */
export interface WorkflowExecution {
  id: string;
  workflowId: number;
  workflowName: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled' | 'awaiting_human';
  triggeredBy: string;   // user message or manual trigger
  startedAt: string;
  completedAt?: string;
  durationMs?: number;
  nodeResults: NodeExecutionResult[];
  error?: string;
}

export interface NodeExecutionResult {
  nodeId: string;
  nodeName: string;
  nodeType: WorkflowNodeType;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'awaiting_human';
  input?: Record<string, unknown>;
  output?: unknown;
  error?: string;
  startedAt?: string;
  completedAt?: string;
  durationMs?: number;
}

/** Variable scope for the {{node_id.output}} engine */
export interface WorkflowVariableScope {
  nodeOutputs: Record<string, unknown>;  // nodeId → output value
  triggerInput?: string;                 // original user input
  workflowParams?: Record<string, string>;
}

export interface AttachedFile {
  name: string;
  content?: string;
  size: number;
  type: string;
  category: FileCategory;
  fileId?: string;
  uploadProgress?: number;
  uploadStatus: 'pending' | 'uploading' | 'done' | 'error';
  uploadError?: string;
}

export interface ContentSegment {
  type: 'think' | 'text';
  content: string;
  isComplete: boolean;
}

// ── Memory System Types ─────────────────────────────────────────────

export type MemoryType = 'user' | 'feedback' | 'project' | 'reference';

export interface MemoryFileInfo {
  filename: string;
  name: string;
  description: string;
  type: MemoryType;
  mtime: number;
  created_at: string;
  updated_at: string;
}

export interface MemoryDetail {
  filename: string;
  meta: {
    name: string;
    description: string;
    type: MemoryType;
    created_at: string;
    updated_at: string;
  };
  body: string;
}

export interface MemoryCreateRequest {
  name: string;
  description?: string;
  type?: MemoryType;
  body?: string;
  filename?: string;
}

export interface MemoryUpdateRequest {
  name?: string;
  description?: string;
  type?: MemoryType;
  body?: string;
}

export interface MemoryIndexResponse {
  content: string;
  path: string;
}

export interface MemoryFreshnessInfo {
  filename: string;
  name: string;
  mtime: number;
  age_days: number;
  warning: string;
}

export interface ConsolidationAction {
  action: 'merge' | 'delete' | 'update' | 'keep';
  targets: string[];
  reason: string;
  merged_name?: string;
  merged_description?: string;
  merged_body?: string;
}

export interface ConsolidationResult {
  merged: Array<{ file: string; targets: string[]; reason: string }>;
  deleted: Array<{ file: string; reason: string }>;
  updated: Array<{ file: string; reason: string }>;
  unchanged: Array<{ file: string; reason: string }>;
  summary: string;
  dry_run: boolean;
  actions?: ConsolidationAction[];
}

export interface ConsolidationStatus {
  last_consolidation: string;
  consolidation_count: number;
  merged_files: Array<{ file: string; targets: string[]; reason: string }>;
}

export interface SessionSummary {
  session_id: string;
  preview: string;
  updated_at: string;
}

export interface MemorySearchResult {
  filename: string;
  name: string;
  description: string;
  type: MemoryType;
  score: number;
  snippet: string;
  mtime: number;
}

// ── Skill System Types ──────────────────────────────────────────────

export type SkillSource = 'user' | 'project' | 'plugin' | 'mcp';

export interface SkillCredential {
  name: string;
  required?: boolean;
  description?: string;
  storage?: string;
}

export interface SkillMeta {
  name: string;
  display_name: string;
  description: string;
  version: string;
  source: SkillSource;
  category: string;
  subcategory: string;
  icon: string;
  enabled: boolean;
  credentials: SkillCredential[];
  authors: string[];
  tags: string[];
  content_length: number;
  body_lines: number;
  has_skill_md: boolean;
  file_count: number;
  path: string;
}

export interface SkillDetail {
  name: string;
  source: SkillSource;
  category: string;
  subcategory: string;
  meta: Record<string, unknown>;
  body: string;
  raw: string;
  path: string;
}

export interface SkillListResponse {
  skills: SkillMeta[];
  total: number;
  total_tokens_estimate: number;
  sources: string[];
  categories: string[];
  _refresh_hint?: boolean;
}

// ── Tool Calling types ───────────────────────────────────────────────

export interface ToolCallParam {
  name: string;
  type: string;
  required: boolean;
  description: string;
  default?: unknown;
  enum?: string[];
}

export interface ToolExample {
  userQuestion: string;
  parameters: Record<string, unknown>;
}

export interface ToolDefinition {
  id?: number;
  name: string;
  description: string;
  category: string;
  parameters: ToolCallParam[];
  returnType: string;
  examples: ToolExample[];
  riskLevel: string;
  handlerType?: string;
  enabled: boolean;
  createdAt?: string;
  isConcurrencySafe?: boolean;
  requiresUserConfirmation?: boolean;
}

export interface ToolCallItem {
  name: string;
  arguments: Record<string, unknown>;
  status: 'queued' | 'executing' | 'calling' | 'success' | 'error';
  progress?: ToolProgressEvent;
  toolUseId?: string;
}

export interface ToolCallData {
  calls: ToolCallItem[];
}

export interface ToolResultItem {
  tool_name: string;
  success: boolean;
  result?: unknown;
  error?: string;
}

export interface ToolResultData {
  results: ToolResultItem[];
}

export interface ToolCallEvent {
  event: 'tool_call';
  sessionId: string;
  messageId: string;
  turnId?: string;
  threadId?: string;
  toolCalls: ToolCallItem[];
  timestamp: string;
}

export interface ToolResultEvent {
  event: 'tool_result';
  sessionId: string;
  messageId: string;
  turnId?: string;
  threadId?: string;
  results: ToolResultItem[];
  timestamp: string;
}

// ── Permission types ─────────────────────────────────────────────────

export type PermissionMode = 'default' | 'bypass' | 'auto';
export type PermissionBehavior = 'allow' | 'deny' | 'ask';

export interface PermissionRule {
  id: number;
  agentId: string;
  toolPattern: string;
  pathPattern: string;
  behavior: PermissionBehavior;
  source: string;
  priority: number;
  enabled: boolean;
  createdAt: string;
}

export interface PermissionRequestEvent {
  event: 'permission_request';
  sessionId: string;
  requestId: string;
  toolName: string;
  arguments: Record<string, unknown>;
  riskLevel: string;
  reason: string;
  timestamp: string;
}

export interface PermissionResponseMessage {
  event: 'permission_response';
  requestId: string;
  decision: 'allow' | 'deny';
}

// ── Progress types ───────────────────────────────────────────────────

export type ToolProgressType = 'progress' | 'read_progress' | 'bash_progress' | 'search_progress';

export interface ToolProgressEvent {
  event: 'tool_progress';
  sessionId: string;
  toolName: string;
  progressType: ToolProgressType;
  message: string;
  percentage?: number;
  timestamp: string;
}

// ── Search progress types ───────────────────────────────────────────

export interface SearchProgressQueryUpdate {
  type: 'query_update';
  query: string;
  provider_count: number;
}

export interface SearchProgressResultsReceived {
  type: 'search_results_received';
  query: string;
  source: string;
  result_count: number;
}

export type SearchProgressData = SearchProgressQueryUpdate | SearchProgressResultsReceived;

export interface SearchProgressEvent {
  event: 'search_progress';
  sessionId: string;
  toolName: 'web_search';
  data: SearchProgressData;
  timestamp: string;
}

// ── Product Preview types ─────────────────────────────────────────────

export type WorkspacePreviewKind = 'file' | 'diff';

export type WorkspaceFileStatus =
  | 'modified' | 'added' | 'deleted' | 'renamed'
  | 'untracked' | 'copied' | 'type_changed' | 'unknown';

export interface WorkspacePreviewTab {
  id: string;
  path: string;
  kind: WorkspacePreviewKind;
  language?: string;
  state?: 'loading' | 'ok' | 'binary' | 'too_large' | 'missing' | 'error';
  content?: string;
  contentType?: string;        // "text" | "html" — for rich document previews
  diffOld?: string;
  diffNew?: string;
  status?: WorkspaceFileStatus;
  /** PPTX-specific metadata */
  slideCount?: number;
  imageCount?: number;
  textLength?: number;
  totalChars?: number;
  truncated?: boolean;
  _version?: number;
}

// ── Agent 真落盘 — workspace real-time event types ──────────────────────

export interface WorkspaceChangeEvent {
  event: 'workspace_change';
  sessionId: string;
  path: string;
  operation: 'write' | 'delete' | 'rename';
  userId: string;
  agentId?: string;
  sizeBytes: number;
  diffPreview?: string;
  oldPath?: string;
  timestamp: string;
}

export interface FileConflictEvent {
  event: 'file_conflict';
  sessionId: string;
  path: string;
  oursUserId: string;
  theirsUserId: string;
  oursPreview?: string;
  theirsPreview?: string;
  diff?: string;
  backupPath?: string;
  timestamp: string;
}

export interface FileLockChangeEvent {
  event: 'file_lock_change';
  sessionId: string;
  path: string;
  userId: string;
  locked: boolean;
  holderName?: string;
  timestamp: string;
}

export type WorkspaceEvent = WorkspaceChangeEvent | FileConflictEvent | FileLockChangeEvent;

export interface FileReference {
  id: string;
  name: string;
  path: string;
  lineStart?: number;
  lineEnd?: number;
  quote?: string;
  kind?: 'file' | 'folder' | 'chat-selection' | 'markdown-paragraph';
}

// ── PM/PMO Agent Interaction Types ───────────────────────────────────

/** PM agent state machine */
export type PMState = 'DECOMPOSING' | 'DISPATCHING' | 'WAITING_USER' | 'EXECUTING' | 'SUMMARIZING' | 'IDLE';

/** PM state labels for UI display */
export const PM_STATE_LABELS: Record<PMState, string> = {
  IDLE: '就绪',
  DECOMPOSING: '拆解中',
  DISPATCHING: '调度中',
  WAITING_USER: '等待用户',
  EXECUTING: '执行中',
  SUMMARIZING: '汇总中',
};

/** PM state change event from backend */
export interface PMStateChangeEvent {
  event: 'pm_state_change';
  sessionId: string;
  state: PMState;
  previousState: PMState;
  details?: string;
  timestamp: string;
}

/** Agent asks user a clarifying question with clickable options */
export interface AgentQuestionEvent {
  event: 'agent_question';
  sessionId: string;
  messageId: string;
  agentId: string;
  question: string;
  options: AgentQuestionOption[];
  allowCustomAnswer: boolean;
  timestamp: string;
  /** Multi-user: set by interaction_already_resolved event */
  resolvedBy?: string;
  resolvedByName?: string;
}

export interface AgentQuestionOption {
  id: string;
  label: string;
  description?: string;
}

/** User's response to an agent question */
export interface AgentQuestionResponse {
  event: 'agent_question_response';
  sessionId: string;
  messageId: string;
  questionMessageId: string;
  selectedOptionId?: string;
  customAnswer?: string;
}

/** PM reports progress update */
export interface ProgressUpdateEvent {
  event: 'progress_update';
  sessionId: string;
  messageId: string;
  completedSteps: number;
  totalSteps: number;
  currentStep: string;
  estimatedRemainingSeconds?: number;
  agentId: string;
  timestamp: string;
}

/** PM warns about a risk */
export interface RiskWarningEvent {
  event: 'risk_warning';
  sessionId: string;
  messageId: string;
  agentId: string;
  riskLevel: 'low' | 'medium' | 'high' | 'critical';
  title: string;
  description: string;
  actions: RiskWarningAction[];
  timestamp: string;
  /** Multi-user: set by interaction_already_resolved event */
  resolvedBy?: string;
  resolvedByName?: string;
}

export interface RiskWarningAction {
  id: string;
  label: string;
  /** 'continue' = proceed anyway, 'mitigate' = apply mitigation, 'cancel' = stop */
  intent: 'continue' | 'mitigate' | 'cancel';
  description?: string;
}

/** User's response to a risk warning */
export interface RiskWarningResponse {
  event: 'risk_warning_response';
  sessionId: string;
  warningMessageId: string;
  selectedActionId: string;
  timestamp: string;
}

/** PM pushes a decision/todo to user */
export interface AgentTodoEvent {
  event: 'agent_todo';
  sessionId: string;
  messageId: string;
  agentId: string;
  title: string;
  description: string;
  actions: AgentTodoAction[];
  priority: 'low' | 'medium' | 'high';
  timestamp: string;
  /** Multi-user: set by interaction_already_resolved event */
  resolvedBy?: string;
  resolvedByName?: string;
}

export interface AgentTodoAction {
  id: string;
  label: string;
  intent: 'approve' | 'reject' | 'modify';
  description?: string;
}

/** User's response to an agent todo */
export interface AgentTodoResponse {
  event: 'agent_todo_response';
  sessionId: string;
  todoMessageId: string;
  selectedActionId: string;
  comment?: string;
  timestamp: string;
}

/** Degradation mode status */
export interface DegradationStatus {
  active: boolean;
  reason: string;
  startedAt: string;
  failedModels: string[];
  recoveryAttempts: number;
  lastRecoveryAttempt?: string;
}

/** Degradation state change event */
export interface DegradationEvent {
  event: 'degradation_change';
  sessionId: string;
  status: DegradationStatus;
  timestamp: string;
}

/** PM task preview for user confirmation */
export interface TaskPreviewEvent {
  event: 'task_preview';
  sessionId: string;
  messageId: string;
  tasks: TaskPreviewItem[];
  estimatedTotalSeconds?: number;
  timestamp: string;
  /** Multi-user: set by interaction_already_resolved event */
  resolvedBy?: string;
  resolvedByName?: string;
}

export interface TaskPreviewItem {
  id: string;
  description: string;
  agent: string;
  dependencies: string[];
  estimatedSeconds?: number;
}

/** User confirms or modifies task preview */
export interface TaskPreviewResponse {
  event: 'task_preview_response';
  sessionId: string;
  previewMessageId: string;
  decision: 'confirm' | 'cancel' | 'modify';
  modifications?: string;
  timestamp: string;
}

// ── Solution Proposal Event ───────────────────────────────────────────

/** A single solution option within a solution proposal */
export interface SolutionOption {
  id: string;
  name: string;
  techStack: string[];
  architecture: string;
  pros: string[];
  cons: string[];
  estimatedEffort: string;
  riskLevel: 'low' | 'medium' | 'high';
  score: number;
}

/** Orchestrator sends solution proposal for user to review and select */
export interface SolutionProposalEvent {
  event: 'solution_proposal';
  sessionId: string;
  messageId: string;
  intentType: string;
  requirements: string[];
  nonFunctionalRequirements: string[];
  constraints: string[];
  solutions: SolutionOption[];
  recommendedSolutionId: string;
  recommendationReason: string;
  autoConfirmSeconds: number;
  timestamp: string;
  /** Multi-user: set by interaction_already_resolved event */
  resolvedBy?: string;
  resolvedByName?: string;
}

/** User selects a solution (or frontend auto-selects on timeout) */
export interface SolutionSelectionEvent {
  event: 'solution_selection';
  sessionId: string;
  messageId: string;
  solutionId: string;
  autoSelected: boolean;
  timestamp: string;
}

// ── Deploy Card Event ────────────────────────────────────────────────

/** Deploy agent sends a deployment card when project is completed */
export interface DeployCardEvent {
  event: 'deploy_card';
  sessionId: string;
  messageId: string;
  /** Git commit hash (short) */
  version: string;
  /** ISO timestamp when the version was created */
  completedAt: string;
  /** Project description / feat message */
  description: string;
  /** List of affected/changed files */
  affectedFiles: string[];
  /** Agent that generated this card */
  agentId: string;
  timestamp: string;
  /** 项目 ID */
  projectId?: string;
  /** 默认分支 */
  defaultBranch?: string;
  /** 默认域名 */
  defaultDomain?: string;
  /** 部署类型候选 */
  deployTypeOptions?: ('preview' | 'production' | 'custom')[];
}

/** User requests deployment details */
export interface DeployRequest {
  event: 'deploy_request';
  sessionId: string;
  messageId: string;
  version: string;
  /** 目标项目 ID */
  projectId: string;
  /** Git 分支 */
  branch: string;
  /** 自定义域名 */
  domain: string;
  /** 部署类型 */
  deployType: 'preview' | 'production' | 'custom';
  /** Deployment environment */
  environment: string;
  /** Deployment notes */
  notes: string;
  /** Deploy target (currently frontend only) */
  targets: string[];
  timestamp: string;
}

/** User requests version rollback */
export interface DeployRollbackRequest {
  event: 'deploy_rollback';
  sessionId: string;
  messageId: string;
  version: string;
  timestamp: string;
}

// ── CloudCode: diff events ───────────────────────────────────────

/** Real-time diff update from CloudCode agent (edit_file tool use) */
export interface DiffUpdateEvent {
  event: 'diff_update';
  sessionId: string;
  messageId: string;
  turnId?: string;
  path: string;
  diff: string;
  timestamp: string;
}

/** User accepts or rejects a diff displayed in DiffBubble */
export interface DiffDecisionEvent {
  event: 'diff_decision';
  sessionId: string;
  messageId: string;
  decision: 'accept' | 'reject';
  path: string;
}

/** Terminal output streaming from CloudCode agent (run_command tool use) */
export interface TerminalOutputEvent {
  event: 'terminal_output';
  sessionId: string;
  messageId: string;
  turnId?: string;
  content: string;
  sender: string;
  timestamp: string;
}

/** Session badge for pending user decisions */
export interface SessionBadge {
  pendingDecisions: number;
  hasRiskWarnings: boolean;
  hasQuestions: boolean;
}

// ── Multi-User Collaboration Event Types ───────────────────────────────

export interface PresenceUser {
  userId: string;
  name: string;
  role: string;
  status: 'online' | 'idle' | 'typing' | 'offline';
}

export interface UserJoinedEvent {
  event: 'user_joined';
  sessionId: string;
  userId: string;
  userName: string;
  role: string;
  timestamp: string;
}

export interface UserLeftEvent {
  event: 'user_left';
  sessionId: string;
  userId: string;
  userName: string;
  timestamp: string;
}

export interface UserRosterEvent {
  event: 'user_roster';
  sessionId: string;
  users: PresenceUser[];
}

export interface PresenceUpdateEvent {
  event: 'presence_update';
  sessionId: string;
  users: Array<{ userId: string; status: string }>;
}

export interface TypingIndicatorEvent {
  event: 'typing_indicator';
  sessionId: string;
  userId: string;
  userName: string;
  isTyping: boolean;
}

export interface SessionMember {
  userId: string;
  userName: string;
  userRole: string;
  role: string;
  invitedBy: string;
  joinedAt: string;
  onlineStatus?: string;
}

// ── Multi-User Interaction Sync Events ────────────────────────────────────

/** Broadcast when a PM interaction has been resolved by any user */
export interface InteractionAlreadyResolvedEvent {
  event: 'interaction_already_resolved';
  sessionId: string;
  messageId: string;
  resolvedBy: string;
  userName: string;
  timestamp: string;
}

/** Broadcast when the execution permission mode is changed */
export interface PermissionModeChangedEvent {
  event: 'permission_mode_changed';
  sessionId: string;
  mode: number;
  changedBy: string;
  changedByName: string;
  timestamp: string;
}

// ── Local Agent Discovery Types ──────────────────────────────────────

/** A single discovered local AI CLI tool candidate */
export interface LocalAgentCandidate {
  adapterType: string;
  displayName: string;
  binary: string;
  installPath: string;
  version: string;
  installed: boolean;
  healthy: boolean;
  errorMessage: string;
  capabilities: string[];
  headlessCommand: string;
  registered?: boolean;
  registeredAgentId?: string;
  registeredStatus?: string;
}

/** Response from GET /api/agent/local/discover */
export interface LocalAgentDiscoverResponse {
  candidates: LocalAgentCandidate[];
  total: number;
}

/** Request body for POST /api/agent/local/register */
export interface LocalAgentRegisterRequest {
  adapterType: string;
  agentId?: string;
  domain?: string;
  displayName?: string;
  riskLevel?: string;
  baseModelName?: string;
  capabilityTags?: string[];
}

/** A single local agent status entry from GET /api/agent/local/status */
export interface LocalAgentStatus {
  agentId: string;
  adapterType: string;
  online: boolean;
  version: string;
  installPath: string;
  message: string;
}

/** Response from GET /api/agent/local/status */
export interface LocalAgentStatusResponse {
  agents: LocalAgentStatus[];
  total: number;
}

// ── Knowledge Base Types (Sprint F1) ─────────────────────────────────

export interface KnowledgeDocument {
  source_id: string;
  collection: string;
  tenant_id: string;
  chunk_count: number;
  file_type?: string;
  size_bytes?: number;
  created_at?: string;
}

export interface CollectionInfo {
  name: string;
  points_count: number;
}

export interface ChunkDetail {
  id: string;
  index: number;
  total: number;
  content: string;
  start_offset: number;
  end_offset: number;
}

export interface RetrievalResult {
  id: string;
  score: number;
  content: string;
  source_id: string;
  collection: string;
  chunk_index: number;
}

// ── Agent Version Management Types (P1-6) ────────────────────────────

export interface AgentVersion {
  id: string;
  agentId: string;
  version: number;
  snapshot: Record<string, unknown>;    // full agent config at this version
  changeSummary: string;                 // human-readable summary of what changed
  changedFields: string[];               // list of field keys that changed
  createdBy: string;                     // user who triggered the save
  createdAt: string;
}

export interface AgentVersionDiff {
  versionA: number;
  versionB: number;
  fieldDiffs: AgentFieldDiff[];
  createdAtA: string;
  createdAtB: string;
}

export interface AgentFieldDiff {
  field: string;
  label: string;                         // human-readable field name
  oldValue: unknown;
  newValue: unknown;
  type: 'added' | 'removed' | 'modified' | 'unchanged';
}

export interface AgentVersionListResponse {
  agentId: string;
  versions: AgentVersion[];
  total: number;
}

export interface AgentRollbackRequest {
  agentId: string;
  targetVersion: number;
}

// ── MCP Gateway Types (P1-2) ───────────────────────────────────────────

export interface MCPServerConfig {
  id: string;
  name: string;
  description: string;
  transport: 'stdio' | 'sse';
  // STDIO transport
  command?: string;          // e.g. "node", "python", "uvx"
  args?: string[];           // e.g. ["server.js"]
  env?: Record<string, string>;
  // SSE transport
  url?: string;              // e.g. "http://localhost:8099/mcp"
  // Status
  status: 'connected' | 'disconnected' | 'error' | 'unknown';
  lastConnectedAt?: string;
  errorMessage?: string;
  // Metadata
  tags?: string[];
  createdAt: string;
  updatedAt: string;
}

export interface MCPToolInfo {
  name: string;
  description: string;
  inputSchema: {
    type: string;
    properties?: Record<string, {
      type: string;
      description?: string;
      enum?: unknown[];
      default?: unknown;
    }>;
    required?: string[];
  };
  serverId: string;
  serverName: string;
}

export interface MCPResourceInfo {
  uri: string;
  name: string;
  description: string;
  mimeType?: string;
  serverId: string;
  serverName: string;
}

export interface MCPPromptInfo {
  name: string;
  description: string;
  arguments?: Array<{
    name: string;
    description?: string;
    required?: boolean;
  }>;
  serverId: string;
  serverName: string;
}

export interface MCPToolCallResult {
  content: Array<{
    type: 'text' | 'image' | 'resource';
    text?: string;
    data?: string;
    mimeType?: string;
  }>;
  isError?: boolean;
}

export interface MCPServerListResponse {
  servers: MCPServerConfig[];
}

// JSON-RPC 2.0 types used by MCP protocol
export interface JSONRPCRequest {
  jsonrpc: '2.0';
  id?: number;
  method: string;
  params?: Record<string, unknown>;
}

export interface JSONRPCResponse {
  jsonrpc: '2.0';
  id: number;
  result?: unknown;
  error?: {
    code: number;
    message: string;
    data?: unknown;
  };
}

// ── A2A Protocol Types (P2-2) ──────────────────────────────────────────

export interface A2AAgentCard {
  protocolVersion: string;
  name: string;
  description: string;
  url: string;
  provider?: {
    name?: string;
    url?: string;
    organization?: string;
  };
  capabilities: {
    streaming: boolean;
    pushNotifications: boolean;
    stateTransitionHistory: boolean;
    multimodal?: boolean;
    codeExecution?: boolean;
  };
  skills: A2ASkill[];
  endpoints: {
    taskApi: string;
    streaming?: string;
    webhookUrl?: string;
  };
  authSchemes?: A2AAuthScheme[];
  version?: string;
  documentation?: string;
  iconUrl?: string;
  tenantId?: string;
  source?: 'internal' | 'external';
  status?: 'active' | 'inactive' | 'error';
  lastSeenAt?: string;
  createdAt?: string;
  tags?: string[];
}

export interface A2ASkill {
  id: string;
  name: string;
  description?: string;
  tags: string[];
  examples?: string[];
  inputSchema?: Record<string, unknown>;
  outputSchema?: Record<string, unknown>;
}

export interface A2AAuthScheme {
  type: 'bearer' | 'oauth2' | 'apiKey';
  description?: string;
  tokenUrl?: string;
  scopes?: string[];
}

export interface A2ATask {
  id: string;
  sessionId?: string;
  status: 'pending' | 'working' | 'completed' | 'failed' | 'cancelled';
  message?: {
    role: string;
    parts: Array<{
      type: 'text' | 'file' | 'data';
      text?: string;
      file?: { name?: string; mimeType?: string; bytes?: string; url?: string };
      data?: Record<string, unknown>;
    }>;
  };
  artifacts?: A2AArtifact[];
  createdAt: string;
  updatedAt: string;
}

export interface A2AArtifact {
  artifactId: string;
  name: string;
  parts: Array<{
    type: string;
    text?: string;
    data?: Record<string, unknown>;
  }>;
}

export interface A2ADiscoveryQuery {
  capabilities?: string[];
}

export interface A2ADiscoveryResponse {
  agents: A2AAgentCard[];
  count: number;
}

// ── RAG Document Search Types (P1-1) ──────────────────────────────────

export type RAGSourceType = 'project_docs' | 'api_docs' | 'uploaded_docs' | 'code_repos' | 'sessions' | 'artifacts';

export interface RAGSearchResult {
  source_id: string;
  chunk_id: string;
  text: string;
  score: number;
  source_type: RAGSourceType;
  metadata: Record<string, string | undefined>;
  highlights: string[];
}

export interface ImageResult {
  id: string;
  url: string;
  caption: string;
  score: number;
  source_id: string;
  source_type: RAGSourceType;
  width?: number;
  height?: number;
}

export interface RAGSearchResponse {
  query: string;
  rewrites: string[];
  results: RAGSearchResult[];
  images: ImageResult[];
  fusion: string;
  latency_ms: number;
}

export interface RAGSourceItem {
  key: RAGSourceType;
  label: string;
  icon: string;
  description: string;
}

// ── Template Types (Sprint F2) ───────────────────────────────────────

export interface AgentTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
  icon: string;
  tags: string[];
  source: 'builtin' | 'user';
  version: string;
  author: string;
  agent_config: Record<string, unknown>;
  workflow_json: string;
  prompt_json: string;
  tools_json: string[];
  knowledge_json: string;
  usage_count: number;
  rating: number;
  created_at: string;
  updated_at: string;
}

export interface PromptBlock {
  type: 'system' | 'user' | 'assistant';
  content: string;
  variables: string[];
}

// ── Workspace Types (Sprint F4) ──────────────────────────────────────

export interface Workspace {
  id: string;
  name: string;
  description: string;
  owner_id: string;
  member_count: number;
  created_at: string;
}

export interface WorkspaceMember {
  user_id: string;
  display_name: string;
  email: string;
  role: 'admin' | 'editor' | 'viewer';
  joined_at: string;
}

// ── AgentNet Types (Sprint I) ─────────────────────────────────────────

export interface AgentCapability {
  agent_id: string;
  display_name: string;
  capabilities: string[];
  preferred_tools: string[];
  quality_score: number;
  current_load: number;
  max_concurrent: number;
  cost_per_task: number;
  status: 'idle' | 'busy' | 'overloaded' | 'offline';
  last_heartbeat: string;
  registered_at: string;
}

export interface AgentNetTask {
  task_id: string;
  parent_task_id?: string;
  dag_id?: string;
  correlation_id: string;
  category: string;
  description: string;
  required_capability: string;
  assigned_agent?: string;
  status: 'pending' | 'assigned' | 'running' | 'completed' | 'failed';
  input?: unknown;
  result?: unknown;
  error?: string;
  created_at: string;
  assigned_at?: string;
  completed_at?: string;
}

export interface AgentNetDAGNode {
  id: string;
  task_id?: string;
  agent_id?: string;
  description: string;
  required_capability: string;
  dependencies: string[];
  status: 'pending' | 'ready' | 'running' | 'completed' | 'failed';
  priority: number;
  estimated_seconds: number;
  result?: unknown;
  error?: string;
  started_at?: string;
  completed_at?: string;
}

export interface AgentNetDAGEdge {
  from: string;
  to: string;
  label?: string;
  weight: number;
}

export interface AgentNetDAG {
  dag_id: string;
  name: string;
  tenant_id: string;
  session_id: string;
  nodes: AgentNetDAGNode[];
  edges: AgentNetDAGEdge[];
  status: 'created' | 'running' | 'completed' | 'failed' | 'cancelled';
  strategy: 'round-robin' | 'least-loaded' | 'capability-match' | 'cost-optimized';
  created_at: string;
  updated_at: string;
}

export interface AgentSpawn {
  spawn_id: string;
  parent_id: string;
  child_id: string;
  child_name: string;
  reason: string;
  capabilities: string[];
  status: 'created' | 'running' | 'completed' | 'destroyed';
  created_at: string;
  completed_at?: string;
  ttl_seconds: number;
}

export interface SharedMemoryEntry {
  id: string;
  agent_id: string;
  content: string;
  intent?: string;
  target?: string;
  timestamp: string;
}

export interface AgentNetStats {
  total_agents: number;
  active_agents: number;
  agents_by_status: Record<string, number>;
  total_tasks: number;
  tasks_by_status: Record<string, number>;
  active_dags: number;
  active_spawns: number;
  memory_entries: number;
  avg_quality_score: number;
}

export interface TopologyNode {
  id: string;
  label: string;
  type: 'agent' | 'task' | 'spawn';
  status: string;
  quality?: number;
  load?: number;
  max_load?: number;
  description?: string;
}

export interface TopologyEdge {
  from: string;
  to: string;
  label: string;
  status: string;
}

export interface TopologyResponse {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  updated_at: string;
}

// ── Sprint J: Digital Identity + Sandbox + Workspace Types ────────────

export interface AgentIdentity {
  id: string;
  agent_id: string;
  tenant_id: string;
  email: string;
  ssh_pubkey: string;
  ssh_key_type: string;
  gpg_key: string;
  oauth2_provider: string;
  oauth2_creds: string;
  status: 'pending' | 'active' | 'suspended' | 'revoked';
  created_at: string;
  updated_at: string;
}

export interface SandboxContainer {
  id: string;
  agent_id: string;
  tenant_id: string;
  container_name: string;
  image: string;
  status: 'created' | 'starting' | 'running' | 'stopped' | 'failed' | 'destroyed';
  cpu_limit: number;
  memory_mb: number;
  disk_mb: number;
  network_allow: string[];
  workspace_path: string;
  seccomp_profile: string;
  started_at?: string;
  stopped_at?: string;
  idle_timeout_s: number;
  max_runtime_s: number;
  created_at: string;
  updated_at: string;
}

export interface SandboxExecLog {
  id: string;
  container_id: string;
  agent_id: string;
  tenant_id: string;
  command: string;
  exit_code: number;
  stdout: string;
  stderr: string;
  duration_ms: number;
  executed_at: string;
}

export interface SandboxStats {
  total_containers: number;
  active_containers: number;
  by_status: Record<string, number>;
  total_execs: number;
  avg_duration_ms: number;
}

export interface WorkspaceTab {
  id: string;
  type: 'code' | 'document' | 'canvas' | 'data' | 'collaboration';
  label: string;
  path?: string;
  content?: string;
  language?: string;
  isDirty?: boolean;
}

export interface WorkspaceOpEvent {
  id: string;
  agent_id: string;
  operation: string;
  target: string;
  detail: string;
  timestamp: string;
}

// Mission Control decision contracts
export type DecisionResolution = 'RETRY_WORK_UNIT' | 'FAIL_MISSION';

export interface MissionDecision {
  id: string;
  missionId: string;
  workUnitId: string;
  attempt: number;
  contextDigest: string;
  reasonCode:
    | 'no_applicable_policy'
    | 'ambiguous_policy'
    | 'invalid_configuration'
    | 'unsupported_evaluator'
    | 'artifact_requirements_not_met';
  criterionIds: string[];
  options: DecisionResolution[];
  recommendedOption: DecisionResolution;
  riskSummary: string;
  status: 'PENDING' | 'RESOLVED' | 'CANCELLED' | 'EXPIRED';
  version: number;
  requestedBy: { type: string; id: string; displayName?: string };
  requestedAt: string;
  expiresAt?: string;
  resolution?: DecisionResolution;
  rationale?: string;
  resolvedBy?: { type: string; id: string; displayName?: string };
  resolvedAt?: string;
}

export interface DecisionListResponse {
  decisions: MissionDecision[];
}

export interface DecisionResolutionResponse {
  decision: MissionDecision;
  workUnit: Record<string, unknown>;
  mission: Record<string, unknown>;
}

// ═══════════════════════════════════════════════════════════════════════
// MCP Dashboard Types
// ═══════════════════════════════════════════════════════════════════════

/** Response from GET /api/admin/mcp/dashboard */
export interface MCPDashboardData {
  timestamp: string;
  health: {
    status: 'healthy' | 'degraded';
    issues: string[];
  };
  agents: {
    total: number;
    byStatus: Record<string, {
      count: number;
      adapters: Record<string, number>;
    }>;
  };
  sessions: {
    activeWebSocket: number;
    today: number;
  };
  messages: {
    today: number;
    thisWeek: number;
  };
  tokens: {
    today: number;
    thisWeek: number;
    perModel: Array<{ model: string; messages: number; tokens: number }>;
  };
  tools: {
    todayCalls: number;
    todaySuccess: number;
    topTools: Array<{ name: string; count: number; successCount: number }>;
  };
  performance: Record<string, unknown>;
  system: {
    cpuPercent: number;
    memoryPercent: number;
    memoryUsedGB: number;
    memoryTotalGB: number;
    pythonPid: number;
    note?: string;
  };
  database: {
    connected: boolean;
    poolSize: number;
    poolFree: number;
  };
  recentEvents: Array<{
    id?: string;
    agentId?: string;
    action?: string;
    riskLevel?: string;
    decision?: string;
    timestamp?: string;
  }>;
}
