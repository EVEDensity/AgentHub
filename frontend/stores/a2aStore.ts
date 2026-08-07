// A2A (Agent-to-Agent) Protocol Store (P2-2)
// Manages A2A agent cards, discovery, and task execution.

import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { useAdminStore } from './adminStore';
import type { A2AAgentCard, A2ADiscoveryResponse } from '../types';

const BASE = '/platform/a2a';

async function api(path: string, options: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
    ...options,
  });
  return res;
}

interface A2AState {
  // Data
  agents: A2AAgentCard[];
  selfCard: A2AAgentCard | null;
  discoveryResults: A2AAgentCard[];
  selectedAgentUrl: string | null;
  taskResult: Record<string, unknown> | null;

  // Loading
  loading: boolean;
  discoveryLoading: boolean;
  taskLoading: boolean;

  // Actions
  loadAgents: () => Promise<void>;
  loadSelfCard: () => Promise<void>;
  discoverAgents: (capabilities: string[]) => Promise<void>;
  registerAgent: (url: string) => Promise<void>;
  unregisterAgent: (url: string) => Promise<void>;
  sendTask: (agentUrl: string, message: string) => Promise<void>;
  selectAgent: (url: string | null) => void;
}

export const useA2AStore = create<A2AState>()((set, get) => ({
  agents: [],
  selfCard: null,
  discoveryResults: [],
  selectedAgentUrl: null,
  taskResult: null,
  loading: false,
  discoveryLoading: false,
  taskLoading: false,

  loadAgents: async () => {
    set({ loading: true });
    try {
      const res = await api('/registry');
      const data = await res.json();
      if (res.ok) {
        set({ agents: data.agents || [] });
      } else {
        set({ agents: [] });
        useAdminStore.getState().setNotice(data.error || 'A2A Agent 列表加载失败');
      }
    } catch {
      set({ agents: [] });
      useAdminStore.getState().setNotice('A2A 网关不可用，无法加载 Agent 列表');
    } finally {
      set({ loading: false });
    }
  },

  loadSelfCard: async () => {
    try {
      const res = await api('/card');
      const data = await res.json();
      if (res.ok) {
        set({ selfCard: data });
      } else {
        set({ selfCard: null });
        useAdminStore.getState().setNotice(data.error || 'AgentHub Agent Card 加载失败');
      }
    } catch {
      set({ selfCard: null });
      useAdminStore.getState().setNotice('A2A 网关不可用，无法加载 Agent Card');
    }
  },

  discoverAgents: async (capabilities: string[]) => {
    set({ discoveryLoading: true });
    try {
      const params = capabilities.map((c) => `capability=${encodeURIComponent(c)}`).join('&');
      const res = await api(`/discover?${params}`);
      const data: A2ADiscoveryResponse = await res.json();
      if (res.ok) {
        set({ discoveryResults: data.agents || [] });
      } else {
        set({ discoveryResults: [] });
        useAdminStore.getState().setNotice('A2A Agent 发现失败');
      }
    } catch {
      set({ discoveryResults: [] });
      useAdminStore.getState().setNotice('A2A 网关不可用，无法发现 Agent');
    } finally {
      set({ discoveryLoading: false });
    }
  },

  registerAgent: async (url: string) => {
    try {
      // Try to fetch the agent card from the remote URL
      const cardRes = await fetch(`${url}/.well-known/agent-card.json`);
      if (!cardRes.ok) throw new Error('Agent card not found');
      const card = await cardRes.json();

      const res = await api('/registry', {
        method: 'POST',
        body: JSON.stringify(card),
      });
      const data = await res.json();
      if (res.ok) {
        useAdminStore.getState().setNotice(`A2A Agent "${card.name}" 已注册`);
        await get().loadAgents();
      } else {
        useAdminStore.getState().setNotice(data.error || '注册失败');
      }
    } catch {
      useAdminStore.getState().setNotice('无法发现远程 Agent Card，请确认 URL 正确');
    }
  },

  unregisterAgent: async (url: string) => {
    try {
      const res = await api(`/registry?url=${encodeURIComponent(url)}`, { method: 'DELETE' });
      if (res.ok) {
        useAdminStore.getState().setNotice('A2A Agent 已注销');
        if (get().selectedAgentUrl === url) set({ selectedAgentUrl: null });
        await get().loadAgents();
      } else {
        useAdminStore.getState().setNotice('A2A Agent 注销失败');
      }
    } catch {
      useAdminStore.getState().setNotice('A2A 网关不可用，注销失败');
    }
  },

  sendTask: async (agentUrl: string, message: string) => {
    set({ taskLoading: true, taskResult: null });
    try {
      const res = await api('/tasks', {
        method: 'POST',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: Date.now(),
          method: 'tasks/send',
          params: {
            agentUrl,
            message: {
              role: 'user',
              parts: [{ type: 'text', text: message }],
            },
          },
        }),
      });
      const data = await res.json();
      if (res.ok && !data.error) {
        const result = data.result || data;
        set({ taskResult: result });
        if (result.status === 'failed') {
          useAdminStore.getState().setNotice('A2A Agent 执行任务失败');
        } else {
          useAdminStore.getState().setNotice('A2A 任务已发送');
        }
      } else {
        set({ taskResult: null });
        useAdminStore.getState().setNotice(data.error?.message || data.error || 'A2A 任务发送失败');
      }
    } catch {
      set({ taskResult: null });
      useAdminStore.getState().setNotice('A2A 网关不可用，任务未发送');
    } finally {
      set({ taskLoading: false });
    }
  },

  selectAgent: (url) => set({ selectedAgentUrl: url }),
}));
