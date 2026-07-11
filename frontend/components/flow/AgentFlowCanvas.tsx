import { useCallback, useEffect, useRef, useState, type JSX } from 'react';

interface FlowNode {
  id: string;
  type: 'start' | 'agent' | 'tool' | 'ifelse' | 'end';
  name: string;
  description: string;
  x: number;
  y: number;
  agent?: string;
  layer?: string;
  dependencies: string[];
}

interface FlowEdge {
  from: string;
  to: string;
  label?: string;
}

interface AgentFlowData {
  id?: number;
  name: string;
  description: string;
  triggerKeywords: string[];
  nodes: FlowNode[];
  edges: FlowEdge[];
  isDefault: boolean;
  active: boolean;
}

const NODE_TYPES = [
  { type: 'start' as const, label: 'Start', color: '#4F6CF7', desc: '接收用户输入' },
  { type: 'agent' as const, label: 'Agent', color: '#8B5CF6', desc: 'LLM 角色节点' },
  { type: 'tool' as const, label: 'Tool', color: '#10B981', desc: '调用外部工具/API' },
  { type: 'ifelse' as const, label: 'IF / ELSE', color: '#F59E0B', desc: '条件分支判断' },
  { type: 'end' as const, label: 'End', color: '#6B7280', desc: '输出最终结果' },
];

const CANVAS_WIDTH = 2000;
const CANVAS_HEIGHT = 1200;

export default function AgentFlowCanvas({
  initialData,
  agents,
  onSave,
  onDelete,
}: {
  initialData?: AgentFlowData;
  agents: { agentId: string; domain: string }[];
  onSave: (data: AgentFlowData) => void;
  onDelete?: () => void;
}): JSX.Element {
  const [name, setName] = useState(initialData?.name || '');
  const [description, setDescription] = useState(initialData?.description || '');
  const [triggerKeywords, setTriggerKeywords] = useState(initialData?.triggerKeywords?.join(',') || '');
  const [nodes, setNodes] = useState<FlowNode[]>(initialData?.nodes || []);
  const [edges, setEdges] = useState<FlowEdge[]>(initialData?.edges || []);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const [connectFrom, setConnectFrom] = useState<string | null>(null);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [isDraggingCanvas, setIsDraggingCanvas] = useState(false);
  const [dragNodeId, setDragNodeId] = useState<string | null>(null);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const canvasRef = useRef<HTMLDivElement>(null);

  const selectedNode = nodes.find((n) => n.id === selectedNodeId) || null;

  function addNode(type: FlowNode['type']) {
    const id = `${type}-${Date.now()}`;
    const x = 400 + nodes.length * 40;
    const y = 300 + nodes.length * 30;
    const defaultNames: Record<string, string> = {
      start: 'Start',
      agent: 'Agent',
      tool: 'Tool',
      ifelse: 'IF / ELSE',
      end: 'End',
    };
    const newNode: FlowNode = {
      id,
      type,
      name: defaultNames[type],
      description: '',
      x,
      y,
      agent: type === 'agent' ? agents[0]?.agentId : undefined,
      layer: 'domain',
      dependencies: [],
    };
    setNodes((prev) => [...prev, newNode]);
    setSelectedNodeId(id);
  }

  function updateNode(id: string, patch: Partial<FlowNode>) {
    setNodes((prev) => prev.map((n) => (n.id === id ? { ...n, ...patch } : n)));
  }

  function removeNode(id: string) {
    setNodes((prev) => prev.filter((n) => n.id !== id));
    setEdges((prev) => prev.filter((e) => e.from !== id && e.to !== id));
    if (selectedNodeId === id) setSelectedNodeId(null);
  }

  function handleNodeClick(id: string) {
    if (isConnecting) {
      if (connectFrom && connectFrom !== id) {
        setEdges((prev) => {
          const exists = prev.some((e) => e.from === connectFrom && e.to === id);
          if (exists) return prev;
          return [...prev, { from: connectFrom, to: id }];
        });
      }
      setIsConnecting(false);
      setConnectFrom(null);
      return;
    }
    setSelectedNodeId(id);
  }

  function startConnect(id: string) {
    setIsConnecting(true);
    setConnectFrom(id);
  }

  function removeEdge(from: string, to: string) {
    setEdges((prev) => prev.filter((e) => !(e.from === from && e.to === to)));
  }

  function handleSave() {
    const data: AgentFlowData = {
      id: initialData?.id,
      name: name.trim() || '未命名工作流',
      description: description.trim(),
      triggerKeywords: triggerKeywords.split(',').map((k) => k.trim()).filter(Boolean),
      nodes,
      edges,
      isDefault: initialData?.isDefault || false,
      active: initialData?.active ?? true,
    };
    onSave(data);
  }

  function handleDelete() {
    if (!initialData?.id) return;
    if (window.confirm(`确认删除工作流 "${name}"？此操作不可撤销。`)) {
      onDelete?.();
    }
  }

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      const target = e.target as HTMLElement;
      const canvasElement = canvasRef.current;
      if (!canvasElement) return;

      const isOnCanvasBackground = 
        target === canvasElement || 
        (target.classList.contains('canvas-grid') && !target.classList.contains('flow-node') && !target.classList.contains('flow-edge'));

      if (isOnCanvasBackground) {
        setIsDraggingCanvas(true);
        setDragStart({ x: e.clientX - offset.x, y: e.clientY - offset.y });
        setSelectedNodeId(null);
      }
    },
    [offset]
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (isDraggingCanvas) {
        setOffset({ x: e.clientX - dragStart.x, y: e.clientY - dragStart.y });
      }
      if (dragNodeId) {
        const rect = canvasRef.current?.getBoundingClientRect();
        if (!rect) return;
        const x = (e.clientX - rect.left - offset.x) / scale;
        const y = (e.clientY - rect.top - offset.y) / scale;
        updateNode(dragNodeId, { x, y });
      }
    },
    [isDraggingCanvas, dragStart, dragNodeId, offset, scale]
  );

  const handleMouseUp = useCallback(() => {
    setIsDraggingCanvas(false);
    setDragNodeId(null);
  }, []);

  const handleMouseLeave = useCallback(() => {
    setIsDraggingCanvas(false);
    setDragNodeId(null);
  }, []);

  function handleWheel(e: React.WheelEvent) {
    e.preventDefault();
    const delta = e.deltaY > 0 ? -0.1 : 0.1;
    setScale((prev) => Math.max(0.3, Math.min(2, prev + delta)));
  }

  function getNodeColor(type: string): string {
    const t = NODE_TYPES.find((n) => n.type === type);
    return t?.color || '#6B7280';
  }

  return (
    <div className="flex h-[calc(100vh-73px)] flex-col">
      {/* Toolbar */}
      <div className="border-b border-warm-150 bg-warm-100 px-4 py-3">
        <div className="flex flex-wrap items-center gap-3 xl:flex-nowrap xl:justify-between">
          {/* ① 左侧：功能模式标签区 */}
          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-xl border border-warm-150 bg-warm-50/60 px-2 py-2">
            <input
              className="h-10 min-w-[150px] flex-1 rounded-lg border border-warm-200 bg-warm-100 px-3 text-sm leading-none text-warm-700 placeholder:text-warm-400 focus:border-primary-300 focus:outline-none focus:ring-2 focus:ring-primary-100"
              placeholder="工作流名称"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <input
              className="h-10 min-w-[210px] flex-[1.2] rounded-lg border border-warm-200 bg-warm-100 px-3 text-sm leading-none text-warm-700 placeholder:text-warm-400 focus:border-primary-300 focus:outline-none focus:ring-2 focus:ring-primary-100"
              placeholder="描述"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
            <input
              className="h-10 min-w-[200px] flex-1 rounded-lg border border-warm-200 bg-warm-100 px-3 text-sm leading-none text-warm-700 placeholder:text-warm-400 focus:border-primary-300 focus:outline-none focus:ring-2 focus:ring-primary-100"
              placeholder="触发关键词（逗号分隔）"
              value={triggerKeywords}
              onChange={(e) => setTriggerKeywords(e.target.value)}
            />
          </div>

          {/* ② 中间：画布控制区 */}
          <div className="flex items-center gap-2 rounded-xl border border-warm-150 bg-warm-50/60 px-2 py-2">
            <button className="btn-secondary h-10 min-w-[76px] whitespace-nowrap rounded-lg px-4 text-sm" onClick={() => setScale((s) => Math.max(0.3, s - 0.1))}>
              缩小
            </button>
            <span className="inline-flex h-10 min-w-[64px] items-center justify-center rounded-lg border border-warm-200 bg-warm-100 px-2 text-xs font-medium text-warm-600">
              {Math.round(scale * 100)}%
            </span>
            <button className="btn-secondary h-10 min-w-[76px] whitespace-nowrap rounded-lg px-4 text-sm" onClick={() => setScale((s) => Math.min(2, s + 0.1))}>
              放大
            </button>
            <button className="btn-secondary h-10 min-w-[98px] whitespace-nowrap rounded-lg px-4 text-sm" onClick={() => { setScale(1); setOffset({ x: 0, y: 0 }); }}>
              重置视角
            </button>
          </div>

          {/* ③ 右侧：操作按钮区 */}
          <div className="ml-auto flex items-center gap-2 rounded-xl border border-warm-150 bg-warm-100 px-2 py-2">
            {initialData?.id && (
              <button
                className="h-10 min-w-[98px] whitespace-nowrap rounded-lg border border-danger-200 bg-danger-50 px-4 text-sm font-medium text-danger-600 transition hover:bg-danger-100 active:translate-y-px"
                onClick={handleDelete}
              >
                删除工作流
              </button>
            )}
            <button className="btn-primary h-10 min-w-[76px] whitespace-nowrap rounded-lg px-5 text-sm font-semibold shadow-sm shadow-primary-200/70" onClick={handleSave}>
              保存
            </button>
          </div>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Left Sidebar - Blocks */}
        <div className="w-56 flex-none border-r border-warm-150 bg-warm-50 p-4">
          <div className="mb-3 text-xs font-medium text-warm-500">BLOCKS</div>
          <div className="space-y-2">
            {NODE_TYPES.map((t) => (
              <button
                key={t.type}
                className="flex w-full items-center gap-2 rounded-lg bg-warm-100 px-3 py-2 text-left text-sm text-warm-700 shadow-sm hover:bg-warm-100"
                onClick={() => addNode(t.type)}
              >
                <span className="h-3 w-3 rounded-full" style={{ background: t.color }} />
                <div>
                  <div className="font-medium">{t.label}</div>
                  <div className="text-[10px] text-warm-400">{t.desc}</div>
                </div>
              </button>
            ))}
          </div>

          {isConnecting && (
            <div className="mt-4 rounded-lg bg-primary-50 p-3 text-xs text-primary-700">
              连线模式：点击目标节点完成连接
              <button className="mt-1 block text-xs text-warm-500 underline" onClick={() => { setIsConnecting(false); setConnectFrom(null); }}>
                取消
              </button>
            </div>
          )}
        </div>

        {/* Canvas */}
        <div
          ref={canvasRef}
          className="relative flex-1 cursor-grab overflow-hidden bg-[#F5F4F0] active:cursor-grabbing"
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseLeave}
          onWheel={handleWheel}
        >
          <div
            className="canvas-grid absolute"
            style={{
              width: CANVAS_WIDTH,
              height: CANVAS_HEIGHT,
              transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
              transformOrigin: '0 0',
              backgroundImage: 'radial-gradient(#CDCBC4 1px, transparent 1px)',
              backgroundSize: '20px 20px',
            }}
          >
            {/* Edges */}
            <svg className="flow-edge pointer-events-none absolute inset-0" style={{ width: CANVAS_WIDTH, height: CANVAS_HEIGHT }}>
              {edges.map((edge) => {
                const fromNode = nodes.find((n) => n.id === edge.from);
                const toNode = nodes.find((n) => n.id === edge.to);
                if (!fromNode || !toNode) return null;
                return (
                  <g key={`${edge.from}-${edge.to}`}>
                    <line
                      x1={fromNode.x + 100}
                      y1={fromNode.y + 30}
                      x2={toNode.x + 100}
                      y2={toNode.y + 30}
                      stroke="#ADABA3"
                      strokeWidth={2}
                      markerEnd="url(#arrowhead)"
                    />
                    {edge.label && (
                      <text
                        x={(fromNode.x + toNode.x) / 2 + 100}
                        y={(fromNode.y + toNode.y) / 2 + 20}
                        className="fill-warm-500 text-[10px]"
                        textAnchor="middle"
                      >
                        {edge.label}
                      </text>
                    )}
                  </g>
                );
              })}
              <defs>
                <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
                  <polygon points="0 0, 10 3.5, 0 7" fill="#ADABA3" />
                </marker>
              </defs>
            </svg>

            {/* Nodes */}
            {nodes.map((node) => (
              <div
                key={node.id}
                className={`flow-node absolute cursor-pointer rounded-xl border-2 bg-warm-100 px-4 py-3 shadow-sm transition-all ${
                  selectedNodeId === node.id ? 'border-primary-500 ring-2 ring-primary-100' : 'border-warm-200'
                } ${dragNodeId === node.id ? 'cursor-grabbing' : 'cursor-grab'}`}
                style={{
                  left: node.x,
                  top: node.y,
                  width: 200,
                  minHeight: 60,
                }}
                onMouseDown={(e) => {
                  e.stopPropagation();
                  setDragNodeId(node.id);
                  handleNodeClick(node.id);
                }}
                onMouseUp={(e) => {
                  e.stopPropagation();
                  setDragNodeId(null);
                }}
                onMouseLeave={(e) => {
                  e.stopPropagation();
                }}
              >
                <div className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: getNodeColor(node.type) }} />
                  <span className="text-sm font-semibold text-warm-800">{node.name}</span>
                </div>
                <div className="mt-1 text-[10px] text-warm-400">{node.description || node.type}</div>
                {node.agent && <div className="mt-1 text-[10px] text-primary-500">{node.agent}</div>}

                {/* Connection handle */}
                <button
                  className="absolute -right-2 top-1/2 h-4 w-4 -translate-y-1/2 rounded-full border border-warm-300 bg-warm-100 text-[8px] text-warm-500 hover:bg-primary-500 hover:text-white"
                  onMouseDown={(e) => {
                    e.stopPropagation();
                    startConnect(node.id);
                  }}
                >
                  +
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Right Sidebar - Properties */}
        <div className="w-64 flex-none border-l border-warm-150 bg-warm-100 p-4">
          <div className="mb-3 text-xs font-medium text-warm-500">属性面板</div>
          {selectedNode ? (
            <div className="space-y-3">
              <div>
                <label className="text-[10px] text-warm-400">节点名称</label>
                <input
                  className="input-field mt-1 text-sm"
                  value={selectedNode.name}
                  onChange={(e) => updateNode(selectedNode.id, { name: e.target.value })}
                />
              </div>
              <div>
                <label className="text-[10px] text-warm-400">描述</label>
                <textarea
                  className="input-field mt-1 text-sm"
                  rows={2}
                  value={selectedNode.description}
                  onChange={(e) => updateNode(selectedNode.id, { description: e.target.value })}
                />
              </div>
              {selectedNode.type === 'agent' && (
                <>
                  <div>
                    <label className="text-[10px] text-warm-400">Agent</label>
                    <select
                      className="input-field mt-1 text-sm"
                      value={selectedNode.agent || ''}
                      onChange={(e) => updateNode(selectedNode.id, { agent: e.target.value })}
                    >
                      {agents.map((a) => (
                        <option key={a.agentId} value={a.agentId}>
                          {a.agentId}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-[10px] text-warm-400">层级</label>
                    <select
                      className="input-field mt-1 text-sm"
                      value={selectedNode.layer || 'domain'}
                      onChange={(e) => updateNode(selectedNode.id, { layer: e.target.value })}
                    >
                      <option value="meta">Layer1 (Meta)</option>
                      <option value="domain">Layer2 (Domain)</option>
                      <option value="micro">Layer3 (Micro)</option>
                    </select>
                  </div>
                </>
              )}
              <div>
                <label className="text-[10px] text-warm-400">依赖节点</label>
                <input
                  className="input-field mt-1 text-sm"
                  placeholder="逗号分隔的节点ID"
                  value={selectedNode.dependencies.join(',')}
                  onChange={(e) =>
                    updateNode(selectedNode.id, {
                      dependencies: e.target.value.split(',').map((x) => x.trim()).filter(Boolean),
                    })
                  }
                />
              </div>
              <button className="btn-ghost w-full text-sm text-red-500" onClick={() => removeNode(selectedNode.id)}>
                删除节点
              </button>
            </div>
          ) : (
            <div className="text-sm text-warm-400">点击节点查看属性</div>
          )}

          {/* Layer list */}
          <div className="mt-6">
            <div className="mb-2 text-xs font-medium text-warm-500">图层管理</div>
            <div className="space-y-1">
              {nodes.map((n) => (
                <div
                  key={n.id}
                  className={`cursor-pointer rounded px-2 py-1 text-xs ${selectedNodeId === n.id ? 'bg-primary-50 text-primary-700' : 'text-warm-600 hover:bg-warm-50'}`}
                  onClick={() => setSelectedNodeId(n.id)}
                >
                  {n.name}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
