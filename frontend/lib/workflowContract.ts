import type { Edge, Node } from '@xyflow/react';

export type WorkflowNodeType =
  | 'start' | 'agent' | 'tool' | 'ifelse' | 'end'
  | 'code' | 'http' | 'knowledge' | 'human';

export interface WorkflowNodeContract {
  id: string;
  type: WorkflowNodeType;
  name: string;
  description: string;
  x: number;
  y: number;
  agent?: string;
  layer?: string;
  dependencies: string[];
  codeConfig?: Record<string, unknown>;
  httpConfig?: Record<string, unknown>;
  knowledgeConfig?: Record<string, unknown>;
  humanConfig?: Record<string, unknown>;
}

export interface WorkflowEdgeContract {
  id?: string;
  from: string;
  to: string;
  label?: string;
  condition?: string;
}

export interface WorkflowDocument {
  id?: number;
  name: string;
  description: string;
  triggerKeywords: string[];
  nodes: WorkflowNodeContract[];
  edges: WorkflowEdgeContract[];
  isDefault: boolean;
  active: boolean;
  version: number;
  schemaVersion: number;
}

export interface WorkflowValidationIssue {
  code: string;
  message: string;
  severity: 'error' | 'warning';
  nodeId?: string | null;
  edgeId?: string | null;
}

export interface WorkflowValidationResult {
  valid: boolean;
  normalized: Record<string, unknown> | null;
  issues: WorkflowValidationIssue[];
}

export interface WorkflowNodeData extends Record<string, unknown> {
  contract: WorkflowNodeContract;
  issues: WorkflowValidationIssue[];
}

export type ReactFlowWorkflowNode = Node<WorkflowNodeData, 'workflowNode'>;
export type ReactFlowWorkflowEdge = Edge<Record<string, unknown>>;

export const EMPTY_WORKFLOW: WorkflowDocument = {
  name: '未命名工作流',
  description: '',
  triggerKeywords: [],
  nodes: [
    { id: 'start', type: 'start', name: 'Start', description: '接收任务输入', x: 80, y: 180, dependencies: [] },
    { id: 'orchestrator', type: 'agent', name: 'Orchestrator', description: '规划并调度 Agent', x: 390, y: 180, agent: 'Orchestrator', dependencies: ['start'] },
    { id: 'end', type: 'end', name: 'End', description: '输出协作结果', x: 700, y: 180, dependencies: ['orchestrator'] },
  ],
  edges: [
    { id: 'start->orchestrator', from: 'start', to: 'orchestrator', label: 'input' },
    { id: 'orchestrator->end', from: 'orchestrator', to: 'end', label: 'result' },
  ],
  isDefault: false,
  active: true,
  version: 0,
  schemaVersion: 1,
};

export function normalizeWorkflowDocument(raw: Partial<WorkflowDocument> | null | undefined): WorkflowDocument {
  if (!raw) return cloneWorkflow(EMPTY_WORKFLOW);
  const nodes = Array.isArray(raw.nodes) ? raw.nodes.map((node) => ({
    ...node,
    x: Number.isFinite(node.x) ? node.x : 0,
    y: Number.isFinite(node.y) ? node.y : 0,
    dependencies: Array.isArray(node.dependencies) ? [...node.dependencies] : [],
  })) : [];
  const explicitEdges = Array.isArray(raw.edges) ? raw.edges : [];
  const edges = explicitEdges.length > 0
    ? explicitEdges.map((edge) => ({ ...edge, id: edge.id || edgeId(edge.from, edge.to) }))
    : nodes.flatMap((node) => node.dependencies.map((dependency) => ({
        id: edgeId(dependency, node.id), from: dependency, to: node.id, label: '',
      })));
  return {
    id: raw.id,
    name: raw.name || '未命名工作流',
    description: raw.description || '',
    triggerKeywords: Array.isArray(raw.triggerKeywords) ? raw.triggerKeywords : [],
    nodes,
    edges,
    isDefault: Boolean(raw.isDefault),
    active: raw.active ?? true,
    version: Number(raw.version || 0),
    schemaVersion: Number(raw.schemaVersion || 1),
  };
}

export function toReactFlowNodes(
  document: WorkflowDocument,
  issues: WorkflowValidationIssue[] = [],
): ReactFlowWorkflowNode[] {
  return document.nodes.map((node) => ({
    id: node.id,
    type: 'workflowNode',
    position: { x: node.x, y: node.y },
    initialWidth: 220,
    initialHeight: node.agent ? 124 : 104,
    data: { contract: node, issues: issues.filter((issue) => issue.nodeId === node.id) },
  }));
}

export function toReactFlowEdges(
  document: WorkflowDocument,
  issues: WorkflowValidationIssue[] = [],
): ReactFlowWorkflowEdge[] {
  return document.edges.map((edge) => {
    const id = edge.id || edgeId(edge.from, edge.to);
    const edgeIssues = issues.filter((issue) => issue.edgeId === id);
    const hasError = edgeIssues.some((issue) => issue.severity === 'error');
    return {
      id,
      type: 'workflowEdge',
      source: edge.from,
      target: edge.to,
      label: edge.label,
      data: { condition: edge.condition || '', issues: edgeIssues },
      animated: false,
      style: { stroke: hasError ? '#DC2626' : '#8D8B84', strokeWidth: hasError ? 3 : 2 },
      labelStyle: { fill: hasError ? '#DC2626' : '#6D6B65', fontWeight: 600 },
    };
  });
}

export function fromReactFlowGraph(
  document: WorkflowDocument,
  nodes: ReactFlowWorkflowNode[],
  edges: ReactFlowWorkflowEdge[],
): WorkflowDocument {
  const incoming = new Map<string, string[]>();
  for (const edge of edges) {
    const dependencies = incoming.get(edge.target) || [];
    if (!dependencies.includes(edge.source)) dependencies.push(edge.source);
    incoming.set(edge.target, dependencies);
  }
  return {
    ...document,
    nodes: nodes.map((node) => ({
      ...node.data.contract,
      id: node.id,
      x: node.position.x,
      y: node.position.y,
      dependencies: incoming.get(node.id) || [],
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      from: edge.source,
      to: edge.target,
      label: typeof edge.label === 'string' ? edge.label : '',
      condition: typeof edge.data?.condition === 'string' ? edge.data.condition : '',
    })),
  };
}

export function workflowFingerprint(document: WorkflowDocument): string {
  return JSON.stringify({
    ...document,
    nodes: document.nodes.map((node) => ({ ...node, dependencies: [...node.dependencies].sort() })),
    edges: document.edges.map((edge) => ({ ...edge, id: edge.id || edgeId(edge.from, edge.to) })),
  });
}

export function workflowDiffSummary(local: WorkflowDocument, remote: WorkflowDocument): string[] {
  const summary: string[] = [];
  if (local.name !== remote.name) summary.push(`名称：本地“${local.name}”，服务器“${remote.name}”`);
  if (local.nodes.length !== remote.nodes.length) summary.push(`节点：本地 ${local.nodes.length}，服务器 ${remote.nodes.length}`);
  if (local.edges.length !== remote.edges.length) summary.push(`连线：本地 ${local.edges.length}，服务器 ${remote.edges.length}`);
  const localNodeIds = new Set(local.nodes.map((node) => node.id));
  const remoteNodeIds = new Set(remote.nodes.map((node) => node.id));
  const changedIds = [...new Set([...localNodeIds, ...remoteNodeIds])]
    .filter((id) => localNodeIds.has(id) !== remoteNodeIds.has(id));
  if (changedIds.length) summary.push(`节点集合差异：${changedIds.slice(0, 6).join('、')}`);
  if (!summary.length) summary.push('图结构相同，但属性或配置存在差异');
  return summary;
}

export function cloneWorkflow(document: WorkflowDocument): WorkflowDocument {
  return JSON.parse(JSON.stringify(document)) as WorkflowDocument;
}

export function edgeId(source: string, target: string): string {
  return `${source}->${target}`;
}
