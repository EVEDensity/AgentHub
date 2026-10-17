import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useMissionChat } from '../../hooks/useMissionChat';

const options = { token: 'token', workspaceId: 'alice', sessionId: 'admin-assistant', authHeaders: () => ({ Authorization: 'Bearer token' }), cacheSessionLink: false, eventLimit: 2 };
const accepted = { missionId: 'mission-1', sessionId: 'native-1', streamUrl: '/api/v1/missions/mission-1/events/stream' };
const json = (body: unknown) => new Response(JSON.stringify(body));
const stream = (ids: string[] = []) => new Response(ids.map(eventId => 'data: ' + JSON.stringify({ eventId, missionId: 'mission-1', type: 'harness.assistant.delta', payload: { text: eventId } }) + '\n\n').join(''), { headers: { 'Content-Type': 'text/event-stream' } });
beforeEach(() => localStorage.clear());
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it('bounds observations and resumes GET with the last durable event ID without duplicates or another POST', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json(accepted)).mockResolvedValueOnce(stream(['event-1', 'event-2', 'event-3']))
    .mockResolvedValueOnce(stream(['event-1', 'event-2', 'event-3', 'event-4']));
  vi.stubGlobal('fetch', fetch);
  const { result, rerender, unmount } = renderHook(({ enabled }) => useMissionChat({ ...options, enabled }), { initialProps: { enabled: true } });
  await act(async () => { await result.current.sendMission('inspect'); });
  await waitFor(() => expect(result.current.events.map(event => event.eventId)).toEqual(['event-2', 'event-3']));
  expect(result.current.omittedEvents).toBe(1);
  rerender({ enabled: false });
  expect(result.current.missionId).toBe('mission-1');
  rerender({ enabled: true });
  await waitFor(() => expect(result.current.events.map(event => event.eventId)).toEqual(['event-3', 'event-4']));
  expect(result.current.omittedEvents).toBe(2);
  expect(fetch.mock.calls[2][1].headers['Last-Event-ID']).toBe('event-3');
  expect(fetch.mock.calls.filter(call => call[1].method === 'POST')).toHaveLength(1);
  unmount();
});

it('retains an admitted Mission after closing during POST, but starts observing only when reopened', async () => {
  let release!: (response: Response) => void;
  const fetch = vi.fn().mockReturnValueOnce(new Promise<Response>(resolve => { release = resolve; })).mockResolvedValueOnce(stream());
  vi.stubGlobal('fetch', fetch);
  const { result, rerender, unmount } = renderHook(({ enabled }) => useMissionChat({ ...options, enabled }), { initialProps: { enabled: true } });
  let acknowledgement!: Promise<unknown>;
  act(() => { acknowledgement = result.current.sendMission('inspect'); });
  rerender({ enabled: false });
  await act(async () => { release(json(accepted)); await acknowledgement; });
  expect(result.current.missionId).toBe('mission-1');
  expect(fetch).toHaveBeenCalledTimes(1);
  rerender({ enabled: true });
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(fetch.mock.calls[1][1].method).toBeUndefined();
  unmount();
});

it('never reuses or modifies persisted native session links in the mounted-only profile', async () => {
  localStorage.setItem('agenthub_v1_chat_session_links', JSON.stringify({ 'alice/admin-assistant': 'previous-person-session' }));
  const fetch = vi.fn().mockImplementation((path: string) => Promise.resolve(path.endsWith('/mission') ? json(accepted) : stream()));
  vi.stubGlobal('fetch', fetch);
  const first = renderHook(() => useMissionChat(options));
  await act(async () => { await first.result.current.sendMission('first'); });
  await act(async () => { await first.result.current.sendMission('second'); });
  first.unmount();
  const second = renderHook(() => useMissionChat(options));
  await act(async () => { await second.result.current.sendMission('fresh view'); });
  const posts = fetch.mock.calls.filter(call => call[1].method === 'POST');
  expect(posts.map(call => JSON.parse(call[1].body).sessionId)).toEqual([null, 'native-1', null]);
  expect(localStorage.getItem('agenthub_v1_chat_session_links')).toContain('previous-person-session');
  second.unmount();
});

it('does not admit a cancellation-resistant old response after token replacement', async () => {
  let release!: (response: Response) => void;
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise<Response>(resolve => { release = resolve; })));
  const { result, rerender, unmount } = renderHook(({ token }) => useMissionChat({ ...options, token }), { initialProps: { token: 'old' } });
  let acknowledgement!: Promise<unknown>;
  act(() => { acknowledgement = result.current.sendMission('old request'); });
  rerender({ token: 'new' });
  await act(async () => { release(json(accepted)); expect(await acknowledgement).toBeNull(); });
  expect(result.current.missionId).toBeNull();
  expect(result.current.events).toEqual([]);
  expect(result.current.error).toBeNull();
  unmount();
});

it('keeps cancellation failure visible instead of closing a still-active observation', async () => {
  const open = new Response(new ReadableStream<Uint8Array>({ start() {} }), { headers: { 'Content-Type': 'text/event-stream' } });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(json(accepted)).mockResolvedValueOnce(open).mockResolvedValueOnce(json({ status: 'accepted' })));
  const { result, unmount } = renderHook(() => useMissionChat(options));
  await act(async () => { await result.current.sendMission('inspect'); });
  await act(async () => { expect(await result.current.cancel()).toBe(false); });
  expect(result.current.error).toContain('取消结果未确认');
  expect(result.current.streamState).toBe('streaming');
  expect(result.current.missionId).toBe('mission-1');
  unmount();
});

it('surfaces an uncertain command deadline and ignores its late acknowledgement', async () => {
  vi.useFakeTimers();
  let release!: (response: Response) => void;
  const fetch = vi.fn().mockReturnValue(new Promise<Response>(resolve => { release = resolve; }));
  vi.stubGlobal('fetch', fetch);
  const { result, unmount } = renderHook(() => useMissionChat(options));
  let acknowledgement!: Promise<unknown>;
  act(() => { acknowledgement = result.current.sendMission('inspect'); });
  await act(async () => { await vi.advanceTimersByTimeAsync(20_001); await acknowledgement; });
  expect(result.current.error).toContain('结果未确认');
  expect(result.current.sending).toBe(false);
  await act(async () => { release(json(accepted)); });
  expect(result.current.missionId).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
  unmount();
});
