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

  // Demo mode
  demoMode: boolean;

  // Actions
  loadAgents: () => Promise<void>;
  loadSelfCard: () => Promise<void>;
  discoverAgents: (capabilities: string[]) => Promise<void>;
  registerAgent: (url: string) => Promise<void>;
  unregisterAgent: (url: string) => Promise<void>;
  sendTask: (agentUrl: string, message: string) => Promise<void>;
  selectAgent: (url: string | null) => void;
}

// Demo data for when the API is unavailable
const DEMO_AGENTS: A2AAgentCard[] = [
  {
    protocolVersion: '1.0',
    name: 'AgentHub Platform',
    description: 'Enterprise self-hosted multi-agent collaboration platform',
    url: 'http://localhost:8081',
    version: '5.1.0',
    provider: { name: 'AgentHub', organization: 'AgentHub Community' },
    capabilities: { streaming: true, pushNotifications: true, stateTransitionHistory: true, multimodal: true, codeExecution: true },
    skills: [
      { id: 'knowledge_search', name: 'Knowledge Search', tags: ['rag', 'search', 'knowledge'] },
      { id: 'agent_orchestration', name: 'Agent Orchestration', tags: ['orchestration', 'multi-agent', 'dag'] },
      { id: 'code_generation', name: 'Code Generation', tags: ['code', 'generation', 'review'] },
    ],
    endpoints: { taskApi: 'http://localhost:8081/platform/a2a/tasks' },
    source: 'internal',
    status: 'active',
    tags: ['agenthub', 'platform'],
  },
  {
    protocolVersion: '1.0',
    name: 'Code Review Bot',
    description: 'Specialized code review agent with security analysis',
    url: 'http://localhost:8090',
    capabilities: { streaming: true, pushNotifications: false, stateTransitionHistory: false, codeExecution: true },
    skills: [
      { id: 'code_review', name: 'Code Review', tags: ['code', 'review', 'security'] },
      { id: 'diff_analysis', name: 'Diff Analysis', tags: ['diff', 'analysis'] },
    ],
    endpoints: { taskApi: 'http://localhost:8090/a2a/tasks' },
    source: 'external',
    status: 'active',
    tags: ['code', 'review'],
  },
  {
    protocolVersion: '1.0',
    name: 'Data Analyzer',
    description: 'Data analysis and visualization agent',
    url: 'http://localhost:8091',
    capabilities: { streaming: false, pushNotifications: false, stateTransitionHistory: false, multimodal: true },
    skills: [
      { id: 'data_analysis', name: 'Data Analysis', tags: ['data', 'analysis', 'visualization'] },
      { id: 'report_generation', name: 'Report Generation', tags: ['report', 'document'] },
    ],
    endpoints: { taskApi: 'http://localhost:8091/a2a/tasks' },
    source: 'external',
    status: 'active',
    tags: ['data', 'analytics'],
  },
];

export const useA2AStore = create<A2AState>()((set, get) => ({
  agents: [],
  selfCard: null,
  discoveryResults: [],
  selectedAgentUrl: null,
  taskResult: null,
  loading: false,
  discoveryLoading: false,
  taskLoading: false,
  demoMode: false,

  loadAgents: async () => {
    set({ loading: true });
    try {
      const res = await api('/registry');
      const data = await res.json();
      if (res.ok) {
        set({ agents: data.agents || [], demoMode: false });
      } else {
        set({ agents: DEMO_AGENTS, demoMode: true });
      }
    } catch {
      set({ agents: DEMO_AGENTS, demoMode: true });
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
      }
    } catch { /* use demo from loadAgents */ }
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
        // Demo: filter by capability
        const results = DEMO_AGENTS.filter((a) =>
          a.skills.some((s) => capabilities.some((c) => s.tags.some((t) => t.toLowerCase().includes(c.toLowerCase()))))
        );
        set({ discoveryResults: results.length > 0 ? results : DEMO_AGENTS });
      }
    } catch {
      set({ discoveryResults: DEMO_AGENTS });
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
      }
    } catch {
      useAdminStore.getState().setNotice('注销失败 (Demo 模式)');
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
            message: {
              role: 'user',
              parts: [{ type: 'text', text: message }],
            },
          },
        }),
      });
      const data = await res.json();
      if (res.ok) {
        set({ taskResult: data.result || data });
        useAdminStore.getState().setNotice('A2A 任务已发送');
      }
    } catch {
      set({ taskResult: { id: `task-demo-${Date.now()}`, status: 'working', createdAt: new Date().toISOString() } });
      useAdminStore.getState().setNotice('任务已发送 (Demo 模式)');
    } finally {
      set({ taskLoading: false });
    }
  },

  selectAgent: (url) => set({ selectedAgentUrl: url }),
}));
