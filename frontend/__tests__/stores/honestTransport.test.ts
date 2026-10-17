import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useMCPStore } from '../../stores/mcpStore';
import { useAgentVersionStore } from '../../stores/agentVersionStore';
import { useCostStore } from '../../stores/costStore';

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const server = { id: 'real-server', name: 'Actual server', description: '', transport: 'sse' as const,
  status: 'unknown' as const, createdAt: '2026-10-17', updatedAt: '2026-10-17' };

describe('management transport failures', () => {
  beforeEach(() => {
    useMCPStore.setState({ servers: [], selectedServerId: null, tools: [], resources: [], prompts: [], callResult: null, error: null });
    useAgentVersionStore.setState({ versions: [], error: '' });
    useCostStore.setState({ tokenData: null, billingByTenant: [], costEstimate: null, costError: '' });
  });
  afterEach(() => vi.unstubAllGlobals());

  it.each(['offline', 'http'])('does not invent MCP servers or a tool result when %s', async failure => {
    vi.stubGlobal('fetch', failure === 'offline' ? vi.fn().mockRejectedValue(new Error('offline')) : vi.fn().mockResolvedValue(json({}, 503)));
    await useMCPStore.getState().loadServers();
    expect(useMCPStore.getState().servers).toEqual([]);
    expect(useMCPStore.getState().error).toBeTruthy();
    await useMCPStore.getState().callTool('missing', 'run', {});
    expect(useMCPStore.getState().callResult).toBeNull();
    expect(useMCPStore.getState().isExecuting).toBe(false);
  });

  it('does not acknowledge server writes or connection tests without a backend result', async () => {
    const fetch = vi.fn().mockResolvedValue(json({}, 503));
    vi.stubGlobal('fetch', fetch);
    useMCPStore.setState({ servers: [server] });
    await useMCPStore.getState().removeServer(server.id);
    expect(useMCPStore.getState().servers).toEqual([server]);
    await useMCPStore.getState().addServer({ name: 'new', description: '', transport: 'stdio' });
    expect(useMCPStore.getState().servers).toEqual([server]);
    await useMCPStore.getState().testConnection(server.id);
    expect(useMCPStore.getState().servers[0].status).toBe('unknown');
    expect(useMCPStore.getState().error).toContain('尚未提供连接检测');
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('clears stale discoveries on a failed read, but preserves genuine tool error results', async () => {
    const actual = { content: [{ type: 'text', text: 'permission denied' }], isError: true };
    const fetch = vi.fn().mockResolvedValueOnce(json({}, 404)).mockResolvedValueOnce(json(actual));
    vi.stubGlobal('fetch', fetch);
    useMCPStore.setState({ selectedServerId: server.id, tools: [{ name: 'old', description: '', inputSchema: { type: 'object' }, serverId: server.id, serverName: server.name }] });
    await useMCPStore.getState().discoverTools(server.id);
    expect(useMCPStore.getState().tools).toEqual([]);
    expect(useMCPStore.getState().error).toBeTruthy();
    await useMCPStore.getState().callTool(server.id, 'run', {});
    expect(useMCPStore.getState().callResult).toEqual(actual);
  });

  it.each(['offline', 'http'])('does not invent versions, rollback success or billing data when %s', async failure => {
    vi.stubGlobal('fetch', failure === 'offline' ? vi.fn().mockRejectedValue(new Error('offline')) : vi.fn().mockResolvedValue(json({}, 500)));
    await useAgentVersionStore.getState().loadVersions('agent-1');
    expect(useAgentVersionStore.getState().versions).toEqual([]);
    expect(useAgentVersionStore.getState().error).toBeTruthy();
    expect(await useAgentVersionStore.getState().rollback('agent-1', 1)).toBe(false);
    expect(useAgentVersionStore.getState().error).toBeTruthy();
    await useCostStore.getState().init();
    expect(useCostStore.getState().billingByTenant).toEqual([]);
    expect(useCostStore.getState().tokenData).toBeNull();
    expect(useCostStore.getState().costEstimate).toBeNull();
    expect(useCostStore.getState().costError).toBeTruthy();
  });
});
