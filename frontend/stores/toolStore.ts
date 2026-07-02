'use client';

import { create } from 'zustand';

// ── Types ────────────────────────────────────────────────────────

export interface ToolParam {
  name: string;
  type: string;
  required: boolean;
  description: string;
  default?: string;
  enum?: string[];
}

export interface ToolDefinition {
  id: string;
  name: string;
  description: string;
  category: string;
  icon?: string;
  parameters: ToolParam[];
  returnType: string;
  examples: { user_question: string; parameters: Record<string, unknown> }[];
  riskLevel: 'L1' | 'L2' | 'L3';
  handlerType: 'builtin' | 'custom' | 'community';
  enabled: boolean;
  isConcurrencySafe: boolean;
  requiresUserConfirmation: boolean;
  createdAt: string;
  // UI state
  installed?: boolean;
}

export interface ToolBinding {
  agentId: string;
  toolIds: string[];
}

export interface ApiKeyInfo {
  id: string;
  name: string;
  keyPrefix: string;
  scopes: string[];
  rateLimit: number;
  createdAt: string;
  lastUsedAt?: string;
  enabled: boolean;
}

// ── Categories ───────────────────────────────────────────────────

export const TOOL_CATEGORIES: Record<string, { label: string; icon: string }> = {
  search: { label: '搜索', icon: 'search' },
  file: { label: '文件', icon: 'description' },
  code: { label: '代码', icon: 'code' },
  memory: { label: '记忆', icon: 'psychology' },
  integration: { label: '集成', icon: 'link' },
  system: { label: '系统', icon: 'settings' },
  browser: { label: '浏览器', icon: 'language' },
  ai: { label: 'AI', icon: 'smart_toy' },
  notification: { label: '通知', icon: 'notifications' },
  data: { label: '数据', icon: 'database' },
};

// ── Store ────────────────────────────────────────────────────────

interface ToolStoreState {
  // Tool definitions
  tools: ToolDefinition[];
  loading: boolean;
  error: string | null;

  // Agent bindings
  agentBindings: Record<string, string[]>; // agentId → toolIds

  // API Keys
  apiKeys: ApiKeyInfo[];
  apiKeysLoading: boolean;

  // Filters
  searchQuery: string;
  selectedCategory: string | null;

  // Actions — Tools
  loadTools: () => Promise<void>;
  createTool: (tool: Partial<ToolDefinition>) => Promise<ToolDefinition | null>;
  updateTool: (id: string, updates: Partial<ToolDefinition>) => Promise<void>;
  deleteTool: (id: string) => Promise<void>;
  toggleToolEnabled: (id: string) => Promise<void>;

  // Actions — Bindings
  loadAgentBindings: (agentId: string) => Promise<void>;
  updateAgentBindings: (agentId: string, toolIds: string[]) => Promise<void>;

  // Actions — API Keys
  loadApiKeys: () => Promise<void>;
  createApiKey: (name: string, scopes: string[], rateLimit: number) => Promise<ApiKeyInfo | null>;
  revokeApiKey: (id: string) => Promise<void>;

  // Actions — Import
  importFromSwagger: (spec: object) => Promise<ToolDefinition[]>;

  // Filters
  setSearchQuery: (q: string) => void;
  setSelectedCategory: (c: string | null) => void;

  // Computed
  filteredTools: () => ToolDefinition[];
  toolsByCategory: () => Record<string, ToolDefinition[]>;
}

export const useToolStore = create<ToolStoreState>((set, get) => ({
  tools: [],
  loading: false,
  error: null,
  agentBindings: {},
  apiKeys: [],
  apiKeysLoading: false,
  searchQuery: '',
  selectedCategory: null,

  // ── Tools CRUD ──────────────────────────────────────────────────

  loadTools: async () => {
    set({ loading: true, error: null });
    try {
      const res = await fetch('/api/admin/tools');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const tools: ToolDefinition[] = (data.tools || data || []).map((t: Record<string, unknown>) => ({
        ...t,
        installed: true,
        icon: t.icon || TOOL_CATEGORIES[t.category as string]?.icon || 'build',
      }));
      set({ tools, loading: false });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  createTool: async (tool) => {
    try {
      const res = await fetch('/api/admin/tools', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(tool),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const created = await res.json();
      await get().loadTools();
      return created as ToolDefinition;
    } catch (err) {
      set({ error: (err as Error).message });
      return null;
    }
  },

  updateTool: async (id, updates) => {
    try {
      await fetch(`/api/admin/tools/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      });
      await get().loadTools();
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  deleteTool: async (id) => {
    try {
      await fetch(`/api/admin/tools/${id}`, { method: 'DELETE' });
      await get().loadTools();
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  toggleToolEnabled: async (id) => {
    const tool = get().tools.find((t) => t.id === id);
    if (!tool) return;
    await get().updateTool(id, { enabled: !tool.enabled } as Partial<ToolDefinition>);
  },

  // ── Bindings ────────────────────────────────────────────────────

  loadAgentBindings: async (agentId) => {
    try {
      const res = await fetch(`/api/admin/tools/bindings/${agentId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      set((s) => ({
        agentBindings: { ...s.agentBindings, [agentId]: data.tool_ids || data.tools || [] },
      }));
    } catch { /* ignore */ }
  },

  updateAgentBindings: async (agentId, toolIds) => {
    try {
      await fetch(`/api/admin/tools/bindings/${agentId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_id: agentId, tool_ids: toolIds }),
      });
      set((s) => ({
        agentBindings: { ...s.agentBindings, [agentId]: toolIds },
      }));
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  // ── API Keys ────────────────────────────────────────────────────

  loadApiKeys: async () => {
    set({ apiKeysLoading: true });
    try {
      const res = await fetch('/platform/api-keys');
      const data = await res.json();
      set({ apiKeys: data.keys || [], apiKeysLoading: false });
    } catch {
      set({ apiKeysLoading: false });
    }
  },

  createApiKey: async (name, scopes, rateLimit) => {
    try {
      const res = await fetch('/platform/api-keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, scopes, rate_limit: rateLimit }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      await get().loadApiKeys();
      return data as ApiKeyInfo;
    } catch (err) {
      set({ error: (err as Error).message });
      return null;
    }
  },

  revokeApiKey: async (id) => {
    try {
      await fetch(`/platform/api-keys/${id}`, { method: 'DELETE' });
      await get().loadApiKeys();
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  // ── Swagger Import ──────────────────────────────────────────────

  importFromSwagger: async (spec) => {
    const discovered: ToolDefinition[] = [];
    const paths = (spec as Record<string, unknown>).paths || {};
    for (const [path, methods] of Object.entries(paths as Record<string, Record<string, unknown>>)) {
      for (const [method, op] of Object.entries(methods)) {
        if (['get', 'post', 'put', 'delete', 'patch'].includes(method)) {
          const opDetail = op as Record<string, unknown>;
          const toolName = `${method.toUpperCase()} ${path}`
            .replace(/[{}]/g, '')
            .replace(/[/:]/g, '_')
            .replace(/^_|_$/g, '');
          const params: ToolParam[] = [];
          const paramList = (opDetail.parameters || []) as Record<string, unknown>[];
          for (const p of paramList) {
            params.push({
              name: (p.name || '') as string,
              type: ((p.schema as Record<string, unknown>)?.type || 'string') as string,
              required: !!p.required,
              description: (p.description || '') as string,
            });
          }
          discovered.push({
            id: `swagger-${toolName}`,
            name: (opDetail.summary || opDetail.operationId || toolName) as string,
            description: (opDetail.description || `${method.toUpperCase()} ${path}`) as string,
            category: 'integration',
            icon: 'link',
            parameters: params,
            returnType: 'object',
            examples: [],
            riskLevel: method === 'get' ? 'L1' : 'L2',
            handlerType: 'custom',
            enabled: false,
            isConcurrencySafe: method === 'get',
            requiresUserConfirmation: method !== 'get',
            createdAt: new Date().toISOString(),
          });
        }
      }
    }
    return discovered;
  },

  // ── Filters ─────────────────────────────────────────────────────

  setSearchQuery: (q) => set({ searchQuery: q }),
  setSelectedCategory: (c) => set({ selectedCategory: c }),

  filteredTools: () => {
    const { tools, searchQuery, selectedCategory } = get();
    let result = tools;
    if (selectedCategory) {
      result = result.filter((t) => t.category === selectedCategory);
    }
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (t) =>
          t.name.toLowerCase().includes(q) ||
          t.description.toLowerCase().includes(q) ||
          t.category.toLowerCase().includes(q)
      );
    }
    return result;
  },

  toolsByCategory: () => {
    const grouped: Record<string, ToolDefinition[]> = {};
    for (const tool of get().tools) {
      const cat = tool.category || 'other';
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push(tool);
    }
    return grouped;
  },
}));
