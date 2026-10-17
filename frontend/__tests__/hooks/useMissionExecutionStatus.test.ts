import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useMissionExecutionStatus } from '../../hooks/useMissionExecutionStatus';

const headers = () => ({ Authorization: 'Bearer test-token' });
const snapshot = {
  schemaVersion: 1, missionId: 'mission-1', workspaceId: 'workspace-1', missionStatus: 'RUNNING', observedAt: '2026-10-18T00:00:00Z',
  workUnits: [{ workUnitId: 'unit-1', status: 'PENDING', assignedAgentId: 'executor', assignedAdapter: 'function-calling', reason: 'waiting_runner', matchingRunnerAvailable: false, availabilitySource: 'none', lastSeenAt: null, availabilityExpiresAt: null }],
};
const json = (value: unknown) => new Response(JSON.stringify(value));
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it.each([
  { ...snapshot, workspaceId: 'foreign' }, { ...snapshot, missionStatus: 'PASSED' },
  { ...snapshot, workUnits: [null] }, { ...snapshot, workUnits: [{ ...snapshot.workUnits[0], matchingRunnerAvailable: 'true' }] },
  { ...snapshot, workUnits: [snapshot.workUnits[0], snapshot.workUnits[0]] },
])('rejects malformed or foreign successful status projections', async value => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(value)));
  const { result } = renderHook(() => useMissionExecutionStatus('mission-1', 'token', headers, { workspaceId: 'workspace-1' }));
  await waitFor(() => expect(result.current.error).toBe('执行状态响应无效'));
  expect(result.current.data).toBeNull();
});

it('removes a loaded projection synchronously on identity change and ignores a cancellation-resistant old read', async () => {
  let release!: (value: Response) => void;
  const fetch = vi.fn().mockResolvedValueOnce(json(snapshot)).mockReturnValueOnce(new Promise<Response>(resolve => { release = resolve; }))
    .mockResolvedValueOnce(json({ ...snapshot, missionStatus: 'CANCELLED' }));
  vi.stubGlobal('fetch', fetch);
  const { result, rerender, unmount } = renderHook(({ token }) => useMissionExecutionStatus('mission-1', token, headers, { workspaceId: 'workspace-1' }), { initialProps: { token: 'old' } });
  await waitFor(() => expect(result.current.data?.missionStatus).toBe('RUNNING'));
  act(() => result.current.retry());
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  rerender({ token: 'new' });
  expect(result.current.data).toBeNull();
  await waitFor(() => expect(result.current.data?.missionStatus).toBe('CANCELLED'));
  await act(async () => { release(json(snapshot)); });
  expect(result.current.data?.missionStatus).toBe('CANCELLED');
  unmount();
});

it('surfaces a deadline even if fetch never settles and refuses late success', async () => {
  vi.useFakeTimers();
  let release!: (value: Response) => void;
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise<Response>(resolve => { release = resolve; })));
  const { result, unmount } = renderHook(() => useMissionExecutionStatus('mission-1', 'token', headers));
  await act(async () => { await vi.advanceTimersByTimeAsync(10_001); });
  expect(result.current.error).toContain('超时');
  await act(async () => { release(json(snapshot)); });
  expect(result.current.data).toBeNull();
  expect(result.current.error).toContain('超时');
  unmount();
});

it('does not poll a closed view', () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  const { result } = renderHook(() => useMissionExecutionStatus('mission-1', 'token', headers, { enabled: false }));
  expect(result.current.data).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});
