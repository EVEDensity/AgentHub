import { create } from 'zustand';
import type { Agent } from '../types';
import { useAuthStore } from './authStore';
import { useAdminStore } from './adminStore';

interface AdapterOption {
  id: string; name: string; description: string;
  default_model: string; default_base_url: string;
  requires_api_key: boolean; category: string;
}

interface AgentFormState {
  agentId: string; domain: string; adapterType: string; baseModelName: string;
  rankLevel: string; dutyNote: string; displayName: string; avatarUrl: string;
  capabilityTags: string[]; baseUrl: string; apiKey: string;
}

const EMPTY_AGENT_FORM: AgentFormState = {
  agentId: '', domain: '', adapterType: 'deepseek', baseModelName: '',
  rankLevel: 'L1', dutyNote: '', displayName: '', avatarUrl: '',
  capabilityTags: [], baseUrl: '', apiKey: '',
};

interface AgentState {
  // Data
  agents: Agent[];
  agentTests: Record<string, { status: 'checking' | 'success' | 'failed'; message: string }>;
  adapterOptions: AdapterOption[];
  selectedAdapterInfo: AdapterOption | null;
  editSelectedAdapterInfo: AdapterOption | null;
  defaultChatAgent: string;
  // UI flags
  isCreatingAgent: boolean;
  showLocalAgentModal: boolean;
  editingAgentId: string | null;
  // Forms
  newAgent: AgentFormState;
  editAgent: AgentFormState;

  // Data actions
  fetchAdapters: () => Promise<void>;
  refresh: () => Promise<void>;
  createAgent: (e: React.FormEvent<HTMLFormElement>) => Promise<void>;
  testAgent: (agentId: string) => Promise<void>;
  removeAgent: (agentId: string) => Promise<void>;
  saveAgentEdit: (e: React.FormEvent<HTMLFormElement>) => Promise<void>;
  handleSetDefaultChatAgent: (agentId: string) => Promise<void>;

  // UI actions
  handleAdapterChange: (value: string, mode: 'create' | 'edit') => void;
  startEditAgent: (agent: Agent) => void;
  cancelEditAgent: () => void;

  // Form setters
  setNewAgent: (updater: AgentFormState | ((prev: AgentFormState) => AgentFormState)) => void;
  setEditAgent: (updater: AgentFormState | ((prev: AgentFormState) => AgentFormState)) => void;
  setIsCreatingAgent: (v: boolean) => void;
  setShowLocalAgentModal: (v: boolean) => void;
  setEditingAgentId: (v: string | null) => void;
  setSelectedAdapterInfo: (v: AdapterOption | null) => void;
  setEditSelectedAdapterInfo: (v: AdapterOption | null) => void;
}

export const useAgentStore = create<AgentState>()((set, get) => ({
  agents: [],
  agentTests: {},
  adapterOptions: [],
  selectedAdapterInfo: null,
  editSelectedAdapterInfo: null,
  defaultChatAgent: 'Orchestrator',
  isCreatingAgent: false,
  showLocalAgentModal: false,
  editingAgentId: null,
  newAgent: { ...EMPTY_AGENT_FORM },
  editAgent: { ...EMPTY_AGENT_FORM },

  // ── Data actions ──────────────────────────────────────────────

  fetchAdapters: async () => {
    const res = await fetch('/api/adapters', { headers: useAuthStore.getState().authHeaders() });
    if (res.ok) {
      const data = await res.json() as { adapters: AdapterOption[] };
      set({ adapterOptions: data.adapters });
    }
  },

  refresh: async () => {
    const [a, d] = await Promise.all([
      fetch('/api/agent/registry', { headers: useAuthStore.getState().authHeaders() }),
      fetch('/api/admin/chat-defaults', { headers: useAuthStore.getState().authHeaders() }),
    ]);
    // /api/agent/registry returns a plain JSON array (list[dict])
    if (a.ok) set({ agents: (await a.json() as Agent[]) || [] });
    // /api/admin/chat-defaults returns { agentId: string }
    if (d.ok) set({ defaultChatAgent: ((await d.json()) as { agentId: string }).agentId });
  },

  createAgent: async (e) => {
    e.preventDefault();
    const { newAgent } = get();
    const { authHeaders, fmtErr } = useAuthStore.getState();
    const payload = { ...newAgent, rankLevel: newAgent.rankLevel };
    const res = await fetch('/api/agent/registry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    useAdminStore.getState().setNotice(
      res.ok ? `已添加服务商：${newAgent.agentId}` : fmtErr(data.detail, '添加失败')
    );
    if (res.ok) {
      set({ newAgent: { ...EMPTY_AGENT_FORM }, selectedAdapterInfo: null, isCreatingAgent: false });
      await get().refresh();
    }
  },

  testAgent: async (agentId) => {
    set((s) => ({
      agentTests: { ...s.agentTests, [agentId]: { status: 'checking', message: '检测中...' } },
    }));
    const res = await fetch(
      `/api/agent/registry/${encodeURIComponent(agentId)}/test`,
      { method: 'POST', headers: useAuthStore.getState().authHeaders() },
    );
    const data = await res.json();
    const ok = res.ok && data.status === 'success';
    set((s) => ({
      agentTests: {
        ...s.agentTests,
        [agentId]: { status: ok ? 'success' : 'failed', message: data.message || (ok ? '连接正常' : '连接失败') },
      },
    }));
    if (res.ok) await get().refresh();
  },

  removeAgent: async (agentId) => {
    if (typeof window !== 'undefined' && !window.confirm(`确认删除服务商 ${agentId}？`)) return;
    try {
      const res = await fetch(
        `/api/agent/registry/${encodeURIComponent(agentId)}`,
        { method: 'DELETE', headers: useAuthStore.getState().authHeaders() },
      );
      const data = await res.json().catch(() => ({}));
      const { fmtErr } = useAuthStore.getState();
      useAdminStore.getState().setNotice(
        res.ok ? `已删除：${agentId}` : fmtErr((data as { detail?: string }).detail, '删除失败')
      );
      if (res.ok) {
        if (get().editingAgentId === agentId) get().cancelEditAgent();
        await get().refresh();
      }
    } catch {
      useAdminStore.getState().setNotice('删除失败，请检查网络或登录状态');
    }
  },

  saveAgentEdit: async (e) => {
    e.preventDefault();
    const { editingAgentId, editAgent } = get();
    if (!editingAgentId) return;
    try {
      const res = await fetch(
        `/api/agent/registry/${encodeURIComponent(editingAgentId)}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
          body: JSON.stringify(editAgent),
        },
      );
      const data = await res.json();
      const { fmtErr } = useAuthStore.getState();
      useAdminStore.getState().setNotice(
        res.ok ? `已更新服务商：${editingAgentId}` : fmtErr(data.detail, '更新失败')
      );
      if (res.ok) {
        get().cancelEditAgent();
        await get().refresh();
      }
    } catch {
      useAdminStore.getState().setNotice('保存失败：网络连接异常，请检查后端服务是否运行');
    }
  },

  handleSetDefaultChatAgent: async (agentId) => {
    const res = await fetch('/api/admin/chat-defaults', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
      body: JSON.stringify({ agentId }),
    });
    const data = await res.json();
    const { fmtErr } = useAuthStore.getState();
    if (res.ok) {
      set({ defaultChatAgent: agentId });
      useAdminStore.getState().setNotice(
        `已将 ${agentId} 设为默认对话模型。不含 @Agent 指令的日常对话将默认使用该模型。`
      );
    } else {
      useAdminStore.getState().setNotice(fmtErr((data as { detail?: string }).detail, '设置失败'));
    }
  },

  // ── UI actions ─────────────────────────────────────────────────

  handleAdapterChange: (value, mode) => {
    const adapter = get().adapterOptions.find((a) => a.id === value) || null;
    if (mode === 'create') {
      set((s) => ({
        newAgent: {
          ...s.newAgent, adapterType: value,
          baseModelName: adapter ? adapter.default_model : s.newAgent.baseModelName,
          baseUrl: adapter ? adapter.default_base_url : s.newAgent.baseUrl,
        },
        selectedAdapterInfo: adapter,
      }));
    } else {
      set((s) => ({
        editAgent: {
          ...s.editAgent, adapterType: value,
          baseModelName: adapter ? adapter.default_model : s.editAgent.baseModelName,
          baseUrl: adapter ? adapter.default_base_url : s.editAgent.baseUrl,
        },
        editSelectedAdapterInfo: adapter,
      }));
    }
  },

  startEditAgent: (agent) => {
    const adapter = get().adapterOptions.find((a) => a.id === agent.adapterType) || null;
    set({
      editingAgentId: agent.agentId,
      editSelectedAdapterInfo: adapter,
      editAgent: {
        agentId: agent.agentId, domain: agent.domain,
        adapterType: agent.adapterType, baseModelName: agent.baseModelName || '',
        rankLevel: agent.rankLevel || 'L1', dutyNote: agent.dutyNote || '',
        displayName: agent.displayName || '', avatarUrl: agent.avatarUrl || '',
        capabilityTags: agent.capabilityTags || [], baseUrl: agent.baseUrl || '', apiKey: '',
      },
    });
  },

  cancelEditAgent: () => {
    set({
      editingAgentId: null, editSelectedAdapterInfo: null,
      editAgent: { ...EMPTY_AGENT_FORM },
    });
  },

  // ── Form setters ───────────────────────────────────────────────

  setNewAgent: (updater) => set((s) => ({
    newAgent: typeof updater === 'function' ? updater(s.newAgent) : updater,
  })),
  setEditAgent: (updater) => set((s) => ({
    editAgent: typeof updater === 'function' ? updater(s.editAgent) : updater,
  })),
  setIsCreatingAgent: (v) => set({ isCreatingAgent: v }),
  setShowLocalAgentModal: (v) => set({ showLocalAgentModal: v }),
  setEditingAgentId: (v) => set({ editingAgentId: v }),
  setSelectedAdapterInfo: (v) => set({ selectedAdapterInfo: v }),
  setEditSelectedAdapterInfo: (v) => set({ editSelectedAdapterInfo: v }),
}));
