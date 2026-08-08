import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

function jsonResponse(data: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(data),
  } as unknown as Response;
}

async function getA2AStore() {
  vi.resetModules();
  const setNotice = vi.fn();

  vi.doMock('../../stores/authStore', () => ({
    useAuthStore: {
      getState: () => ({
        user: { id: 'workspace-1', name: 'Ada', role: 'developer' },
        authHeaders: () => ({ Authorization: 'Bearer test-token' }),
      }),
    },
  }));
  vi.doMock('../../stores/adminStore', () => ({
    useAdminStore: {
      getState: () => ({ setNotice }),
    },
  }));

  const mod = await import('../../stores/a2aStore');
  return { store: mod.useA2AStore, setNotice };
}

describe('a2aStore honest failure behavior', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('keeps the registry empty when the gateway is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    const { store, setNotice } = await getA2AStore();

    await store.getState().loadAgents();

    expect(store.getState().agents).toEqual([]);
    expect(setNotice).toHaveBeenCalledWith('A2A 网关不可用，无法加载 Agent 列表');
  });

  it('does not fabricate a working task when sending fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    const { store, setNotice } = await getA2AStore();

    await store.getState().sendTask('http://agent.test', 'review this');

    expect(store.getState().taskResult).toBeNull();
    expect(setNotice).toHaveBeenCalledWith('A2A 网关不可用，任务未发送');
  });

  it('forwards the selected agent URL to the gateway', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ result: { id: 'task-1', status: 'working' } }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const { store, setNotice } = await getA2AStore();

    await store.getState().sendTask('http://agent.test', 'review this');

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(body.params.agentUrl).toBe('http://agent.test');
    expect(body.params.workspaceId).toBe('workspace-1');
    expect(store.getState().taskResult).toEqual({ id: 'task-1', status: 'working' });
    expect(setNotice).toHaveBeenCalledWith('A2A 任务已发送');
  });

  it('reports a failed remote task without announcing success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ result: { id: 'task-2', status: 'failed' } }),
    ));
    const { store, setNotice } = await getA2AStore();

    await store.getState().sendTask('http://agent.test', 'review this');

    expect(store.getState().taskResult).toEqual({ id: 'task-2', status: 'failed' });
    expect(setNotice).toHaveBeenCalledWith('A2A Agent 执行任务失败');
    expect(setNotice).not.toHaveBeenCalledWith('A2A 任务已发送');
  });
});
