import { useAuthStore } from '../stores/authStore';
import type { WorkflowDocument, WorkflowValidationResult } from './workflowContract';
import { normalizeWorkflowDocument } from './workflowContract';

export interface WorkflowDraftResponse {
  draftKey: string;
  workflowId?: number | null;
  baseVersion: number;
  draftVersion: number;
  createdAt: string;
  updatedAt: string;
  payload: WorkflowDocument;
  validation: WorkflowValidationResult;
}

export class WorkflowApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(readErrorMessage(detail));
  }
}

function headers(): Record<string, string> {
  return { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() };
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, headers: { ...headers(), ...(init?.headers || {}) } });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new WorkflowApiError(response.status, body?.detail ?? body);
  return body as T;
}

export const workflowClient = {
  async getWorkflow(id: number): Promise<WorkflowDocument> {
    return normalizeWorkflowDocument(await request<WorkflowDocument>(`/api/admin/workflows/${id}`));
  },

  async getDraft(key: string): Promise<WorkflowDraftResponse | null> {
    try {
      const draft = await request<WorkflowDraftResponse>(`/api/admin/workflows/drafts/${encodeURIComponent(key)}`);
      return { ...draft, payload: normalizeWorkflowDocument(draft.payload) };
    } catch (error) {
      if (error instanceof WorkflowApiError && error.status === 404) return null;
      throw error;
    }
  },

  saveDraft(key: string, document: WorkflowDocument, baseVersion: number, draftVersion: number) {
    return request<WorkflowDraftResponse>(`/api/admin/workflows/drafts/${encodeURIComponent(key)}`, {
      method: 'PUT',
      body: JSON.stringify({
        ...document,
        workflowId: document.id ?? null,
        baseVersion,
        draftVersion,
      }),
    });
  },

  deleteDraft(key: string): Promise<void> {
    return request(`/api/admin/workflows/drafts/${encodeURIComponent(key)}`, { method: 'DELETE' });
  },

  validate(document: WorkflowDocument): Promise<WorkflowValidationResult> {
    return request('/api/admin/workflows/validate', {
      method: 'POST',
      body: JSON.stringify({
        nodes: document.nodes,
        edges: document.edges,
        schemaVersion: document.schemaVersion,
      }),
    });
  },

  async publish(document: WorkflowDocument, expectedVersion = document.version): Promise<WorkflowDocument> {
    const updating = Boolean(document.id);
    const result = await request<{ route: WorkflowDocument }>(
      updating ? `/api/admin/workflows/${document.id}` : '/api/admin/workflows',
      {
        method: updating ? 'PUT' : 'POST',
        body: JSON.stringify({ ...document, version: expectedVersion }),
      },
    );
    return normalizeWorkflowDocument(result.route);
  },
};

export function readConflictDetail(detail: unknown): { expectedVersion: number; currentVersion: number } | null {
  if (!detail || typeof detail !== 'object') return null;
  const value = detail as Record<string, unknown>;
  if (typeof value.expectedVersion !== 'number' || typeof value.currentVersion !== 'number') return null;
  return { expectedVersion: value.expectedVersion, currentVersion: value.currentVersion };
}

function readErrorMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).message === 'string') {
    return (detail as Record<string, string>).message;
  }
  return '工作流请求失败';
}
