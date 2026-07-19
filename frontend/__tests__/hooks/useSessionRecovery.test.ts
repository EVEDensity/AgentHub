import { describe, expect, it } from 'vitest';
import { sortMessagesByTimestamp } from '../../hooks/useSessionRecovery';
import type { Message } from '../../types';

describe('useSessionRecovery helpers', () => {
  it('sorts reloaded messages by timestamp without mutating input', () => {
    const messages: Message[] = [
      {
        event: 'message',
        sessionId: 's1',
        sender: 'agent',
        content: 'later',
        type: 'text',
        timestamp: '2026-07-19T00:00:02.000Z',
      },
      {
        event: 'message',
        sessionId: 's1',
        sender: 'agent',
        content: 'earlier',
        type: 'text',
        timestamp: '2026-07-19T00:00:01.000Z',
      },
    ];

    const sorted = sortMessagesByTimestamp(messages);

    expect(sorted.map((m) => m.content)).toEqual(['earlier', 'later']);
    expect(messages.map((m) => m.content)).toEqual(['later', 'earlier']);
  });
});
