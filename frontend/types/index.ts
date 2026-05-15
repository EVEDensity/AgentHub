export interface Message {
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
}

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
  riskLevel: string;
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
  name: string;
  status: 'PENDING' | 'RUNNING' | 'SUCCESS' | 'FAILED';
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
}
