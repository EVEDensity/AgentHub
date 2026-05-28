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

export interface Message {
  id?: string;
  event: string;
  sessionId: string;
  sender: string;
  content: string;
  type: 'text' | 'code' | 'system' | 'diff';
  timestamp: string;
  fidelityScore?: number;
  symbolic?: SymbolicData & {
    generated?: GeneratedData;
  };
  messageId?: string;
  isStreaming?: boolean;
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

export interface FidelityWarning {
  event: 'fidelity_warning';
  sessionId: string;
  agentId: string;
  fidelityScore: number;
  grade: 'warn' | 'low';
  message: string;
  timestamp: string;
}

export interface FidelityBlock {
  event: 'fidelity_block';
  sessionId: string;
  agentId: string;
  fidelityScore: number;
  grade: 'block';
  message: string;
  requiresHumanConfirm: boolean;
  timestamp: string;
}

export interface FidelityResolved {
  event: 'fidelity_resolved';
  sessionId: string;
  agentId: string;
  fidelityScore: number;
  message: string;
  timestamp: string;
}

export type FidelityEvent = FidelityWarning | FidelityBlock | FidelityResolved;

export interface SymbolicData {
  task_fingerprint_id: string;
  session_id: string;
  core_summary: string;
  extended_summaries: Array<{ id: string; text: string; vector_idx: string }>;
  key_params: Record<string, unknown>;
  knowledge_vector_idx: string[];
  confidence: number;
  fidelity_score: number;
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
