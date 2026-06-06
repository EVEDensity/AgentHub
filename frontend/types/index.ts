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
  type: 'text' | 'code' | 'system' | 'diff' | 'tool_call' | 'tool_result' | 'agent_question' | 'progress_update' | 'risk_warning' | 'agent_todo' | 'task_preview' | 'terminal';
  timestamp: string;
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
  /** Diff accept/reject */
  diffDecisionState?: 'pending' | 'accepted' | 'rejected';
  diffFilePath?: string;
}

export interface StreamChunk {
  event: 'message_chunk';
  messageId: string;
  sessionId: string;
  content: string;
  isFinal: boolean;
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
  name: string;
  role: string;
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
  createdAt?: string;
  isPinned?: number;
  lastMessageAt?: string;
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
  toolCalls: ToolCallItem[];
  timestamp: string;
}

export interface ToolResultEvent {
  event: 'tool_result';
  sessionId: string;
  messageId: string;
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
  diffOld?: string;
  diffNew?: string;
  status?: WorkspaceFileStatus;
}

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

// ── CloudCode: diff events ───────────────────────────────────────

/** Real-time diff update from CloudCode agent (edit_file tool use) */
export interface DiffUpdateEvent {
  event: 'diff_update';
  sessionId: string;
  messageId: string;
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
