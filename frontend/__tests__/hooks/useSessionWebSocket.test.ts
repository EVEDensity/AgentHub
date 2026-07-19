import { describe, expect, it } from 'vitest';
import { computeReconnectDelay } from '../../hooks/useSessionWebSocket';

describe('useSessionWebSocket helpers', () => {
  it('computes capped reconnect backoff with deterministic jitter', () => {
    expect(computeReconnectDelay(0, 0)).toBe(1000);
    expect(computeReconnectDelay(3, 125)).toBe(8125);
    expect(computeReconnectDelay(99, 250)).toBe(30250);
  });
});
