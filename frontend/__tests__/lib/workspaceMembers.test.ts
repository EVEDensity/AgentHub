import { agentsFromMembers, type WorkspaceMember } from '../../lib/workspaceMembers';

describe('agentsFromMembers', () => {
  const members: WorkspaceMember[] = [
    {
      memberId: 'user-1',
      kind: 'human',
      name: 'Ada',
      role: 'member',
      adapterType: null,
      capabilities: [],
      enabled: true,
    },
    {
      memberId: 'CodeGen',
      kind: 'agent',
      name: 'CodeGen',
      role: 'agent',
      adapterType: 'desktop.local',
      capabilities: ['code-generation'],
      enabled: true,
    },
    {
      memberId: 'Review',
      kind: 'agent',
      name: 'Review',
      role: 'agent',
      adapterType: 'desktop.local',
      capabilities: ['code-review'],
      enabled: false,
    },
  ];

  test('filters out humans and disabled agents', () => {
    const result = agentsFromMembers(members);
    expect(result).toHaveLength(1);
    expect(result[0].agentId).toBe('CodeGen');
  });

  test('maps to legacy Agent shape', () => {
    const result = agentsFromMembers(members);
    expect(result[0]).toMatchObject({
      agentId: 'CodeGen',
      adapterType: 'desktop.local',
      capabilityTags: ['code-generation'],
      status: 'online',
    });
  });

  test('returns empty array when no agent members', () => {
    expect(agentsFromMembers([members[0]])).toEqual([]);
  });
});
