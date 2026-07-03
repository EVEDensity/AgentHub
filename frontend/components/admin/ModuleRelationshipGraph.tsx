'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { Stage, Layer, Circle, Line, Text, Group, Rect } from 'react-konva';
import type { KonvaEventObject } from 'konva/lib/Node';

/**
 * Module Relationship Graph
 *
 * Visualizes the 22+ admin modules and their relationships:
 * - Data flow (solid thick lines)
 * - Config dependency (dashed lines)
 * - Event subscription (dotted lines)
 *
 * Color-coded by real-time status: 🟢 normal, 🟡 degraded, 🔴 error
 *
 * Part of AgentHub V5.1 P0 Module Relationship Visualization
 */

// ── Module definitions matching adminStore MENU_GROUPS ──────────────

interface ModuleNode {
  id: string;
  label: string;
  group: string;
  status: 'normal' | 'degraded' | 'error';
  x: number;
  y: number;
}

interface ModuleEdge {
  from: string;
  to: string;
  type: 'dataflow' | 'config' | 'event';
  label?: string;
}

// Fixed layout positions for 22 modules (hand-crafted to be readable)
const MODULE_LAYOUT: { id: string; label: string; group: string; status: 'normal' | 'degraded' | 'error'; x: number; y: number }[] = [
  // 核心配置 group (top-left)
  { id: '服务商', label: '服务商', group: '核心配置', status: 'normal', x: 100, y: 80 },
  { id: '工作流', label: '工作流', group: '核心配置', status: 'normal', x: 280, y: 80 },
  { id: '权限', label: '权限', group: '核心配置', status: 'normal', x: 460, y: 80 },
  { id: '通用', label: '通用设置', group: '核心配置', status: 'normal', x: 640, y: 80 },
  { id: '工作空间', label: '工作空间', group: '核心配置', status: 'normal', x: 100, y: 200 },
  { id: 'Agent 身份', label: 'Agent身份', group: '核心配置', status: 'normal', x: 280, y: 200 },
  { id: '多模态工作区', label: '多模态区', group: '核心配置', status: 'normal', x: 460, y: 200 },

  // 能力扩展 group (middle)
  { id: 'IM 接入', label: 'IM接入', group: '能力扩展', status: 'normal', x: 100, y: 340 },
  { id: 'MCP', label: 'MCP网关', group: '能力扩展', status: 'normal', x: 280, y: 340 },
  { id: '技能', label: '技能', group: '能力扩展', status: 'normal', x: 460, y: 340 },
  { id: '插件', label: '插件', group: '能力扩展', status: 'normal', x: 640, y: 340 },
  { id: '知识库', label: '知识库', group: '能力扩展', status: 'normal', x: 100, y: 460 },
  { id: '模板市场', label: '模板市场', group: '能力扩展', status: 'normal', x: 280, y: 460 },
  { id: '工具市场', label: '工具市场', group: '能力扩展', status: 'normal', x: 460, y: 460 },
  { id: 'AgentNet', label: 'AgentNet', group: '能力扩展', status: 'normal', x: 640, y: 460 },

  // 系统运维 group (right/bottom)
  { id: 'Computer Use', label: 'CompUse', group: '系统运维', status: 'normal', x: 100, y: 600 },
  { id: '审计日志', label: '审计日志', group: '系统运维', status: 'normal', x: 280, y: 600 },
  { id: '用户管理', label: '用户管理', group: '系统运维', status: 'normal', x: 460, y: 600 },
  { id: '记忆', label: '记忆', group: '系统运维', status: 'normal', x: 640, y: 600 },
  { id: '上下文引擎', label: '上下文引擎', group: '系统运维', status: 'normal', x: 190, y: 720 },
  { id: 'Docker 沙箱', label: 'Docker沙箱', group: '系统运维', status: 'normal', x: 370, y: 720 },
  { id: '集中日志', label: '集中日志', group: '系统运维', status: 'normal', x: 550, y: 720 },
];

// Module relationships
const MODULE_EDGES: ModuleEdge[] = [
  // Service provider → Agent identity (配置模型)
  { from: '服务商', to: 'Agent 身份', type: 'config', label: '配置模型' },
  { from: 'Agent 身份', to: '工作流', type: 'dataflow', label: '调用' },
  { from: '工作流', to: 'IM 接入', type: 'dataflow', label: '发布' },
  { from: '工作流', to: 'AgentNet', type: 'event', label: '触发任务' },

  // Workflow → Sandbox
  { from: 'AgentNet', to: 'Docker 沙箱', type: 'dataflow', label: '沙箱执行' },
  { from: 'AgentNet', to: '知识库', type: 'dataflow', label: '检索' },

  // Memory chain
  { from: 'Docker 沙箱', to: '集中日志', type: 'dataflow', label: '日志' },
  { from: 'AgentNet', to: '上下文引擎', type: 'dataflow', label: '记忆读写' },
  { from: '上下文引擎', to: '工作空间', type: 'config', label: '上下文注入' },
  { from: '知识库', to: '上下文引擎', type: 'dataflow', label: 'RAG结果' },

  // Tool/Plugin chain
  { from: 'MCP', to: '工具市场', type: 'config', label: '工具注册' },
  { from: '工具市场', to: 'AgentNet', type: 'dataflow', label: '工具调用' },
  { from: '插件', to: 'MCP', type: 'config', label: '扩展协议' },
  { from: '技能', to: 'AgentNet', type: 'config', label: '能力注入' },

  // Template → Agent
  { from: '模板市场', to: 'Agent 身份', type: 'config', label: '一键创建' },
  { from: '模板市场', to: '工作流', type: 'config', label: '工作流模板' },

  // IAM / Audit
  { from: '权限', to: '服务商', type: 'config', label: '访问控制' },
  { from: '权限', to: '用户管理', type: 'config', label: '角色绑定' },
  { from: '用户管理', to: '审计日志', type: 'event', label: '操作审计' },
  { from: '审计日志', to: '集中日志', type: 'dataflow', label: '日志流' },

  // General settings
  { from: '通用', to: 'IM 接入', type: 'config', label: '全局参数' },
  { from: '通用', to: '工作空间', type: 'config', label: '默认策略' },

  // GPU/CompUse
  { from: 'Computer Use', to: 'Docker 沙箱', type: 'dataflow', label: 'GPU分配' },
  { from: '记忆', to: '上下文引擎', type: 'dataflow', label: '记忆归档' },
  { from: '多模态工作区', to: '知识库', type: 'dataflow', label: '产物入库' },
];

// Layout constants
const NODE_W = 100;
const NODE_H = 36;
const CANVAS_W = 820;
const CANVAS_H = 800;

const GROUP_COLORS: Record<string, string> = {
  '核心配置': '#6366f1',
  '能力扩展': '#06b6d4',
  '系统运维': '#f59e0b',
};

const STATUS_COLORS: Record<string, string> = {
  normal: '#22c55e',
  degraded: '#f59e0b',
  error: '#ef4444',
};

const EDGE_STYLE: Record<string, { dash: number[]; width: number; alpha: number }> = {
  dataflow: { dash: [], width: 2.5, alpha: 0.6 },
  config: { dash: [8, 4], width: 1.5, alpha: 0.4 },
  event: { dash: [2, 4], width: 1.5, alpha: 0.35 },
};

export default function ModuleRelationshipGraph({
}: {
}): JSX.Element {
  const [modules, setModules] = useState<ModuleNode[]>(MODULE_LAYOUT);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const stageRef = useRef<any>(null);

  // Simulate status updates
  useEffect(() => {
    const interval = setInterval(() => {
      setModules((prev) =>
        prev.map((m) => ({
          ...m,
          status: Math.random() > 0.95
            ? (['degraded', 'error'][Math.floor(Math.random() * 2)] as 'degraded' | 'error')
            : m.status === 'error'
              ? (Math.random() > 0.7 ? 'degraded' : 'error')
              : m.status === 'degraded'
                ? (Math.random() > 0.6 ? 'normal' : 'degraded')
                : 'normal',
        })),
      );
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  const handleWheel = useCallback((e: KonvaEventObject<WheelEvent>) => {
    e.evt.preventDefault();
    const scaleBy = 1.08;
    const oldScale = scale;
    const newScale = e.evt.deltaY < 0 ? oldScale * scaleBy : oldScale / scaleBy;
    const clamped = Math.min(2, Math.max(0.4, newScale));
    setScale(clamped);
  }, [scale]);

  const handleDragEnd = useCallback((e: KonvaEventObject<DragEvent>) => {
    setOffset({ x: e.target.x(), y: e.target.y() });
  }, []);

  // Find node positions for edge drawing
  const getNodePos = (id: string) => {
    const m = modules.find((n) => n.id === id);
    return m ? { x: m.x + NODE_W / 2, y: m.y + NODE_H / 2 } : null;
  };

  // Highlight edges connected to hovered node
  const isEdgeHighlighted = (edge: ModuleEdge) => {
    if (!hoveredNode) return false;
    return edge.from === hoveredNode || edge.to === hoveredNode;
  };

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-base font-semibold text-warm-800">🌐 AgentHub 模块关系图</h3>
        <div className="flex items-center gap-3 text-xs text-warm-500">
          <span>
            <span className="inline-block w-3 h-[2px] bg-warm-400 mr-1 align-middle" /> 数据流
          </span>
          <span>
            <span className="inline-block w-3 h-[2px] border-t border-dashed border-warm-400 mr-1 align-middle" /> 配置依赖
          </span>
          <span>
            <span className="inline-block w-3 h-[2px] border-t border-dotted border-warm-400 mr-1 align-middle" /> 事件订阅
          </span>
          <span className="ml-2 flex items-center gap-1">
            <span className="inline-block w-2 h-2 rounded-full bg-green-500" /> 正常
            <span className="inline-block w-2 h-2 rounded-full bg-amber-500" /> 降级
            <span className="inline-block w-2 h-2 rounded-full bg-red-500" /> 异常
          </span>
        </div>
      </div>

      <div style={{ background: '#fafbfc', borderRadius: 12, overflow: 'hidden', border: '1px solid #e5e7eb' }}>
        <Stage
          width={CANVAS_W}
          height={CANVAS_H}
          scaleX={scale}
          scaleY={scale}
          x={offset.x}
          y={offset.y}
          draggable
          onDragEnd={handleDragEnd}
          onWheel={handleWheel}
          ref={stageRef}
        >
          {/* Background layer */}
          <Layer>
            {[0, 1, 2].map((i) => (
              <Rect
                key={i}
                x={0}
                y={i * 280}
                width={CANVAS_W}
                height={260}
                fill={i === 0 ? 'rgba(99,102,241,0.03)' : i === 1 ? 'rgba(6,182,212,0.03)' : 'rgba(245,158,11,0.03)'}
                stroke={i === 0 ? 'rgba(99,102,241,0.1)' : i === 1 ? 'rgba(6,182,212,0.1)' : 'rgba(245,158,11,0.1)'}
                strokeWidth={1}
                cornerRadius={8}
              />
            ))}
          </Layer>

          {/* Edges layer */}
          <Layer>
            {MODULE_EDGES.map((edge, i) => {
              const from = getNodePos(edge.from);
              const to = getNodePos(edge.to);
              if (!from || !to) return null;

              const style = EDGE_STYLE[edge.type];
              const highlighted = isEdgeHighlighted(edge);
              const alpha = highlighted ? 1 : style.alpha;

              return (
                <Group key={`edge-${i}`}>
                  <Line
                    points={[from.x, from.y, to.x, to.y]}
                    stroke={highlighted ? '#6366f1' : '#9ca3af'}
                    strokeWidth={highlighted ? 3 : style.width}
                    dash={style.dash}
                    opacity={alpha}
                    hitStrokeWidth={12}
                  />
                  {edge.label && (
                    <Text
                      x={(from.x + to.x) / 2 - 20}
                      y={(from.y + to.y) / 2 - 10}
                      text={edge.label}
                      fontSize={9}
                      fill={highlighted ? '#6366f1' : '#9ca3af'}
                      fontFamily="system-ui"
                      opacity={highlighted ? 1 : 0.7}
                    />
                  )}
                </Group>
              );
            })}
          </Layer>

          {/* Nodes layer */}
          <Layer>
            {modules.map((mod) => {
              const isHovered = hoveredNode === mod.id;
              const groupColor = GROUP_COLORS[mod.group] || '#6b7280';
              const statusColor = STATUS_COLORS[mod.status];

              return (
                <Group
                  key={mod.id}
                  x={mod.x}
                  y={mod.y}
                  onMouseEnter={() => setHoveredNode(mod.id)}
                  onMouseLeave={() => setHoveredNode(null)}
                >
                  {/* Status indicator dot */}
                  <Circle
                    x={-NODE_W / 2 + 10}
                    y={NODE_H / 2}
                    radius={4}
                    fill={statusColor}
                    opacity={0.9}
                  />

                  {/* Node background */}
                  <Rect
                    width={NODE_W}
                    height={NODE_H}
                    fill="#fff"
                    stroke={isHovered ? groupColor : 'rgba(0,0,0,0.08)'}
                    strokeWidth={isHovered ? 2.5 : 1}
                    cornerRadius={8}
                    shadowColor={isHovered ? groupColor : 'rgba(0,0,0,0.06)'}
                    shadowBlur={isHovered ? 12 : 4}
                    shadowOffset={{ x: 0, y: isHovered ? 4 : 2 }}
                    shadowOpacity={isHovered ? 0.25 : 0.5}
                  />

                  {/* Group color bar (left edge) */}
                  <Rect
                    x={0}
                    y={0}
                    width={3}
                    height={NODE_H}
                    fill={groupColor}
                    cornerRadius={[8, 0, 0, 8]}
                  />

                  {/* Module label */}
                  <Text
                    x={16}
                    y={NODE_H / 2 - 7}
                    text={mod.label}
                    fontSize={11}
                    fontFamily="system-ui"
                    fontWeight={isHovered ? 700 : 500}
                    fill={isHovered ? '#1f2937' : '#374151'}
                  />

                  {/* Group badge */}
                  <Text
                    x={16}
                    y={NODE_H / 2 + 8}
                    text={mod.group}
                    fontSize={8}
                    fontFamily="system-ui"
                    fill={groupColor}
                    opacity={0.7}
                  />
                </Group>
              );
            })}
          </Layer>
        </Stage>
      </div>

      {/* Stats footer */}
      <div className="flex items-center justify-between mt-3 text-xs text-warm-500">
        <span>
          {modules.length} 模块 · {MODULE_EDGES.length} 条关系
        </span>
        <span>🖱️ 滚轮缩放 · 拖拽平移 · 悬停高亮</span>
      </div>
    </div>
  );
}
