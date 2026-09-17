import type { Agent, ChatSession } from '../types';

/**
 * Roster of built-in agents and session-sort helpers.
 *
 * Extracted from the page shell (R3/R4 hot-module thinning) so the constants
 * and pure helpers can be unit-tested and reused outside the IM page.
 */

export const AGENTS = ['Orchestrator', 'Architect', 'CodeGen', 'Review', 'Test', 'Deploy', 'Implement'] as const;

export const FALLBACK_AGENTS: Agent[] = AGENTS.map((agentId) => ({
  agentId,
  domain: agentId.toLowerCase(),
  status: 'sleeping',
  adapterType: 'mock',
  riskLevel:
    agentId === 'Deploy' ? 'L3' : agentId === 'CodeGen' || agentId === 'Orchestrator' ? 'L2' : 'L1',
}));

/** Sort sessions: pinned first, then by last activity (desc). */
export function sortSessions(items: ChatSession[]): ChatSession[] {
  return [...items].sort((a, b) => {
    const pinDiff = (b.isPinned || 0) - (a.isPinned || 0);
    if (pinDiff !== 0) return pinDiff;
    const aTime = a.lastMessageAt || a.createdAt || '';
    const bTime = b.lastMessageAt || b.createdAt || '';
    return bTime.localeCompare(aTime);
  });
}