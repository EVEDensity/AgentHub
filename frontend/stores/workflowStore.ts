import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { useAdminStore } from './adminStore';

// ── Types ────────────────────────────────────────────────────────

export interface FlowNode {
  id: string;
  type: 'start' | 'agent' | 'tool' | 'ifelse' | 'end' | 'code' | 'http' | 'knowledge' | 'human';
  name: string;
  description: string;
  x: number;
  y: number;
  agent?: string;
  layer?: string;
  dependencies: string[];
  // P1-4: Node-specific configs
  codeConfig?: { language?: string; code?: string; timeout?: number };
  httpConfig?: { method?: string; url?: string; headers?: string; body?: string; timeout?: number; retry?: number };
  knowledgeConfig?: { collectionId?: string; query?: string; topK?: number; scoreThreshold?: number };
  humanConfig?: { prompt?: string; assignee?: string; timeout?: number };
}

export interface FlowEdge {
  from: string;
  to: string;
  label?: string;
}

export interface WorkflowData {
  id?: number;
  name: string;
  description: string;
  triggerKeywords: string[];
  nodes: FlowNode[];
  edges: FlowEdge[];
  isDefault: boolean;
  active: boolean;
}

export interface WorkflowListItem {
  id: number;
  name: string;
  description: string;
  triggerKeywords: string[];
  nodes: FlowNode[];
  edges: FlowEdge[];
  isDefault: boolean;
  active: boolean;
  createdAt: string;
  updatedAt: string;
}

interface WorkflowState {
  workflows: WorkflowListItem[];
  loading: boolean;
  error: string;

  loadWorkflows: () => Promise<void>;
  getWorkflow: (id: number) => Promise<WorkflowData | null>;
  createWorkflow: (data: WorkflowData) => Promise<WorkflowListItem | null>;
  updateWorkflow: (id: number, data: WorkflowData) => Promise<WorkflowListItem | null>;
  deleteWorkflow: (id: number) => Promise<boolean>;
  setDefault: (id: number) => Promise<boolean>;
  toggleActive: (id: number, active: boolean) => Promise<boolean>;
}

// ── Helpers ──────────────────────────────────────────────────────

function apiHeaders(): Record<string, string> {
  const auth = useAuthStore.getState().authHeaders();
  return { 'Content-Type': 'application/json', ...auth };
}

function fmtErr(detail: unknown, fallback: string): string {
  return useAuthStore.getState().fmtErr(detail, fallback);
}

// ── Store ────────────────────────────────────────────────────────

export const useWorkflowStore = create<WorkflowState>()((set, get) => ({
  workflows: [],
  loading: false,
  error: '',

  loadWorkflows: async () => {
    set({ loading: true, error: '' });
    try {
      const res = await fetch('/api/admin/workflows', { headers: apiHeaders() });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(fmtErr((data as { detail?: string }).detail, '加载工作流列表失败'));
      }
      const workflows = (await res.json()) as WorkflowListItem[];
      set({ workflows, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  getWorkflow: async (id: number) => {
    try {
      // Load all workflows and find the one we need
      // (backend doesn't have a single-GET endpoint, so we list and filter)
      const res = await fetch('/api/admin/workflows', { headers: apiHeaders() });
      if (!res.ok) return null;
      const list = (await res.json()) as WorkflowListItem[];
      const found = list.find((w) => w.id === id);
      if (!found) return null;

      return {
        id: found.id,
        name: found.name,
        description: found.description,
        triggerKeywords: found.triggerKeywords,
        nodes: found.nodes,
        edges: found.edges || found.nodes.flatMap((n) =>
          (n.dependencies || []).map((dep: string) => ({
            from: dep,
            to: n.id,
            label: '',
          }))
        ),
        isDefault: found.isDefault,
        active: found.active,
      };
    } catch {
      return null;
    }
  },

  createWorkflow: async (data) => {
    try {
      // Convert edges to node dependencies
      const deps = new Map<string, string[]>();
      (data.edges || []).forEach((e) => {
        const a = deps.get(e.to) || [];
        a.push(e.from);
        deps.set(e.to, a);
      });

      const nodes = data.nodes.map((n) => ({
        ...n,
        dependencies: deps.get(n.id) || n.dependencies || [],
      }));

      const res = await fetch('/api/admin/workflows', {
        method: 'POST',
        headers: apiHeaders(),
        body: JSON.stringify({
          name: data.name,
          description: data.description,
          triggerKeywords: data.triggerKeywords,
          nodes,
          isDefault: data.isDefault,
        }),
      });

      const result = await res.json();
      if (!res.ok) {
        useAdminStore.getState().setNotice(fmtErr((result as { detail?: string }).detail, '创建工作流失败'));
        return null;
      }

      useAdminStore.getState().setNotice(`已创建工作流：${data.name}`);
      await get().loadWorkflows();
      return (result as { route: WorkflowListItem }).route;
    } catch (e) {
      useAdminStore.getState().setNotice(`创建失败：${(e as Error).message}`);
      return null;
    }
  },

  updateWorkflow: async (id, data) => {
    try {
      const deps = new Map<string, string[]>();
      (data.edges || []).forEach((e) => {
        const a = deps.get(e.to) || [];
        a.push(e.from);
        deps.set(e.to, a);
      });

      const nodes = data.nodes.map((n) => ({
        ...n,
        dependencies: deps.get(n.id) || n.dependencies || [],
      }));

      const res = await fetch(`/api/admin/workflows/${id}`, {
        method: 'PUT',
        headers: apiHeaders(),
        body: JSON.stringify({
          name: data.name,
          description: data.description,
          triggerKeywords: data.triggerKeywords,
          nodes,
          isDefault: data.isDefault,
        }),
      });

      const result = await res.json();
      if (!res.ok) {
        useAdminStore.getState().setNotice(fmtErr((result as { detail?: string }).detail, '更新工作流失败'));
        return null;
      }

      useAdminStore.getState().setNotice(`已更新工作流：${data.name}`);
      await get().loadWorkflows();
      return (result as { route: WorkflowListItem }).route;
    } catch (e) {
      useAdminStore.getState().setNotice(`更新失败：${(e as Error).message}`);
      return null;
    }
  },

  deleteWorkflow: async (id) => {
    try {
      const res = await fetch(`/api/admin/workflows/${id}`, {
        method: 'DELETE',
        headers: apiHeaders(),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        useAdminStore.getState().setNotice(fmtErr((data as { detail?: string }).detail, '删除工作流失败'));
        return false;
      }

      useAdminStore.getState().setNotice('已删除工作流');
      await get().loadWorkflows();
      return true;
    } catch (e) {
      useAdminStore.getState().setNotice(`删除失败：${(e as Error).message}`);
      return false;
    }
  },

  setDefault: async (id) => {
    try {
      const res = await fetch(`/api/admin/workflows/${id}/default`, {
        method: 'POST',
        headers: apiHeaders(),
      });

      if (!res.ok) return false;

      useAdminStore.getState().setNotice('已设为默认工作流');
      await get().loadWorkflows();
      return true;
    } catch {
      useAdminStore.getState().setNotice('设置默认失败');
      return false;
    }
  },

  toggleActive: async (id, active) => {
    try {
      const res = await fetch(`/api/admin/workflows/${id}/active`, {
        method: 'PATCH',
        headers: apiHeaders(),
        body: JSON.stringify({ active }),
      });

      if (!res.ok) return false;

      useAdminStore.getState().setNotice(active ? '工作流已启用' : '工作流已禁用');
      await get().loadWorkflows();
      return true;
    } catch {
      useAdminStore.getState().setNotice('切换状态失败');
      return false;
    }
  },
}));
