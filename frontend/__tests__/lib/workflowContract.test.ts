import { describe, expect, it } from 'vitest';

import {
  fromReactFlowGraph,
  normalizeWorkflowDocument,
  toReactFlowEdges,
  toReactFlowNodes,
  workflowDiffSummary,
} from '../../lib/workflowContract';

describe('workflow ReactFlow contract adapter', () => {
  it('round-trips explicit edge identity, labels, conditions, and dependencies', () => {
    const document = normalizeWorkflowDocument({
      name: 'review',
      version: 4,
      schemaVersion: 1,
      nodes: [
        { id: 'plan', type: 'agent', name: 'Plan', description: '', x: 10, y: 20, agent: 'Architect', dependencies: [] },
        { id: 'review', type: 'human', name: 'Review', description: '', x: 320, y: 20, dependencies: ['plan'], humanConfig: { prompt: 'Approve?' } },
      ],
      edges: [{ id: 'approval-edge', from: 'plan', to: 'review', label: 'approve', condition: 'score > 0.8' }],
    });

    const nodes = toReactFlowNodes(document);
    const edges = toReactFlowEdges(document);
    const restored = fromReactFlowGraph(document, nodes, edges);

    expect(restored.edges).toEqual([
      { id: 'approval-edge', from: 'plan', to: 'review', label: 'approve', condition: 'score > 0.8' },
    ]);
    expect(restored.nodes.find((node) => node.id === 'review')?.dependencies).toEqual(['plan']);
  });

  it('projects validation issues onto the matching node and edge', () => {
    const document = normalizeWorkflowDocument({
      nodes: [{ id: 'a', type: 'agent', name: 'A', description: '', x: 0, y: 0, dependencies: [] }],
      edges: [{ id: 'broken', from: 'a', to: 'missing' }],
    });
    const issues = [
      { code: 'agent_unassigned', message: 'Agent missing', severity: 'warning' as const, nodeId: 'a' },
      { code: 'missing_edge_target', message: 'Target missing', severity: 'error' as const, edgeId: 'broken' },
    ];

    expect(toReactFlowNodes(document, issues)[0].data.issues).toHaveLength(1);
    const edge = toReactFlowEdges(document, issues)[0];
    expect(edge.data?.issues).toHaveLength(1);
    expect(edge.style?.stroke).toBe('#DC2626');
  });

  it('summarizes graph differences for conflict comparison', () => {
    const local = normalizeWorkflowDocument({ name: 'local', nodes: [], edges: [] });
    const remote = normalizeWorkflowDocument({ name: 'remote', nodes: [], edges: [] });

    expect(workflowDiffSummary(local, remote)[0]).toContain('local');
  });
});
