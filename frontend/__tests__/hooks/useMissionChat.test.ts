import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useMissionChat } from '../../hooks/useMissionChat';

const options = { token: 'test-token', workspaceId: 'alice', sessionId: 'legacy-view', authHeaders: () => ({ Authorization: 'Bearer test-token' }) };
const accepted = {
  missionId: 'mission-1', sessionId: 'native-session-1', streamUrl: '/api/v1/missions/mission-1/events/stream',
  dispatch: { status: 'PENDING' }, mentions: { resolved: [], unresolved: [] },
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const stream = () => new Response(new ReadableStream<Uint8Array>({ start() {} }), { headers: { 'Content-Type': 'text/event-stream' } });

describe('useMissionChat admission', () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('returns the durable acknowledgement while the event stream remains open', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(json(accepted)).mockResolvedValueOnce(stream());
    vi.stubGlobal('fetch', fetch);
    const { result, unmount } = renderHook(() => useMissionChat(options));
    let acknowledgement: unknown;
    await act(async () => { acknowledgement = await result.current.sendMission('@executor inspect'); });
    expect(acknowledgement).toMatchObject({ missionId: 'mission-1' });
    expect(result.current.missionId).toBe('mission-1');
    expect(result.current.sending).toBe(false);
    expect(result.current.streamState).toBe('streaming');
    unmount();
  });

  it('does not assign a workspace to legacy sessions and reuses only the admitted native ID', async () => {
    const fetch = vi.fn().mockImplementation((path: string) => Promise.resolve(path.endsWith('/mission') ? json(accepted) : stream()));
    vi.stubGlobal('fetch', fetch);
    const { result, unmount } = renderHook(() => useMissionChat(options));
    await act(async () => { await result.current.sendMission('first'); });
    expect(JSON.parse(fetch.mock.calls[0][1].body).sessionId).toBeNull();
    await act(async () => { await result.current.sendMission('second'); });
    const posts = fetch.mock.calls.filter(call => call[0] === '/api/v1/chat/mission');
    expect(JSON.parse(posts[1][1].body).sessionId).toBe('native-session-1');
    unmount();
  });

  it('keeps submission failures visible without inventing a Mission', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'catalog unavailable' }, 503)));
    const { result } = renderHook(() => useMissionChat(options));
    await act(async () => { expect(await result.current.sendMission('inspect')).toBeNull(); });
    expect(result.current.missionId).toBeNull();
    expect(result.current.error).toBe('catalog unavailable');
  });

  it('waits for confirmation before subscribing or displaying an execution', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(json({ status: 'pending', pendingId: 'pending-1', sessionId: 'native-session-1', ruleDescription: 'requires approval' }))
      .mockResolvedValueOnce(json(accepted)).mockResolvedValueOnce(stream());
    vi.stubGlobal('fetch', fetch);
    const { result, unmount } = renderHook(() => useMissionChat(options));
    await act(async () => { await result.current.sendMission('approved work'); });
    expect(result.current.pending?.id).toBe('pending-1');
    expect(result.current.missionId).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(1);
    await act(async () => { await result.current.confirmPending(); });
    expect(fetch.mock.calls[1][0]).toBe('/api/v1/chat/confirm');
    expect(result.current.missionId).toBe('mission-1');
    unmount();
  });

  it('cancels through the server command instead of only closing the stream', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(json(accepted)).mockResolvedValueOnce(stream()).mockResolvedValueOnce(json({ status: 'CANCELLED' }));
    vi.stubGlobal('fetch', fetch);
    const { result, unmount } = renderHook(() => useMissionChat(options));
    await act(async () => { await result.current.sendMission('inspect'); });
    await act(async () => { expect(await result.current.cancel()).toBe(true); });
    expect(fetch.mock.calls[2][0]).toBe('/api/v1/missions/mission-1/cancel');
    expect(result.current.streamState).toBe('closed');
    unmount();
  });

  it('reconnects and deduplicates ledger IDs without submitting another Mission', async () => {
    const encode = (ids: string[]) => new Response(ids.map(eventId => 'data: ' + JSON.stringify({ eventId, missionId: 'mission-1', type: 'harness.assistant.delta', payload: { text: eventId } }) + '\n\n').join(''));
    const fetch = vi.fn().mockResolvedValueOnce(json(accepted)).mockResolvedValueOnce(encode(['event-1'])).mockResolvedValueOnce(encode(['event-1', 'event-2']));
    vi.stubGlobal('fetch', fetch);
    const { result } = renderHook(() => useMissionChat(options));
    await act(async () => { await result.current.sendMission('inspect'); });
    await waitFor(() => expect(result.current.events).toHaveLength(1));
    await act(async () => { result.current.reconnect(); });
    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(fetch.mock.calls.filter(call => call[0] === '/api/v1/chat/mission')).toHaveLength(1);
  });

  it.each([false, true])('ignores a stale cancel acknowledgement after another Mission is admitted (same view: %s)', async (sameView) => {
    let finishCancel!: (response: Response) => void;
    const delayedCancel = new Promise<Response>(resolve => { finishCancel = resolve; });
    const second = { ...accepted, missionId: 'mission-2', sessionId: 'native-session-2', streamUrl: '/api/v1/missions/mission-2/events/stream' };
    const fetch = vi.fn().mockResolvedValueOnce(json(accepted)).mockResolvedValueOnce(stream())
      .mockReturnValueOnce(delayedCancel).mockResolvedValueOnce(json(second)).mockResolvedValueOnce(stream());
    vi.stubGlobal('fetch', fetch);
    const { result, rerender, unmount } = renderHook(({ sessionId }) => useMissionChat({ ...options, sessionId }), { initialProps: { sessionId: 'old-view' } });
    await act(async () => { await result.current.sendMission('first'); });
    let cancel!: Promise<boolean>;
    act(() => { cancel = result.current.cancel(); });
    if (!sameView) rerender({ sessionId: 'new-view' });
    await act(async () => { await result.current.sendMission('second'); });
    await act(async () => { finishCancel(json({ status: 'CANCELLED' })); expect(await cancel).toBe(false); });
    expect(result.current.missionId).toBe('mission-2');
    expect(result.current.streamState).toBe('streaming');
    expect(fetch.mock.calls[4][1].signal.aborted).toBe(false);
    unmount();
  });
});
