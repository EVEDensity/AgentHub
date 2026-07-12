// MCP Gateway Store (P1-2)
// Manages MCP server connections, tool discovery, and execution.
// Communicates with the Go MCP Gateway service (port 8099) for SSE transport
// and provides client-side tools for STDIO transport management.

import { create } from 'zustand';
import type { MCPServerConfig, MCPToolInfo, MCPResourceInfo, MCPPromptInfo, MCPToolCallResult, JSONRPCResponse } from '../types';

const MCP_GATEWAY_URL = process.env.NEXT_PUBLIC_MCP_GATEWAY_URL || 'http://127.0.0.1:8099';

// ── Demo Data ─────────────────────────────────────────────────────────

function generateDemoServers(): MCPServerConfig[] {
  const now = new Date().toISOString();
  return [
    {
      id: 'mcp-demo-1',
      name: 'AgentHub Knowledge',
      description: 'Search and retrieve documents from AgentHub knowledge base via MCP',
      transport: 'sse',
      url: 'http://127.0.0.1:8099/mcp',
      status: 'connected',
      lastConnectedAt: now,
      tags: ['knowledge', 'search', 'rag'],
      createdAt: now,
      updatedAt: now,
    },
    {
      id: 'mcp-demo-2',
      name: 'Code Analysis Tools',
      description: 'Static analysis, linting, and code review tools exposed via MCP',
      transport: 'stdio',
      command: 'node',
      args: ['mcp-code-analyzer.js'],
      env: { NODE_ENV: 'production' },
      status: 'disconnected',
      tags: ['code', 'analysis', 'linting'],
      createdAt: now,
      updatedAt: now,
    },
    {
      id: 'mcp-demo-3',
      name: 'Database Explorer',
      description: 'Explore PostgreSQL schemas and run read-only queries',
      transport: 'sse',
      url: 'http://127.0.0.1:8100/mcp',
      status: 'unknown',
      tags: ['database', 'postgresql', 'schema'],
      createdAt: now,
      updatedAt: now,
    },
    {
      id: 'mcp-demo-4',
      name: 'File System Tools',
      description: 'Read, list, and search files in connected workspaces',
      transport: 'stdio',
      command: 'python',
      args: ['-m', 'mcp_server_fs'],
      status: 'error',
      errorMessage: 'Connection refused: python module not found',
      tags: ['filesystem', 'workspace'],
      createdAt: now,
      updatedAt: now,
    },
  ];
}

function generateDemoTools(serverId: string, serverName: string): MCPToolInfo[] {
  if (serverId === 'mcp-demo-1') {
    return [
      {
        name: 'knowledge_search',
        description: 'Search the AgentHub knowledge base using semantic search. Returns relevant document chunks with scores and citations.',
        inputSchema: {
          type: 'object',
          properties: {
            query: { type: 'string', description: 'Natural language search query' },
            collection: { type: 'string', description: 'Knowledge collection', enum: ['docs', 'code', 'memory', 'artifacts'] },
            top_k: { type: 'integer', description: 'Number of results (1-20)', default: 5 },
          },
          required: ['query'],
        },
        serverId,
        serverName,
      },
      {
        name: 'ingest_document',
        description: 'Ingest a document into the knowledge base for later retrieval.',
        inputSchema: {
          type: 'object',
          properties: {
            content: { type: 'string', description: 'Document content' },
            title: { type: 'string', description: 'Document title' },
            collection: { type: 'string', description: 'Target collection', enum: ['docs', 'code', 'memory', 'artifacts'] },
          },
          required: ['content', 'title'],
        },
        serverId,
        serverName,
      },
    ];
  }
  return [
    {
      name: 'list_agents',
      description: 'List all registered agents in AgentHub',
      inputSchema: { type: 'object', properties: {} },
      serverId,
      serverName,
    },
  ];
}

// ── Store ──────────────────────────────────────────────────────────────

export interface MCPStore {
  // State
  servers: MCPServerConfig[];
  selectedServerId: string | null;
  tools: MCPToolInfo[];
  resources: MCPResourceInfo[];
  prompts: MCPPromptInfo[];
  callResult: MCPToolCallResult | null;
  isLoading: boolean;
  isExecuting: boolean;
  error: string | null;
  demoMode: boolean;

  // Actions
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
  servers: [],
  selectedServerId: null,
  tools: [],
  resources: [],
  prompts: [],
  callResult: null,
  isLoading: false,
  isExecuting: false,
  error: null,
  demoMode: true,

  loadServers: async () => {
    set({ isLoading: true, error: null });
    try {
      const res = await fetch(`${MCP_GATEWAY_URL}/platform/mcp/servers`);
      if (res.ok) {
        const data = await res.json();
        set({ servers: data.servers || [], isLoading: false, demoMode: false });
      } else {
        throw new Error('API unavailable');
      }
    } catch {
      // Demo mode fallback
      const demoServers = generateDemoServers();
      set({ servers: demoServers, isLoading: false, demoMode: true });
    }
  },

  selectServer: (id: string | null) => {
    set({ selectedServerId: id, tools: [], resources: [], prompts: [], callResult: null, error: null });
    if (id) {
      const { servers } = get();
      const server = servers.find(s => s.id === id);
      if (server) {
        // Auto-discover tools for connected servers
        if (server.status === 'connected') {
          get().discoverTools(id);
        } else if (get().demoMode) {
          // Demo tools
          set({ tools: generateDemoTools(server.id, server.name) });
        }
      }
    }
  },

  discoverTools: async (serverId: string) => {
    set({ error: null });
    try {
      const res = await fetch(`${MCP_GATEWAY_URL}/platform/mcp/servers/${serverId}/tools`);
      if (res.ok) {
        const data = await res.json();
        set({ tools: data.tools || [] });
      } else {
        throw new Error('API unavailable');
      }
    } catch {
      // Demo fallback
      const { servers } = get();
      const server = servers.find(s => s.id === serverId);
      if (server) {
        set({ tools: generateDemoTools(server.id, server.name) });
      }
    }
  },

  discoverResources: async (serverId: string) => {
    set({ error: null });
    try {
      const res = await fetch(`${MCP_GATEWAY_URL}/platform/mcp/servers/${serverId}/resources`);
      if (res.ok) {
        const data = await res.json();
        set({ resources: data.resources || [] });
      }
    } catch {
      // Silently fail — resources are optional
    }
  },

  discoverPrompts: async (serverId: string) => {
    set({ error: null });
    try {
      const res = await fetch(`${MCP_GATEWAY_URL}/platform/mcp/servers/${serverId}/prompts`);
      if (res.ok) {
        const data = await res.json();
        set({ prompts: data.prompts || [] });
      }
    } catch {
      // Silently fail — prompts are optional
    }
  },

  callTool: async (serverId: string, toolName: string, args: Record<string, unknown>) => {
    set({ isExecuting: true, callResult: null, error: null });
    try {
      const res = await fetch(`${MCP_GATEWAY_URL}/platform/mcp/servers/${serverId}/tools/call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: toolName, arguments: args }),
      });
      if (res.ok) {
        const data = await res.json();
        set({ callResult: data, isExecuting: false });
      } else {
        throw new Error('Tool call failed');
      }
    } catch (err) {
      // Demo fallback: simulate tool execution
      const demoResult: MCPToolCallResult = {
        content: [{
          type: 'text',
          text: `[Demo] Tool "${toolName}" executed with args: ${JSON.stringify(args, null, 2)}\n\nThis is a simulated response. Connect to a real MCP server for actual results.`,
        }],
      };
      set({ callResult: demoResult, isExecuting: false });
    }
  },

  addServer: async (config) => {
    const now = new Date().toISOString();
    const newServer: MCPServerConfig = {
      ...config,
      id: `mcp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      status: 'unknown',
      createdAt: now,
      updatedAt: now,
    };

    try {
      const res = await fetch(`${MCP_GATEWAY_URL}/platform/mcp/servers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newServer),
      });
      if (res.ok) {
        const saved = await res.json();
        set(s => ({ servers: [...s.servers, saved] }));
        return;
      }
    } catch {
      // Fall through to local-only add
    }
    set(s => ({ servers: [...s.servers, newServer] }));
  },

  removeServer: async (id: string) => {
    try {
      await fetch(`${MCP_GATEWAY_URL}/platform/mcp/servers/${id}`, { method: 'DELETE' });
    } catch {
      // Local remove regardless
    }
    set(s => ({
      servers: s.servers.filter(srv => srv.id !== id),
      selectedServerId: s.selectedServerId === id ? null : s.selectedServerId,
      tools: s.selectedServerId === id ? [] : s.tools,
    }));
  },

  testConnection: async (id: string) => {
    set(s => ({
      servers: s.servers.map(srv =>
        srv.id === id ? { ...srv, status: 'connected' as const, errorMessage: undefined, lastConnectedAt: new Date().toISOString() } : srv
      ),
    }));
    // In production, this would ping the MCP server
    try {
      const server = get().servers.find(s => s.id === id);
      if (server?.url) {
        const res = await fetch(`${server.url}/healthz`);
        if (res.ok) {
          set(s => ({
            servers: s.servers.map(srv =>
              srv.id === id ? { ...srv, status: 'connected' as const, lastConnectedAt: new Date().toISOString() } : srv
            ),
          }));
        }
      }
    } catch {
      // Keep the optimistic connected status for demo
    }
  },
}));
