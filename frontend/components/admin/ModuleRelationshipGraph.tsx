'use client';

import { useState, useEffect, useRef, useCallback, type JSX } from 'react';
import { Application, Graphics, Text, Container, TextStyle, type Text as PixiText } from 'pixi.js';

/**
 * Module Relationship Graph — PixiJS v8 WebGL implementation.
 *
 * Visualizes the 22+ admin modules and their relationships:
 * - Data flow (solid thick lines)
 * - Config dependency (dashed lines)
 * - Event subscription (dotted lines)
 *
 * Color-coded by real-time status: 🟢 normal, 🟡 degraded, 🔴 error
 *
 * Migrated from react-konva (Canvas 2D) to PixiJS v8 (WebGL 2.0)
 * as part of AgentHub V5.1 P0-2.
 */

// ── Module definitions ─────────────────────────────────────────────

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

const NODE_W = 100;
const NODE_H = 36;
const CANVAS_W = 820;
const CANVAS_H = 800;

// Fixed layout positions for 22 modules
const MODULE_LAYOUT: ModuleNode[] = [
  { id: '服务商', label: '服务商', group: '核心配置', status: 'normal', x: 100, y: 80 },
  { id: '工作流', label: '工作流', group: '核心配置', status: 'normal', x: 280, y: 80 },
  { id: '权限', label: '权限', group: '核心配置', status: 'normal', x: 460, y: 80 },
  { id: '通用', label: '通用设置', group: '核心配置', status: 'normal', x: 640, y: 80 },
  { id: '工作空间', label: '工作空间', group: '核心配置', status: 'normal', x: 100, y: 200 },
  { id: 'Agent 身份', label: 'Agent身份', group: '核心配置', status: 'normal', x: 280, y: 200 },
  { id: '多模态工作区', label: '多模态区', group: '核心配置', status: 'normal', x: 460, y: 200 },
  { id: 'IM 接入', label: 'IM接入', group: '能力扩展', status: 'normal', x: 100, y: 340 },
  { id: 'MCP', label: 'MCP网关', group: '能力扩展', status: 'normal', x: 280, y: 340 },
  { id: '技能', label: '技能', group: '能力扩展', status: 'normal', x: 460, y: 340 },
  { id: '插件', label: '插件', group: '能力扩展', status: 'normal', x: 640, y: 340 },
  { id: '知识库', label: '知识库', group: '能力扩展', status: 'normal', x: 100, y: 460 },
  { id: '模板市场', label: '模板市场', group: '能力扩展', status: 'normal', x: 280, y: 460 },
  { id: '工具市场', label: '工具市场', group: '能力扩展', status: 'normal', x: 460, y: 460 },
  { id: 'AgentNet', label: 'AgentNet', group: '能力扩展', status: 'normal', x: 640, y: 460 },
  { id: 'Computer Use', label: 'CompUse', group: '系统运维', status: 'normal', x: 100, y: 600 },
  { id: '审计日志', label: '审计日志', group: '系统运维', status: 'normal', x: 280, y: 600 },
  { id: '用户管理', label: '用户管理', group: '系统运维', status: 'normal', x: 460, y: 600 },
  { id: '记忆', label: '记忆', group: '系统运维', status: 'normal', x: 640, y: 600 },
  { id: '上下文引擎', label: '上下文引擎', group: '系统运维', status: 'normal', x: 190, y: 720 },
  { id: 'Docker 沙箱', label: 'Docker沙箱', group: '系统运维', status: 'normal', x: 370, y: 720 },
  { id: '集中日志', label: '集中日志', group: '系统运维', status: 'normal', x: 550, y: 720 },
];

const MODULE_EDGES: ModuleEdge[] = [
  { from: '服务商', to: 'Agent 身份', type: 'config', label: '配置模型' },
  { from: 'Agent 身份', to: '工作流', type: 'dataflow', label: '调用' },
  { from: '工作流', to: 'IM 接入', type: 'dataflow', label: '发布' },
  { from: '工作流', to: 'AgentNet', type: 'event', label: '触发任务' },
  { from: 'AgentNet', to: 'Docker 沙箱', type: 'dataflow', label: '沙箱执行' },
  { from: 'AgentNet', to: '知识库', type: 'dataflow', label: '检索' },
  { from: 'Docker 沙箱', to: '集中日志', type: 'dataflow', label: '日志' },
  { from: 'AgentNet', to: '上下文引擎', type: 'dataflow', label: '记忆读写' },
  { from: '上下文引擎', to: '工作空间', type: 'config', label: '上下文注入' },
  { from: '知识库', to: '上下文引擎', type: 'dataflow', label: 'RAG结果' },
  { from: 'MCP', to: '工具市场', type: 'config', label: '工具注册' },
  { from: '工具市场', to: 'AgentNet', type: 'dataflow', label: '工具调用' },
  { from: '插件', to: 'MCP', type: 'config', label: '扩展协议' },
  { from: '技能', to: 'AgentNet', type: 'config', label: '能力注入' },
  { from: '模板市场', to: 'Agent 身份', type: 'config', label: '一键创建' },
  { from: '模板市场', to: '工作流', type: 'config', label: '工作流模板' },
  { from: '权限', to: '服务商', type: 'config', label: '访问控制' },
  { from: '权限', to: '用户管理', type: 'config', label: '角色绑定' },
  { from: '用户管理', to: '审计日志', type: 'event', label: '操作审计' },
  { from: '审计日志', to: '集中日志', type: 'dataflow', label: '日志流' },
  { from: '通用', to: 'IM 接入', type: 'config', label: '全局参数' },
  { from: '通用', to: '工作空间', type: 'config', label: '默认策略' },
  { from: 'Computer Use', to: 'Docker 沙箱', type: 'dataflow', label: 'GPU分配' },
  { from: '记忆', to: '上下文引擎', type: 'dataflow', label: '记忆归档' },
  { from: '多模态工作区', to: '知识库', type: 'dataflow', label: '产物入库' },
];

// ── Color constants ───────────────────────────────────────────────

const GROUP_COLORS: Record<string, number> = {
  '核心配置': 0x6366f1,
  '能力扩展': 0x06b6d4,
  '系统运维': 0xf59e0b,
};

const STATUS_COLORS: Record<string, number> = {
  normal: 0x22c55e,
  degraded: 0xf59e0b,
  error: 0xef4444,
};

const EDGE_STYLE: Record<string, { dash: number[]; width: number; alpha: number }> = {
  dataflow: { dash: [], width: 2.5, alpha: 0.6 },
  config: { dash: [8, 4], width: 1.5, alpha: 0.4 },
  event: { dash: [2, 4], width: 1.5, alpha: 0.35 },
};

// ── Component ──────────────────────────────────────────────────────

export default function ModuleRelationshipGraph(): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const appRef = useRef<Application | null>(null);
  const nodesRef = useRef<ModuleNode[]>(MODULE_LAYOUT);

  // PixiJS graphics objects
  const gfxRef = useRef<{
    bgRects: Graphics;
    edgeLines: Graphics;
    edgeLabels: Container;
    nodeBodies: Graphics;
    nodeLabels: Container;
  } | null>(null);

  // Zoom/pan state
  const scaleRef = useRef(1);
  const offsetRef = useRef({ x: 0, y: 0 });
  const hoveredNodeRef = useRef<string | null>(null);

  // React state for footer stats and tooltip
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [modules, setModules] = useState<ModuleNode[]>(MODULE_LAYOUT);
  const [scale, setScale] = useState(1);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number; label: string; group: string; status: string } | null>(null);

  // Status simulation
  useEffect(() => {
    const interval = setInterval(() => {
      setModules((prev) => {
        const next: ModuleNode[] = prev.map((m) => {
          let status: ModuleNode['status'];
          if (Math.random() > 0.95) {
            const options: Array<ModuleNode['status']> = ['degraded', 'error'];
            status = options[Math.floor(Math.random() * 2)];
          } else if (m.status === 'error') {
            status = Math.random() > 0.7 ? 'degraded' : 'error';
          } else if (m.status === 'degraded') {
            status = Math.random() > 0.6 ? 'normal' : 'degraded';
          } else {
            status = 'normal';
          }
          return { ...m, status };
        });
        nodesRef.current = next;
        return next;
      });
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  // ── Rendering ──────────────────────────────────────────────────

  const renderFrame = useCallback(() => {
    const gfx = gfxRef.current;
    const nodes = nodesRef.current;
    if (!gfx) return;

    const s = scaleRef.current;
    const o = offsetRef.current;
    const hovered = hoveredNodeRef.current;

    // Helper: find node center position in screen space
    const getNodeCenter = (id: string): { x: number; y: number } | null => {
      const m = nodes.find((n) => n.id === id);
      if (!m) return null;
      return { x: (m.x + NODE_W / 2) * s + o.x, y: (m.y + NODE_H / 2) * s + o.y };
    };

    // ── Background ──
    gfx.bgRects.clear();
    const groupBgs = [
      { y: 60, h: 180, color: 0x6366f1 },
      { y: 320, h: 180, color: 0x06b6d4 },
      { y: 580, h: 190, color: 0xf59e0b },
    ];
    groupBgs.forEach(({ y: gy, h, color }) => {
      gfx.bgRects.rect(0, gy, CANVAS_W, h)
        .fill({ color, alpha: 0.03 });
      gfx.bgRects.rect(0, gy, CANVAS_W, h)
        .stroke({ color, alpha: 0.08, width: 1 });
    });

    // ── Edges ──
    gfx.edgeLines.clear();
    gfx.edgeLabels.removeChildren();

    MODULE_EDGES.forEach((edge) => {
      const from = getNodeCenter(edge.from);
      const to = getNodeCenter(edge.to);
      if (!from || !to) return;

      const style = EDGE_STYLE[edge.type];
      const highlighted = hovered === edge.from || hovered === edge.to;
      const alpha = highlighted ? 1 : style.alpha;
      const color = highlighted ? 0x6366f1 : 0x9ca3af;
      const strokeW = highlighted ? 3 : style.width;

      // Draw edge line (dashed lines drawn as segments for pixi v8)
      if (style.dash.length > 0) {
        const [dashLen, gapLen] = style.dash;
        const dx = to.x - from.x;
        const dy = to.y - from.y;
        const length = Math.sqrt(dx * dx + dy * dy);
        const ux = dx / length;
        const uy = dy / length;
        let drawn = 0;
        let odd = false;
        while (drawn < length) {
          const segLen = odd ? gapLen : dashLen;
          const startX = from.x + ux * drawn;
          const startY = from.y + uy * drawn;
          const endDrawn = Math.min(drawn + segLen, length);
          if (!odd) {
            gfx.edgeLines.moveTo(startX, startY)
              .lineTo(from.x + ux * endDrawn, from.y + uy * endDrawn)
              .stroke({ color, width: strokeW, alpha });
          }
          drawn = endDrawn;
          odd = !odd;
        }
      } else {
        gfx.edgeLines.moveTo(from.x, from.y)
          .lineTo(to.x, to.y)
          .stroke({ color, width: strokeW, alpha });
      }

      // Edge label
      if (edge.label) {
        const midX = (from.x + to.x) / 2;
        const midY = (from.y + to.y) / 2;
        const labelText = new Text({
          text: edge.label,
          style: new TextStyle({
            fontSize: 9,
            fill: highlighted ? 0x6366f1 : 0x9ca3af,
            fontFamily: 'system-ui',
          }),
        });
        labelText.alpha = highlighted ? 1 : 0.7;
        labelText.x = midX - 15;
        labelText.y = midY - 10;
        gfx.edgeLabels.addChild(labelText);
      }
    });

    // ── Nodes ──
    gfx.nodeBodies.clear();
    gfx.nodeLabels.removeChildren();

    nodes.forEach((mod) => {
      const nx = mod.x * s + o.x;
      const ny = mod.y * s + o.y;
      const isHovered = hovered === mod.id;
      const groupColor = GROUP_COLORS[mod.group] || 0x6b7280;
      const statusColor = STATUS_COLORS[mod.status];

      // Shadow (drawn behind node)
      gfx.nodeBodies.rect(nx, ny + (isHovered ? 4 : 2), NODE_W * s, NODE_H * s)
        .fill({ color: isHovered ? groupColor : 0x000000, alpha: isHovered ? 0.25 : 0.06 });

      // Node body (white background)
      gfx.nodeBodies.rect(nx, ny, NODE_W * s, NODE_H * s)
        .fill({ color: 0xffffff, alpha: 1 });

      // Border
      const borderColor = isHovered ? groupColor : 0x000000;
      const borderAlpha = isHovered ? 1 : 0.08;
      const borderWidth = isHovered ? 2.5 : 1;
      gfx.nodeBodies.rect(nx, ny, NODE_W * s, NODE_H * s)
        .stroke({ color: borderColor, width: borderWidth, alpha: borderAlpha });

      // Left color bar
      gfx.nodeBodies.rect(nx, ny, 3 * s, NODE_H * s)
        .fill({ color: groupColor, alpha: 1 });

      // Status dot
      gfx.nodeBodies.circle(nx - NODE_W / 2 * s + 10 * s, ny + NODE_H / 2 * s, 4 * s)
        .fill({ color: statusColor, alpha: 0.9 });

      // Label text
      const labelStr = isHovered ? mod.label : mod.label;
      const labelTxt = new Text({
        text: labelStr,
        style: new TextStyle({
          fontSize: 11 * s,
          fontFamily: 'system-ui',
          fontWeight: isHovered ? '700' : '500',
          fill: isHovered ? 0x1f2937 : 0x374151,
        }),
      });
      labelTxt.x = nx + 16 * s;
      labelTxt.y = ny + NODE_H / 2 * s - 7 * s;
      gfx.nodeLabels.addChild(labelTxt);

      // Group badge
      const groupTxt = new Text({
        text: mod.group,
        style: new TextStyle({
          fontSize: 8 * s,
          fontFamily: 'system-ui',
          fill: groupColor,
        }),
      });
      groupTxt.alpha = 0.7;
      groupTxt.x = nx + 16 * s;
      groupTxt.y = ny + NODE_H / 2 * s + 8 * s;
      gfx.nodeLabels.addChild(groupTxt);
    });
  }, []);

  // ── PixiJS Initialization ──────────────────────────────────────

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let destroyed = false;

    const initPixi = async () => {
      const app = new Application();
      await app.init({
        width: CANVAS_W,
        height: CANVAS_H,
        backgroundAlpha: 0,
        antialias: true,
        resolution: Math.min(window.devicePixelRatio || 1, 2),
        autoDensity: true,
      });

      if (destroyed) {
        app.destroy(true);
        return;
      }

      appRef.current = app;

      // Create layers
      const bgLayer = new Container();
      const edgeLayer = new Container();
      const nodeLayer = new Container();

      const bgRects = new Graphics();
      const edgeLines = new Graphics();
      const edgeLabels = new Container();
      const nodeBodies = new Graphics();
      const nodeLabels = new Container();

      bgLayer.addChild(bgRects);
      edgeLayer.addChild(edgeLines);
      edgeLayer.addChild(edgeLabels);
      nodeLayer.addChild(nodeBodies);
      nodeLayer.addChild(nodeLabels);

      app.stage.addChild(bgLayer);
      app.stage.addChild(edgeLayer);
      app.stage.addChild(nodeLayer);

      gfxRef.current = { bgRects, edgeLines, edgeLabels, nodeBodies, nodeLabels };

      // Hover detection
      app.stage.eventMode = 'static';
      app.stage.hitArea = { contains: () => true };

      app.stage.on('pointermove', (e) => {
        const pos = e.global;
        const s = scaleRef.current;
        const o = offsetRef.current;
        const nodes = nodesRef.current;

        // Hit test nodes (reverse order for top-most first)
        let found: ModuleNode | null = null;
        for (let i = nodes.length - 1; i >= 0; i--) {
          const n = nodes[i];
          const nx = n.x * s + o.x;
          const ny = n.y * s + o.y;
          const nw = NODE_W * s;
          const nh = NODE_H * s;
          if (pos.x >= nx && pos.x <= nx + nw && pos.y >= ny && pos.y <= ny + nh) {
            found = n;
            break;
          }
        }

        const prev = hoveredNodeRef.current;
        hoveredNodeRef.current = found ? found.id : null;

        if (hoveredNodeRef.current !== prev) {
          setHoveredNode(hoveredNodeRef.current);
          if (found) {
            setTooltipPos({ x: pos.x, y: pos.y, label: found.label, group: found.group, status: found.status });
          } else {
            setTooltipPos(null);
          }
        }

        if (found) {
          setTooltipPos({ x: pos.x, y: pos.y, label: found.label, group: found.group, status: found.status });
        }
      });

      app.stage.on('pointerleave', () => {
        hoveredNodeRef.current = null;
        setHoveredNode(null);
        setTooltipPos(null);
      });

      // Wheel zoom
      container.addEventListener('wheel', (e: WheelEvent) => {
        e.preventDefault();

        const rect = container.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;

        const oldScale = scaleRef.current;
        const scaleBy = 1.08;
        const newScale = e.deltaY < 0 ? oldScale * scaleBy : oldScale / scaleBy;
        const clamped = Math.min(2, Math.max(0.4, newScale));

        // Zoom toward mouse position
        const worldX = (mx - offsetRef.current.x) / oldScale;
        const worldY = (my - offsetRef.current.y) / oldScale;
        offsetRef.current.x = mx - worldX * clamped;
        offsetRef.current.y = my - worldY * clamped;
        scaleRef.current = clamped;

        setScale(clamped);
      }, { passive: false });

      // Pan via drag
      let dragging = false;
      let dragStart = { x: 0, y: 0 };
      let offsetStart = { x: 0, y: 0 };

      container.addEventListener('mousedown', (e: MouseEvent) => {
        // Only pan on background click (not on nodes)
        const s = scaleRef.current;
        const o = offsetRef.current;
        const nodes = nodesRef.current;
        const rect = container.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;

        let hitNode = false;
        for (const n of nodes) {
          const nx = n.x * s + o.x;
          const ny = n.y * s + o.y;
          if (mx >= nx && mx <= nx + NODE_W * s && my >= ny && my <= ny + NODE_H * s) {
            hitNode = true;
            break;
          }
        }

        if (!hitNode) {
          dragging = true;
          dragStart = { x: e.clientX, y: e.clientY };
          offsetStart = { ...offsetRef.current };
          container.style.cursor = 'grabbing';
        }
      });

      window.addEventListener('mousemove', (e: MouseEvent) => {
        if (!dragging) return;
        offsetRef.current.x = offsetStart.x + (e.clientX - dragStart.x);
        offsetRef.current.y = offsetStart.y + (e.clientY - dragStart.y);
      });

      window.addEventListener('mouseup', () => {
        if (dragging) {
          dragging = false;
          if (container) container.style.cursor = 'default';
        }
      });

      // Append canvas
      container.appendChild(app.canvas);
      app.canvas.style.width = '100%';
      app.canvas.style.height = 'auto';
      app.canvas.style.display = 'block';
      app.canvas.style.borderRadius = '12px';

      // Render loop
      app.ticker.add(() => {
        renderFrame();
      });
    };

    initPixi();

    return () => {
      destroyed = true;
      if (appRef.current) {
        appRef.current.destroy(true);
        appRef.current = null;
      }
      gfxRef.current = null;
    };
  }, [renderFrame]);

  // Sync hover/scale to render
  useEffect(() => {
    hoveredNodeRef.current = hoveredNode;
  }, [hoveredNode]);

  // ── Status color for tooltip ────────────────────────────────────

  const statusLabel: Record<string, string> = {
    normal: '正常',
    degraded: '降级中',
    error: '异常',
  };
  const statusHex: Record<string, string> = {
    normal: '#22c55e',
    degraded: '#f59e0b',
    error: '#ef4444',
  };

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-base font-semibold text-warm-800">🌐 AgentHub 模块关系图</h3>
        <div className="flex items-center gap-3 text-xs text-warm-500 flex-wrap">
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

      {/* Canvas container */}
      <div
        ref={containerRef}
        style={{
          width: '100%',
          maxWidth: CANVAS_W,
          height: CANVAS_H,
          background: '#fafbfc',
          borderRadius: 12,
          overflow: 'hidden',
          border: '1px solid #e5e7eb',
          position: 'relative',
          cursor: 'default',
          userSelect: 'none',
          margin: '0 auto',
        }}
      >
        {/* Hover tooltip — absolutely positioned over canvas */}
        {tooltipPos && (
          <div
            style={{
              position: 'absolute',
              left: Math.min(tooltipPos.x + 14, CANVAS_W - 140),
              top: Math.max(tooltipPos.y - 40, 4),
              zIndex: 100,
              background: 'rgba(17,24,39,0.94)',
              backdropFilter: 'blur(8px)',
              borderRadius: 8,
              padding: '6px 10px',
              color: '#f9fafb',
              fontSize: 11,
              fontFamily: 'system-ui',
              lineHeight: 1.5,
              pointerEvents: 'none',
              whiteSpace: 'nowrap',
              boxShadow: '0 4px 16px rgba(0,0,0,0.25)',
              border: '1px solid rgba(255,255,255,0.12)',
            }}
          >
            <div style={{ fontWeight: 700, marginBottom: 2 }}>{tooltipPos.label}</div>
            <div style={{ color: '#d1d5db', fontSize: 10 }}>
              {tooltipPos.group}
              <span style={{
                display: 'inline-block',
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: statusHex[tooltipPos.status] || '#9ca3af',
                marginLeft: 6,
                marginRight: 3,
                verticalAlign: 'middle',
              }} />
              {statusLabel[tooltipPos.status] || tooltipPos.status}
            </div>
          </div>
        )}
      </div>

      {/* Stats footer */}
      <div className="flex items-center justify-between mt-3 text-xs text-warm-500">
        <span>
          {modules.length} 模块 · {MODULE_EDGES.length} 条关系
          <span className="ml-3" style={{
            display: 'inline-block',
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: scale >= 1.5 ? '#22c55e' : scale >= 1 ? '#3b82f6' : '#f59e0b',
          }} />
          {' '}缩放 {Math.round(scale * 100)}%
        </span>
        <span>🖱️ 滚轮缩放 · 拖拽平移 · 悬停高亮</span>
      </div>
    </div>
  );
}
