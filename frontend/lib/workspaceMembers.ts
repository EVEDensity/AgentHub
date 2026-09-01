/**
 * Fetch helpers for the v1 unified workspace member roster (P1 ADR-0108 §3.3).
 *
 * Replaces the legacy /api/agent/registry call in the chat page. The
 * roster endpoint returns both human and agent members; agents are
 * mapped to the legacy Agent shape so the existing mention panel and
 * @-trigger logic keep working without a rewrite.
 */

import { authHeaders } from './api';
import type { Agent } from '../types';

export interface WorkspaceMember {
  memberId: string;
  kind: 'human' | 'agent';
  name: string;
  role: string;
  adapterType: string | null;
  capabilities: string[];
  enabled: boolean;
}

export interface WorkspaceMembersResponse {
  scopeId: string;
  members: WorkspaceMember[];
}

/** Fetch the unified member roster for a workspace. */
export async function fetchWorkspaceMembers(
  scopeId: string,
): Promise<WorkspaceMembersResponse> {
  const res = await fetch(`/api/v1/workspaces/${encodeURIComponent(scopeId)}/members`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return (await res.json()) as WorkspaceMembersResponse;
}

/** Map agent members from the unified roster to the legacy Agent shape. */
export function agentsFromMembers(members: WorkspaceMember[]): Agent[] {
  return members
    .filter((m) => m.kind === 'agent' && m.enabled)
    .map((m) => ({
      agentId: m.memberId,
      domain: m.memberId.toLowerCase(),
      status: 'online',
      adapterType: m.adapterType || 'unknown',
      capabilityTags: m.capabilities,
    }));
}
