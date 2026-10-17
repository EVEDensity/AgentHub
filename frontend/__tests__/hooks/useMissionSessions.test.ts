import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useMissionSessions, useSessionMissions } from '../../hooks/useMissionSessions';
import { readSessionPage, validateSessionPage } from '../../lib/missionSessions';

const scope = { token: 'human', workspaceId: 'workspace-1' };
const session = (id = 'session-1', workspaceId = 'workspace-1') => ({ id, workspaceId, title: '真实会话', status: 'ACTIVE', createdAt: '2026-10-18T01:00:00Z', updatedAt: '2026-10-18T01:00:00Z' });
const page = (ids = ['session-1']) => ({ schemaVersion: 1, workspaceId: 'workspace-1', sessions: ids.map(id => session(id)), hasMore: false, nextOffset: null });
const mission = { id: 'mission-1', workspaceId: 'workspace-1', sessionId: 'session-1', title: '任务', objectivePreview: '服务端目标', objectiveTruncated: false, status: 'VERIFYING', createdAt: '2026-10-18T01:00:00Z' };
const missions = () => ({ schemaVersion: 1, workspaceId: 'workspace-1', sessionId: 'session-1', missions: [mission], hasMore: false, nextBeforeId: null });
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it.each([
  { ...page(), workspaceId: 'foreign' }, { ...page(), sessions: [session('foreign', 'foreign')] },
  { ...page(), sessions: [session(), session()] }, { ...page(), sessions: [{ ...session(), status: 'UNKNOWN' }] },
  { ...page(), sessions: [{ ...session(), createdAt: 'not-a-time' }] },
  { ...page(), hasMore: true, nextOffset: 200 }, { ...page(), nextOffset: 50 },
])('rejects foreign, duplicate, malformed and invalid pagination snapshots %#', value => {
  expect(() => validateSessionPage(value, 'sessions', scope, null)).toThrow('会话读取响应无效');
});

it.each([
  { ...missions(), sessionId: 'foreign' }, { ...missions(), missions: [{ ...mission, sessionId: 'foreign' }] },
  { ...missions(), missions: [{ ...mission, objectivePreview: '字'.repeat(1001) }] },
  { ...missions(), hasMore: true, nextBeforeId: 'foreign' },
])('refuses to turn a foreign or oversized objective into recovered history %#', value => {
  expect(() => validateSessionPage(value, 'missions', { ...scope, sessionId: 'session-1' }, null)).toThrow();
});

it('reads pages without commands, deduplicates offset overlap, and preserves visible rows on a failed next read', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({ ...page(), hasMore: true, nextOffset: 1 }))
    .mockResolvedValueOnce(json({ ...page(['session-1', 'session-2']), hasMore: true, nextOffset: 3 }))
    .mockResolvedValueOnce(json({ detail: 'unavailable' }, 503));
  vi.stubGlobal('fetch', fetch);
  const { result, unmount } = renderHook(() => useMissionSessions(scope));
  await waitFor(() => expect(result.current.items).toHaveLength(1));
  act(() => { result.current.loadMore(); result.current.loadMore(); });
  await waitFor(() => expect(result.current.items).toHaveLength(2));
  act(() => result.current.loadMore());
  await waitFor(() => expect(result.current.error).toContain('HTTP 503'));
  expect(result.current.items.map(row => row.id)).toEqual(['session-1', 'session-2']);
  expect(fetch).toHaveBeenCalledTimes(3);
  expect(fetch.mock.calls[1][0]).toContain('offset=1');
  for (const [, init] of fetch.mock.calls) expect(init).toMatchObject({ cache: 'no-store', redirect: 'error', headers: { Authorization: 'Bearer human' } });
  unmount();
});

it('does not request an unselected session or anonymous scope', () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  const a = renderHook(() => useSessionMissions(scope));
  const b = renderHook(() => useMissionSessions({ ...scope, token: '' }));
  expect(fetch).not.toHaveBeenCalled(); a.unmount(); b.unmount();
});

it('masks old workspace bytes immediately and rejects a late A response after A to B to A', async () => {
  let releaseOld!: (v: Response) => void;
  const fetch = vi.fn().mockReturnValueOnce(new Promise<Response>(r => { releaseOld = r; }))
    .mockResolvedValueOnce(json({ ...page(), workspaceId: 'workspace-2', sessions: [session('session-2', 'workspace-2')] }))
    .mockResolvedValueOnce(json(page(['session-new'])));
  vi.stubGlobal('fetch', fetch);
  const { result, rerender, unmount } = renderHook(({ workspaceId }) => useMissionSessions({ ...scope, workspaceId }), { initialProps: { workspaceId: 'workspace-1' } });
  rerender({ workspaceId: 'workspace-2' });
  expect(result.current.items).toEqual([]);
  await waitFor(() => expect(result.current.items[0]?.id).toBe('session-2'));
  rerender({ workspaceId: 'workspace-1' });
  expect(result.current.items).toEqual([]);
  await waitFor(() => expect(result.current.items[0]?.id).toBe('session-new'));
  await act(async () => { releaseOld(json(page(['session-old']))); });
  expect(result.current.items[0].id).toBe('session-new');
  expect(result.current.error).toBeNull(); unmount();
});

it('bounds a stalled read even when fetch ignores cancellation', async () => {
  vi.useFakeTimers(); vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)));
  const pending = readSessionPage('sessions', scope, null, new AbortController());
  const check = expect(pending).rejects.toThrow('会话读取超时');
  await vi.advanceTimersByTimeAsync(10_001); await check;
});

it('caps the session cache and reports that more server rows remain', async () => {
  const fetch = vi.fn().mockImplementation((path: string) => {
    const offset = Number(new URL(path, 'http://test').searchParams.get('offset') ?? 0);
    return Promise.resolve(json({ ...page(Array.from({ length: 50 }, (_, i) => 'session-' + (i + offset))), hasMore: true, nextOffset: offset + 50 }));
  });
  vi.stubGlobal('fetch', fetch);
  const { result, unmount } = renderHook(() => useMissionSessions(scope));
  await waitFor(() => expect(result.current.items).toHaveLength(50));
  for (let i = 2; i <= 10; i++) {
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.items).toHaveLength(i * 50));
  }
  expect(result.current.limited).toBe(true); expect(result.current.hasMore).toBe(false);
  act(() => result.current.loadMore()); expect(fetch).toHaveBeenCalledTimes(10); unmount();
});
