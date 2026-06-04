export type FileCategory = 'code' | 'document' | 'image' | 'archive' | 'spreadsheet' | 'config' | 'unknown';

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
  type: 'text' | 'code' | 'system' | 'diff' | 'tool_call' | 'tool_result';
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
  baseUrl?: string;
}

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

export interface PendingMessage {
  sessionId: string;
  content: string;
  sender: string;
  timestamp: string;
  type: 'text' | 'code' | 'system' | 'diff';
  attachments?: AttachmentMeta[];
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
    description?: string;
    dependencies?: string[];
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
