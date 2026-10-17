import { afterEach, describe, expect, it, vi } from 'vitest';
import { missionCommand, missionStreamPath, readMissionJson, readMissionStream } from '../../lib/missionStream';

const path = '/api/v1/missions/mission-1/events/stream';
const signal = () => new AbortController().signal;
const event = (overrides = {}) => ({ eventId: 'event-1', missionId: 'mission-1', type: 'harness.assistant.delta', payload: { text: '中文<script>bad()</script>' }, ...overrides });
const sse = (body: string) => new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });

afterEach(() => vi.unstubAllGlobals());

describe('bounded Mission transport', () => {
  it.each(['https://foreign.test/feed', '//foreign.test/feed', '/api/v1/missions/other/events/stream', path + '#fragment', '/\\foreign.test/feed'])('rejects a credential destination outside this Mission: %s', async url => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    expect(() => missionStreamPath(url, 'mission-1')).toThrow();
    await expect(readMissionStream(url, { Authorization: 'Bearer private' }, signal(), vi.fn(), undefined, { missionId: 'mission-1' })).rejects.toThrow();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('decodes split UTF-8/CRLF, preserves stable identity/type and sends the real replay cursor', async () => {
    const bytes = new TextEncoder().encode(`id: event-1\r\ndata: ${JSON.stringify(event())}\r\n\r\n`);
    const body = new ReadableStream<Uint8Array>({ start(controller) { for (const byte of bytes) controller.enqueue(new Uint8Array([byte])); controller.close(); } });
    const fetch = vi.fn().mockResolvedValue(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }));
    vi.stubGlobal('fetch', fetch);
    const receive = vi.fn();
    await readMissionStream(path + '?maxSeconds=0', { Authorization: 'Bearer private' }, signal(), receive, undefined, { missionId: 'mission-1', requireEventId: true, afterEventId: 'previous-event' });
    expect(fetch.mock.calls[0][1]).toMatchObject({ redirect: 'error', cache: 'no-store', credentials: 'same-origin', headers: { 'Last-Event-ID': 'previous-event' } });
    expect(receive).toHaveBeenCalledOnce();
    expect(receive.mock.calls[0][0]).toMatchObject({ eventId: 'event-1', eventType: 'harness.assistant.delta', missionId: 'mission-1', content: '中文<script>bad()</script>' });
  });

  it.each([event({ missionId: 'foreign' }), event({ eventId: '' }), event({ type: null }), []])('rejects an invalid or foreign ledger envelope', async payload => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sse('data: ' + JSON.stringify(payload) + '\n\n')));
    const receive = vi.fn();
    await expect(readMissionStream(path, {}, signal(), receive, undefined, { missionId: 'mission-1', requireEventId: true })).rejects.toThrow();
    expect(receive).not.toHaveBeenCalled();
  });

  it('refuses oversized residual frames and releases the reader', async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new TextEncoder().encode('data: ' + '界'.repeat(90_000))); }, cancel });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body)));
    await expect(readMissionStream(path, {}, signal(), vi.fn())).rejects.toThrow('上限');
    expect(cancel).toHaveBeenCalledOnce();
    expect(body.locked).toBe(false);
  });

  it('never exposes malformed JSON response bytes in a command error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('private-server-token', { status: 200 })));
    await expect(missionCommand('/api/v1/chat/mission', {}, {})).rejects.toThrow('任务响应格式无效');
  });

  it('bounds JSON before parsing and cancels excess bytes', async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new Uint8Array(262145)); }, cancel });
    await expect(readMissionJson(new Response(body))).rejects.toThrow('上限');
    expect(cancel).toHaveBeenCalledOnce();
    expect(body.locked).toBe(false);
  });
});
