'use client';

import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  getBezierPath,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type EdgeProps,
  type NodeProps,
} from '@xyflow/react';
import {
  AlertCircle, Check, Cloud, CloudOff, GitBranch, Plus, RotateCcw, Save, Trash2, X,
} from 'lucide-react';
import { useEffect, useMemo, useState, type JSX } from 'react';

import { useWorkflowEditorSession } from '../../hooks/useWorkflowEditorSession';
import {
  edgeId,
  fromReactFlowGraph,
  toReactFlowEdges,
  toReactFlowNodes,
  type ReactFlowWorkflowEdge,
  type ReactFlowWorkflowNode,
  type WorkflowNodeContract,
  type WorkflowNodeType,
  type WorkflowValidationIssue,
} from '../../lib/workflowContract';
import NodeConfigPanel from './NodeConfigPanel';
import { WorkflowConflictDialog } from './WorkflowConflictDialog';

const NODE_LIBRARY: Array<{ type: WorkflowNodeType; label: string; color: string }> = [
  { type: 'start', label: 'Start', color: '#22A06B' },
  { type: 'agent', label: 'Agent', color: '#4F6CF7' },
  { type: 'tool', label: 'Tool', color: '#8B5CF6' },
  { type: 'ifelse', label: 'IF / ELSE', color: '#D97706' },
  { type: 'code', label: 'Code', color: '#0EA5E9' },
  { type: 'http', label: 'HTTP', color: '#DB2777' },
  { type: 'knowledge', label: 'Knowledge', color: '#0F766E' },
  { type: 'human', label: 'Human', color: '#B45309' },
  { type: 'end', label: 'End', color: '#64748B' },
];

const NODE_COLORS = Object.fromEntries(NODE_LIBRARY.map((item) => [item.type, item.color]));

function WorkflowNode({ data, selected }: NodeProps<ReactFlowWorkflowNode>): JSX.Element {
  const node = data.contract;
  const errors = data.issues.filter((issue) => issue.severity === 'error');
  const warnings = data.issues.filter((issue) => issue.severity === 'warning');
  const color = NODE_COLORS[node.type] || '#64748B';
  const invalid = errors.length > 0;
  const title = [...errors, ...warnings].map((issue) => issue.message).join('\n');
  return (
    <div
      className={`relative w-[220px] border bg-white px-4 py-3 shadow-sm ${invalid ? 'border-danger-500' : selected ? 'border-primary-500' : 'border-warm-200'}`}
      style={{ borderTopWidth: 4, borderTopColor: invalid ? '#DC2626' : color }}
      title={title || undefined}
    >
      <Handle type="target" position={Position.Left} className="!h-3 !w-3 !border-2 !border-white" style={{ background: color }} />
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] font-bold uppercase" style={{ color }}>{node.type}</span>
        {(errors.length > 0 || warnings.length > 0) && (
          <AlertCircle size={15} className={invalid ? 'text-danger-500' : 'text-amber-500'} aria-label="节点校验问题" />
        )}
      </div>
      <div className="mt-1 truncate text-sm font-semibold text-warm-800">{node.name || node.id}</div>
      <div className="mt-1 line-clamp-2 min-h-8 text-[11px] leading-4 text-warm-500">{node.description || '未配置描述'}</div>
      {node.agent && <div className="mt-2 truncate text-[10px] text-warm-400">{node.agent}</div>}
      <Handle type="source" position={Position.Right} className="!h-3 !w-3 !border-2 !border-white" style={{ background: color }} />
    </div>
  );
}

function WorkflowEdge(props: EdgeProps<ReactFlowWorkflowEdge>): JSX.Element {
  const [path, labelX, labelY] = getBezierPath(props);
  const issues = (props.data?.issues || []) as WorkflowValidationIssue[];
  const invalid = issues.some((issue) => issue.severity === 'error');
  return (
    <>
      <BaseEdge path={path} markerEnd={props.markerEnd} style={props.style} />
      <EdgeLabelRenderer>
        <div
          className={`nodrag nopan absolute border bg-white px-2 py-1 text-[10px] shadow-sm ${invalid ? 'border-danger-400 text-danger-600' : 'border-warm-200 text-warm-600'}`}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)` }}
          title={issues.map((issue) => issue.message).join('\n') || undefined}
        >
          {invalid && <AlertCircle size={11} className="mr-1 inline" />}
          {typeof props.label === 'string' && props.label ? props.label : 'flow'}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

function initialNode(type: WorkflowNodeType, index: number): WorkflowNodeContract {
  const name = NODE_LIBRARY.find((item) => item.type === type)?.label || type;
  const config = type === 'code'
    ? { codeConfig: { language: 'python', code: '# Write code here\n' } }
    : type === 'http'
      ? { httpConfig: { method: 'GET', url: 'https://' } }
      : type === 'knowledge'
        ? { knowledgeConfig: { collectionId: '', query: '' } }
        : type === 'human'
          ? { humanConfig: { prompt: '请审核以下内容：' } }
          : {};
  return {
    id: `${type}-${Date.now()}`,
    type,
    name,
    description: '',
    x: 160 + (index % 3) * 260,
    y: 100 + Math.floor(index / 3) * 180,
    dependencies: [],
    agent: type === 'agent' ? 'Orchestrator' : undefined,
    ...config,
  };
}

export default function ReactFlowWorkflowEditor({ workflowId }: { workflowId?: number }): JSX.Element {
  const session = useWorkflowEditorSession(workflowId);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const projectedNodes = useMemo(() => toReactFlowNodes(session.document, session.issues), [session.document, session.issues]);
  const projectedEdges = useMemo(() => toReactFlowEdges(session.document, session.issues).map((edge) => ({
    ...edge,
    markerEnd: { type: MarkerType.ArrowClosed, color: String(edge.style?.stroke || '#8D8B84') },
  })), [session.document, session.issues]);
  const [nodes, setNodes] = useNodesState<ReactFlowWorkflowNode>(projectedNodes);
  const [edges, setEdges] = useEdgesState<ReactFlowWorkflowEdge>(projectedEdges);
  const selectedNode = session.document.nodes.find((node) => node.id === selectedNodeId) || null;
  const errorCount = session.issues.filter((issue) => issue.severity === 'error').length;

  useEffect(() => {
    setNodes((current) => projectedNodes.map((node) => {
      const existing = current.find((item) => item.id === node.id);
      return existing ? { ...node, measured: existing.measured, selected: existing.selected } : node;
    }));
  }, [projectedNodes, setNodes]);

  useEffect(() => {
    setEdges(projectedEdges);
  }, [projectedEdges, setEdges]);

  function updateGraph(nextNodes: ReactFlowWorkflowNode[], nextEdges: ReactFlowWorkflowEdge[]): void {
    setNodes(nextNodes);
    setEdges(nextEdges);
    session.setDocument(fromReactFlowGraph(session.document, nextNodes, nextEdges));
  }

  function patchNode(id: string, patch: Record<string, unknown>): void {
    session.setDocument({
      ...session.document,
      nodes: session.document.nodes.map((node) => node.id === id ? { ...node, ...patch } : node),
    });
  }

  function addNode(type: WorkflowNodeType): void {
    const node = initialNode(type, session.document.nodes.length);
    session.setDocument({ ...session.document, nodes: [...session.document.nodes, node] });
    setSelectedNodeId(node.id);
  }

  const statusIcon = session.status === 'error'
    ? <CloudOff size={14} className="text-danger-500" />
    : session.status === 'saved' || session.status === 'clean'
      ? <Check size={14} className="text-success-500" />
      : <Cloud size={14} className="text-primary-500" />;

  if (!session.ready) {
    return <div className="flex h-screen items-center justify-center bg-warm-50 text-sm text-warm-500">正在加载工作流...</div>;
  }

  return (
    <div className="flex h-screen min-h-[640px] flex-col bg-warm-50 text-warm-800">
      <header className="flex min-h-16 flex-wrap items-center gap-2 border-b border-warm-200 bg-white px-4 py-2">
        <input
          aria-label="工作流名称"
          className="input-field h-9 w-44 text-sm"
          value={session.document.name}
          onChange={(event) => session.setDocument({ ...session.document, name: event.target.value })}
        />
        <input
          aria-label="工作流描述"
          className="input-field h-9 min-w-48 flex-1 text-sm"
          placeholder="工作流描述"
          value={session.document.description}
          onChange={(event) => session.setDocument({ ...session.document, description: event.target.value })}
        />
        <input
          aria-label="触发关键词"
          className="input-field h-9 w-52 text-sm"
          placeholder="关键词，逗号分隔"
          value={session.document.triggerKeywords.join(', ')}
          onChange={(event) => session.setDocument({
            ...session.document,
            triggerKeywords: event.target.value.split(',').map((item) => item.trim()).filter(Boolean),
          })}
        />
        <div className="flex h-9 items-center gap-1.5 border border-warm-200 bg-warm-50 px-3 text-xs text-warm-500">
          {statusIcon}{session.status === 'saving' ? '保存中' : session.status === 'dirty' ? '等待保存' : session.status === 'error' ? '保存失败' : '草稿已同步'}
        </div>
        {errorCount > 0 && (
          <div className="flex h-9 items-center gap-1.5 border border-danger-200 bg-danger-50 px-3 text-xs text-danger-600">
            <AlertCircle size={14} /> {errorCount} 个问题
          </div>
        )}
        <button className="btn-secondary flex h-9 items-center gap-1.5 px-3 text-xs" onClick={() => void session.discardDraft()} title="丢弃草稿">
          <RotateCcw size={14} /> 丢弃草稿
        </button>
        <button className="btn-primary flex h-9 items-center gap-1.5 px-4 text-xs" onClick={() => void session.publish()}>
          <Save size={14} /> 发布
        </button>
      </header>

      <div className="relative flex min-h-0 flex-1">
        <aside className="hidden w-[196px] shrink-0 overflow-y-auto border-r border-warm-200 bg-white p-3 lg:block">
          <a href="/admin?menu=工作流" className="mb-4 flex items-center gap-1 text-xs text-primary-600"><GitBranch size={14} /> 工作流管理</a>
          <div className="mb-2 text-[10px] font-semibold uppercase text-warm-400">节点</div>
          <div className="grid grid-cols-2 gap-2">
            {NODE_LIBRARY.map((item) => (
              <button
                key={item.type}
                className="flex min-h-14 flex-col items-start justify-center border border-warm-200 bg-warm-50 px-2 text-left hover:border-primary-300"
                onClick={() => addNode(item.type)}
              >
                <span className="flex items-center gap-1 text-xs font-semibold" style={{ color: item.color }}><Plus size={12} /> {item.label}</span>
                <span className="mt-1 text-[9px] text-warm-400">{item.type}</span>
              </button>
            ))}
          </div>
        </aside>

        <main className="min-w-0 flex-1">
          <ReactFlow<ReactFlowWorkflowNode, ReactFlowWorkflowEdge>
            nodes={nodes}
            edges={edges}
            nodeTypes={{ workflowNode: WorkflowNode }}
            edgeTypes={{ workflowEdge: WorkflowEdge }}
            onNodesChange={(changes) => {
              const nextNodes = applyNodeChanges(changes, nodes);
              const ids = new Set(nextNodes.map((node) => node.id));
              updateGraph(nextNodes, edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)));
            }}
            onEdgesChange={(changes) => updateGraph(nodes, applyEdgeChanges(changes, edges))}
            onConnect={(connection: Connection) => {
              if (!connection.source || !connection.target || connection.source === connection.target) return;
              if (edges.some((edge) => edge.source === connection.source && edge.target === connection.target)) return;
              const next = addEdge({
                ...connection,
                id: edgeId(connection.source, connection.target),
                type: 'workflowEdge',
                label: 'flow',
              }, edges);
              updateGraph(nodes, next);
            }}
            onNodeClick={(_, node) => setSelectedNodeId(node.id)}
            onPaneClick={() => setSelectedNodeId(null)}
            fitView
            minZoom={0.25}
            maxZoom={2}
            deleteKeyCode={['Backspace', 'Delete']}
          >
            <Background color="#D8D6CF" gap={24} size={1} />
            <MiniMap
              pannable
              zoomable
              nodeColor={(node) => NODE_COLORS[session.document.nodes.find((item) => item.id === node.id)?.type || 'end'] || '#64748B'}
            />
            <Controls showInteractive={false} />
            <Panel position="top-left" className="lg:hidden">
              <select
                aria-label="添加节点"
                className="h-9 border border-warm-200 bg-white px-2 text-xs text-warm-700 shadow-sm"
                defaultValue=""
                onChange={(event) => {
                  if (event.target.value) addNode(event.target.value as WorkflowNodeType);
                  event.target.value = '';
                }}
              >
                <option value="" disabled>+ 添加节点</option>
                {NODE_LIBRARY.map((item) => <option key={item.type} value={item.type}>{item.label}</option>)}
              </select>
            </Panel>
            {session.message && <Panel position="bottom-center" className="border border-warm-200 bg-white px-3 py-2 text-xs text-warm-600 shadow-sm">{session.message}</Panel>}
          </ReactFlow>
        </main>

        <aside className={`${selectedNode ? 'flex' : 'hidden'} absolute inset-y-0 right-0 z-20 w-[min(300px,90vw)] shrink-0 flex-col overflow-y-auto border-l border-warm-200 bg-white p-4 shadow-lg md:static md:flex md:w-[280px] md:shadow-none xl:w-[300px]`}>
          {selectedNode ? (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold">节点属性</h2>
                <div className="flex items-center gap-3">
                  <button aria-label="关闭属性面板" className="text-warm-400 md:hidden" onClick={() => setSelectedNodeId(null)}><X size={16} /></button>
                  <button
                    aria-label="删除节点"
                    className="text-danger-500 hover:text-danger-600"
                    onClick={() => session.setDocument({
                      ...session.document,
                      nodes: session.document.nodes.filter((node) => node.id !== selectedNode.id),
                      edges: session.document.edges.filter((edge) => edge.from !== selectedNode.id && edge.to !== selectedNode.id),
                    })}
                  ><Trash2 size={16} /></button>
                </div>
              </div>
              <label className="block text-[10px] text-warm-400">名称</label>
              <input className="input-field text-sm" value={selectedNode.name} onChange={(event) => patchNode(selectedNode.id, { name: event.target.value })} />
              <label className="block text-[10px] text-warm-400">描述</label>
              <textarea className="input-field min-h-20 text-sm" value={selectedNode.description} onChange={(event) => patchNode(selectedNode.id, { description: event.target.value })} />
              {selectedNode.type === 'agent' && (
                <>
                  <label className="block text-[10px] text-warm-400">Agent</label>
                  <input className="input-field text-sm" value={selectedNode.agent || ''} onChange={(event) => patchNode(selectedNode.id, { agent: event.target.value })} />
                </>
              )}
              <NodeConfigPanel node={selectedNode} onPatch={patchNode} />
              {session.issues.filter((issue) => issue.nodeId === selectedNode.id).map((issue) => (
                <div key={`${issue.code}-${issue.message}`} className={`border px-3 py-2 text-xs ${issue.severity === 'error' ? 'border-danger-200 bg-danger-50 text-danger-600' : 'border-amber-200 bg-amber-50 text-amber-700'}`}>
                  {issue.message}
                </div>
              ))}
            </div>
          ) : (
            <div className="py-12 text-center text-xs text-warm-400">选择节点后编辑属性</div>
          )}
        </aside>
      </div>

      <WorkflowConflictDialog
        conflict={session.conflict}
        onClose={session.dismissConflict}
        onReload={session.reloadConflictRemote}
        onOverwrite={session.overwriteConflict}
      />
    </div>
  );
}
