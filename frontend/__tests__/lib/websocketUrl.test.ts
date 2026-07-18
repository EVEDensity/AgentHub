import { afterEach, describe, expect, it } from 'vitest';
import { buildChatWebSocketUrl } from '../../lib/websocketUrl';

describe('buildChatWebSocketUrl', () => {
  afterEach(() => {
    delete process.env.NEXT_PUBLIC_WS_URL;
  });

  it('falls back to the local backend by default', () => {
    const url = buildChatWebSocketUrl('session-1', 'token-1');
    expect(url).toContain('ws://127.0.0.1:8000/ws/session-1?token=token-1');
  });

  it('normalizes http and https websocket bases', () => {
    process.env.NEXT_PUBLIC_WS_URL = 'https://example.com:8443';
    expect(buildChatWebSocketUrl('abc', 'tok')).toContain('wss://example.com:8443/ws/abc?token=tok');

    process.env.NEXT_PUBLIC_WS_URL = 'http://example.com:8001/';
    expect(buildChatWebSocketUrl('abc', 'tok')).toContain('ws://example.com:8001/ws/abc?token=tok');
  });
});
