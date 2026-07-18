import { describe, expect, it } from 'vitest';
import {
  mergeFinalMessage,
  mergeReloadedMessages,
  registerReplayMessageId,
} from '../../lib/messageRecovery';
import type { Message } from '../../types';

describe('messageRecovery helpers', () => {
  it('merges reloaded messages without duplicating finalized ids', () => {
    const prev: Message[] = [
      {
        event: 'message',
        sessionId: 's1',
        sender: 'agent',
        content: 'streaming',
        type: 'text',
        timestamp: '2026-07-19T00:00:00.000Z',
        isStreaming: true,
      },
      {
        event: 'message',
        sessionId: 's1',
        sender: 'user',
        content: 'done',
        type: 'text',
        timestamp: '2026-07-19T00:00:01.000Z',
        id: 'm1',
      },
    ];
    const merged = mergeReloadedMessages(prev, [
      {
        event: 'message',
        sessionId: 's1',
        sender: 'user',
        content: 'done',
        type: 'text',
        timestamp: '2026-07-19T00:00:01.000Z',
        id: 'm1',
      },
      {
        event: 'message',
        sessionId: 's1',
        sender: 'agent',
        content: 'fresh',
        type: 'text',
        timestamp: '2026-07-19T00:00:02.000Z',
        id: 'm2',
      },
    ]);

    expect(merged.some((m) => m.content === 'streaming')).toBe(false);
    expect(merged.map((m) => m.id)).toContain('m2');
  });

  it('replaces streaming placeholder with the final message payload', () => {
    const merged = mergeFinalMessage([
      {
        event: 'message',
        sessionId: 's1',
        sender: 'agent',
        content: 'tools done',
        type: 'text',
        timestamp: '2026-07-19T00:00:00.000Z',
        messageId: 'mid-1',
        isStreaming: true,
      },
    ], {
      event: 'message',
      sessionId: 's1',
      sender: 'agent',
      content: 'final answer',
      type: 'text',
      timestamp: '2026-07-19T00:00:01.000Z',
      messageId: 'mid-1',
      id: 'server-1',
    });

    expect(merged).toHaveLength(1);
    expect(merged[0].content).toBe('final answer');
    expect(merged[0].isStreaming).toBe(false);
  });

  it('keeps replay dedup ids bounded', () => {
    let seen = new Set<string>();
    for (let i = 0; i < 520; i += 1) {
      seen = registerReplayMessageId(seen, `m-${i}`);
    }
    expect(seen.size).toBeLessThanOrEqual(500);
  });
});
