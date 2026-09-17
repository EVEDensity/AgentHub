import { describe, expect, it } from 'vitest';
import { AGENTS, FALLBACK_AGENTS, sortSessions } from '../../lib/agents';

describe('lib/agents', () => {
  it('AGENTS matches the built-in roster', () => {
    expect(AGENTS).toEqual(['Orchestrator', 'Architect', 'CodeGen', 'Review', 'Test', 'Deploy', 'Implement']);
  });

  it('FALLBACK_AGENTS derives domain/status/adapter from every agent id', () => {
    expect(FALLBACK_AGENTS).toHaveLength(AGENTS.length);
    for (const agent of FALLBACK_AGENTS) {
      expect(agent.domain).toBe(agent.agentId.toLowerCase());
      expect(agent.status).toBe('sleeping');
      expect(agent.adapterType).toBe('mock');
    }
    const deploy = FALLBACK_AGENTS.find((a) => a.agentId === 'Deploy');
    expect(deploy?.riskLevel).toBe('L3');
  });

  it('sortSessions pins first then by latest activity', () => {
    const sessions = [
      { id: 'a', isPinned: 0, lastMessageAt: '2026-08-01T00:00:00Z', createdAt: '2026-08-01T00:00:00Z' },
      { id: 'b', isPinned: 1, lastMessageAt: '2026-08-02T00:00:00Z', createdAt: '2026-08-01T00:00:00Z' },
      { id: 'c', isPinned: 0, lastMessageAt: '2026-08-03T00:00:00Z', createdAt: '2026-08-01T00:00:00Z' },
    ];
    const sorted = sortSessions(sessions as Parameters<typeof sortSessions>[0]);
    expect(sorted.map((s) => s.id)).toEqual(['b', 'c', 'a']);
  });
});