// Transport cache only: a failed gateway request never manufactures MCP state.
import { create } from 'zustand';
import type { MCPServerConfig, MCPToolInfo, MCPResourceInfo, MCPPromptInfo, MCPToolCallResult } from '../types';

const MCP_GATEWAY_URL = process.env.NEXT_PUBLIC_MCP_GATEWAY_URL || 'http://127.0.0.1:8099';
const serverPath = (id: string) => `/platform/mcp/servers/${encodeURIComponent(id)}`;
const message = (err: unknown) => err instanceof Error ? err.message : 'MCP 网关不可用';

async function request(path: string, init?: RequestInit) {
  const response = await fetch(`${MCP_GATEWAY_URL}${path}`, init);
  if (!response.ok) throw new Error(`MCP 请求失败 (HTTP ${response.status})`);
  if (response.status === 204) return null;
  return response.json();
}

export interface MCPStore {
  servers: MCPServerConfig[];
  selectedServerId: string | null;
  tools: MCPToolInfo[];
  resources: MCPResourceInfo[];
  prompts: MCPPromptInfo[];
  callResult: MCPToolCallResult | null;
  isLoading: boolean;
  isExecuting: boolean;
  error: string | null;
  loadServers: () => Promise<void>;
  selectServer: (id: string | null) => void;
  discoverTools: (serverId: string) => Promise<void>;
  discoverResources: (serverId: string) => Promise<void>;
  discoverPrompts: (serverId: string) => Promise<void>;
  callTool: (serverId: string, toolName: string, args: Record<string, unknown>) => Promise<void>;
  addServer: (config: Omit<MCPServerConfig, 'id' | 'createdAt' | 'updatedAt' | 'status'>) => Promise<void>;
  removeServer: (id: string) => Promise<void>;
  testConnection: (id: string) => Promise<void>;
}

export const useMCPStore = create<MCPStore>((set, get) => ({
  servers: [], selectedServerId: null, tools: [], resources: [], prompts: [],
  callResult: null, isLoading: false, isExecuting: false, error: null,

  loadServers: async () => {
    set({ isLoading: true, error: null });
    try {
      const data = await request('/platform/mcp/servers');
      if (!Array.isArray(data?.servers)) throw new Error('MCP 服务器响应无效');
      set({ servers: data.servers });
    } catch (err) {
      set({ servers: [], selectedServerId: null, tools: [], resources: [], prompts: [], callResult: null, error: message(err) });
    } finally { set({ isLoading: false }); }
  },

  selectServer: (id) => {
    set({ selectedServerId: id, tools: [], resources: [], prompts: [], callResult: null, error: null });
    if (id && get().servers.some(server => server.id === id && server.status === 'connected')) {
      void get().discoverTools(id);
    }
  },

  discoverTools: async (id) => {
    set({ tools: [], error: null });
    try {
      const data = await request(`${serverPath(id)}/tools`);
      if (!Array.isArray(data?.tools)) throw new Error('MCP 工具响应无效');
      if (get().selectedServerId === id) set({ tools: data.tools });
    } catch (err) { set({ error: message(err) }); }
  },

  discoverResources: async (id) => {
    set({ resources: [], error: null });
    try {
      const data = await request(`${serverPath(id)}/resources`);
      if (!Array.isArray(data?.resources)) throw new Error('MCP 资源响应无效');
      if (get().selectedServerId === id) set({ resources: data.resources });
    } catch (err) { set({ error: message(err) }); }
  },

  discoverPrompts: async (id) => {
    set({ prompts: [], error: null });
    try {
      const data = await request(`${serverPath(id)}/prompts`);
      if (!Array.isArray(data?.prompts)) throw new Error('MCP 提示响应无效');
      if (get().selectedServerId === id) set({ prompts: data.prompts });
    } catch (err) { set({ error: message(err) }); }
  },

  callTool: async (id, toolName, args) => {
    set({ isExecuting: true, callResult: null, error: null });
    try {
      const result = await request(`${serverPath(id)}/tools/call`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: toolName, arguments: args }),
      });
      if (!Array.isArray(result?.content)) throw new Error('MCP 工具结果无效');
      if (get().selectedServerId === id) set({ callResult: result });
    } catch (err) { set({ error: message(err) }); }
    finally { set({ isExecuting: false }); }
  },

  addServer: async (config) => {
    set({ error: null });
    try {
      const saved = await request('/platform/mcp/servers', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config),
      });
      if (typeof saved?.id !== 'string') throw new Error('MCP 保存响应无效');
      set(state => ({ servers: [...state.servers, saved] }));
    } catch (err) { set({ error: message(err) }); }
  },

  removeServer: async (id) => {
    set({ error: null });
    try {
      await request(serverPath(id), { method: 'DELETE' });
      const selected = get().selectedServerId === id;
      set(state => ({ servers: state.servers.filter(server => server.id !== id) }));
      if (selected) get().selectServer(null);
    } catch (err) { set({ error: message(err) }); }
  },

  testConnection: async (id) => {
    const error = '当前网关尚未提供连接检测接口，连接状态未知';
    set(state => ({
      error,
      servers: state.servers.map(server => server.id === id ? { ...server, status: 'unknown', errorMessage: error } : server),
    }));
  },
}));
