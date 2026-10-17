import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useMissionChat } from '../../hooks/useMissionChat';

const base = { token: 'token', workspaceId: 'workspace-1', sessionId: 'view', authHeaders: () => ({ Authorization: 'Bearer token' }), cacheSessionLink: false, eventLimit: 200 };
const accepted = { missionId: 'mission-1', sessionId: 'native-1', streamUrl: '/api/v1/missions/mission-1/events/stream' };
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
const stream = () => new Response('data: ' + JSON.stringify({ eventId: 'event-1', missionId: 'mission-1', type: 'harness.assistant.delta', payload: { text: 'durable' } }) + '\n\n', { headers: { 'Content-Type': 'text/event-stream' } });
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

it.each(['network', 'invalid-ack', 'server-error'])('latches an uncertain %s admission without discarding draft authority or retrying it', async failure => {
  const fetch = vi.fn().mockImplementation(() => failure === 'network' ? Promise.reject(new TypeError('network lost'))
    : Promise.resolve(failure === 'server-error' ? json({}, 503) : json({ ...accepted, streamUrl: 'https://foreign.test/events' })));
  vi.stubGlobal('fetch', fetch);
  const { result, rerender, unmount } = renderHook(({ sessionId }) => useMissionChat({ ...base, sessionId, blockUncertainAdmission: true }), { initialProps: { sessionId: 'view' } });
  await act(async () => { expect(await result.current.sendMission('may be committed')).toBeNull(); });
  expect(result.current.admissionUncertain).toBe(true);
  await act(async () => { expect(await result.current.sendMission('same draft')).toBeNull(); });
  expect(fetch).toHaveBeenCalledTimes(1);
  rerender({ sessionId: 'explicit-new-task' });
  expect(result.current.admissionUncertain).toBe(false); unmount();
});

it('allows correction after a definite 403 rejection', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({}, 403)).mockResolvedValueOnce(json(accepted)).mockResolvedValue(stream());
  vi.stubGlobal('fetch', fetch);
  const { result, unmount } = renderHook(() => useMissionChat({ ...base, blockUncertainAdmission: true }));
  await act(async () => { expect(await result.current.sendMission('denied')).toBeNull(); });
  expect(result.current.admissionUncertain).toBe(false);
  await act(async () => { expect(await result.current.sendMission('corrected')).toMatchObject({ missionId: 'mission-1' }); });
  expect(fetch.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(2); unmount();
});

it('restores an explicit native Session/Mission using GET only and sends new work to that native Session', async () => {
  const fetch = vi.fn().mockImplementation((_path: string, init: RequestInit) => Promise.resolve(init.method === 'POST' ? json(accepted) : stream()));
  vi.stubGlobal('fetch', fetch);
  const { result, unmount } = renderHook(() => useMissionChat({ ...base, nativeSessionId: 'native-1', resumeMissionId: 'mission-1' }));
  await waitFor(() => expect(result.current.events[0]?.content).toBe('durable'));
  expect(result.current.missionId).toBe('mission-1'); expect(result.current.sessionId).toBe('native-1');
  expect(fetch).toHaveBeenCalledTimes(1); expect(fetch.mock.calls[0][1].method).toBeUndefined();
  await act(async () => { await result.current.sendMission('next'); });
  const command = fetch.mock.calls.find(([, init]) => init.method === 'POST')!;
  expect(JSON.parse(command[1].body as string).sessionId).toBe('native-1');
  expect(localStorage.getItem('agenthub_v1_chat_session_links')).toBeNull(); unmount();
});

it('does not infer native scope from a view ID, or observe malformed restoration', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  const { result, unmount } = renderHook(() => useMissionChat({ ...base, resumeMissionId: 'mission-1' }));
  expect(result.current.error).toBe('原生会话恢复参数无效');
  await act(async () => { expect(await result.current.sendMission('inspect')).toBeNull(); });
  expect(fetch).not.toHaveBeenCalled(); unmount();
});

it('refuses an admission acknowledgement that changes an explicitly selected native Session', async () => {
  const fetch = vi.fn().mockResolvedValue(json({ ...accepted, sessionId: 'foreign' })); vi.stubGlobal('fetch', fetch);
  const { result, unmount } = renderHook(() => useMissionChat({ ...base, nativeSessionId: 'native-1' }));
  await act(async () => { expect(await result.current.sendMission('inspect')).toBeNull(); });
  expect(result.current.error).toContain('会话不符'); expect(result.current.missionId).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1); unmount();
});

it.each([200, 503])('a late A response (%s) cannot replace or unlock a new A command after A to B to A', async status => {
  let oldResolve!: (r: Response) => void; let newResolve!: (r: Response) => void;
  const fetch = vi.fn().mockReturnValueOnce(new Promise<Response>(r => { oldResolve = r; }))
    .mockReturnValueOnce(new Promise<Response>(r => { newResolve = r; })).mockResolvedValue(stream());
  vi.stubGlobal('fetch', fetch);
  const { result, rerender, unmount } = renderHook(({ sessionId }) => useMissionChat({ ...base, sessionId }), { initialProps: { sessionId: 'A' } });
  let old!: Promise<unknown>; act(() => { old = result.current.sendMission('old'); });
  rerender({ sessionId: 'B' }); rerender({ sessionId: 'A' });
  let current!: Promise<unknown>; act(() => { current = result.current.sendMission('new'); });
  await act(async () => { oldResolve(json(status === 200 ? accepted : {}, status)); await old; });
  expect(result.current.sending).toBe(true); expect(result.current.error).toBeNull(); expect(result.current.missionId).toBeNull();
  await act(async () => { expect(await result.current.sendMission('duplicate')).toBeNull(); });
  expect(fetch).toHaveBeenCalledTimes(2);
  await act(async () => { newResolve(json(accepted)); await current; });
  await waitFor(() => expect(result.current.events).toHaveLength(1)); unmount();
});
