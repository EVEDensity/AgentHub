import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { buildSocketDedupKey, computeReconnectDelay, useSessionWebSocket } from '../../hooks/useSessionWebSocket';

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readyState = MockWebSocket.CONNECTING;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;

  constructor(readonly url: string) {
    MockWebSocket.instances.push(this);
  }

  send(payload: string): void { this.sent.push(payload); }
  close(): void { this.readyState = MockWebSocket.CLOSED; }
  open(): void { this.readyState = MockWebSocket.OPEN; this.onopen?.(); }
  disconnect(): void { this.readyState = MockWebSocket.CLOSED; this.onclose?.(); }
  emit(payload: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>);
  }
}

describe('useSessionWebSocket helpers', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    MockWebSocket.instances = [];
  });

  it('computes capped reconnect backoff with deterministic jitter', () => {
    expect(computeReconnectDelay(0, 0)).toBe(1000);
    expect(computeReconnectDelay(3, 125)).toBe(8125);
    expect(computeReconnectDelay(99, 250)).toBe(30250);
  });

  it('deduplicates replayable events without collapsing unsequenced stream chunks', () => {
    expect(buildSocketDedupKey('task_preview', { messageId: 'preview-1' })).toBe('task_preview:preview-1');
    expect(buildSocketDedupKey('message_chunk', { messageId: 'stream-1' })).toBe('');
    expect(buildSocketDedupKey('message_chunk', { messageId: 'stream-1', sequence: 2 })).toBe('message_chunk:stream-1:2');
  });

  it('uses the latest replayable event id as the reconnect cursor', async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, 'random').mockReturnValue(0);
    vi.stubGlobal('WebSocket', MockWebSocket);
    const wsRef = { current: new Map<string, WebSocket>() };
    const currentSessionRef = { current: 'session-1' };
    const tokenRef = { current: 'token' };

    const { result, unmount } = renderHook(() => useSessionWebSocket({
      wsRef,
      currentSessionRef,
      tokenRef,
      setConnected: vi.fn(),
      setNotice: vi.fn(),
      addToast: vi.fn(),
      onSessionClosed: vi.fn(),
      onMessage: vi.fn(),
    }));

    act(() => result.current.connectSession('session-1'));
    const first = MockWebSocket.instances[0];
    act(() => first.open());
    act(() => first.emit({ event: 'task_preview', messageId: 'preview-cursor-1', sessionId: 'session-1' }));
    act(() => first.disconnect());
    await act(async () => { vi.advanceTimersByTime(1000); });

    const reconnected = MockWebSocket.instances[1];
    act(() => reconnected.open());
    expect(reconnected.sent.map((payload) => JSON.parse(payload))).toContainEqual({
      event: 'sync_request',
      lastMessageId: 'preview-cursor-1',
    });

    unmount();
  });
});
