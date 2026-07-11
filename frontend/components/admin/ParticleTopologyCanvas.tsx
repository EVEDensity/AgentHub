'use client';

import { useEffect, useRef, useCallback, useState } from 'react';
import { Application, Graphics, Text, Container, TextStyle } from 'pixi.js';
import type { TopologyNode, TopologyEdge } from '../../types';
import {
  FPSTracker,
  getQualityTier,
  type QualityTier,
} from '../../lib/performance/adaptiveQuality';
import PerformanceMonitor, { usePerformanceMonitorToggle } from './PerformanceMonitor';

/**
 * Particle-Enhanced Agent Topology Canvas (PixiJS WebGL)
 *
 * Renders a force-directed agent topology graph with:
 * - Web Worker physics simulation (off-main-thread)
 * - Glow particles orbiting agent nodes
 * - Data-flow particles traveling along edges
 * - Emergence burst effects on shared-memory events
 * - Adaptive quality based on measured FPS
 *
 * Part of AgentHub V5.1 P0 — PixiJS v8 WebGL Particle Topology
 */

// ── Constants ──────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  idle: '#22c55e',
  busy: '#f59e0b',
  overloaded: '#ef4444',
  offline: '#9ca3af',
  pending: '#6b7280',
  assigned: '#3b82f6',
  running: '#f59e0b',
  completed: '#22c55e',
  failed: '#ef4444',
  destroyed: '#9ca3af',
  created: '#8b5cf6',
  ready: '#06b6d4',
  cancelled: '#9ca3af',
};

const TYPE_ICON: Record<string, string> = {
  agent: '[bot]',
  task: '[clipboard]',
  spawn: '[dna]',
  memory: '[eye]',
};

function hexToNumber(hex: string): number {
  return parseInt(hex.replace('#', ''), 16);
}

// ── Sim Node with rendering state ─────────────────────────────────

interface RenderNode {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  type: string;
  status: string;
  label: string;
  radius: number;
  glowPhase: number;
  glowParticles: { angle: number; speed: number; radius: number; size: number; alpha: number }[];
}

interface FlowParticle {
  id: string;
  edgeIndex: number;
  from: RenderNode;
  to: RenderNode;
  progress: number;
  speed: number;
  size: number;
  color: string;
}

interface EmergenceBurst {
  id: string;
  x: number;
  y: number;
  particles: { angle: number; speed: number; life: number; maxLife: number; size: number; color: string }[];
  age: number;
  maxAge: number;
}

// ── Props ──────────────────────────────────────────────────────────

interface ParticleTopologyCanvasProps {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  width?: number;
  height?: number;
}

// ── PixiJS-based Component ────────────────────────────────────────

export default function ParticleTopologyCanvas({
  nodes,
  edges,
  width = 780,
  height = 560,
}: ParticleTopologyCanvasProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const appRef = useRef<Application | null>(null);
  const layersRef = useRef<{
    bgGlow: Container;
    edges: Container;
    nodes: Container;
    effects: Container;
  } | null>(null);
  const graphicsRef = useRef<{
    bgGlowGfx: Graphics;
    edgeLines: Graphics;
    flowParticles: Graphics;
    glowRings: Graphics;
    glowOrbits: Graphics;
    nodeBodies: Graphics;
    emergenceGfx: Graphics;
  } | null>(null);
  const textsRef = useRef<{
    nodeLabels: Map<string, Text>;
    edgeLabels: Map<string, Text>;
    nodeIcons: Map<string, Text>;
    hoverTooltip: Container | null;
  }>({ nodeLabels: new Map(), edgeLabels: new Map(), nodeIcons: new Map(), hoverTooltip: null });

  const [renderNodes, setRenderNodes] = useState<RenderNode[]>([]);
  const [flowParticles, setFlowParticles] = useState<FlowParticle[]>([]);
  const [emergenceBursts, setEmergenceBursts] = useState<EmergenceBurst[]>([]);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [qualityTier, setQualityTier] = useState<QualityTier>(getQualityTier(60));
  const [particleCount, setParticleCount] = useState(0);

  const fpsTrackerRef = useRef<FPSTracker>(new FPSTracker(60));
  const workerRef = useRef<Worker | null>(null);
  const animRef = useRef<number>(0);
  const nodesRef = useRef<RenderNode[]>([]);
  const edgesRef = useRef<TopologyEdge[]>(edges);
  const tickCountRef = useRef(0);
  const flowParticlesRef = useRef<FlowParticle[]>([]);
  const emergenceBurstsRef = useRef<EmergenceBurst[]>([]);
  const qualityTierRef = useRef<QualityTier>(qualityTier);
  const hoveredNodeRef = useRef<string | null>(null);

  const [perfVisible] = usePerformanceMonitorToggle();

  // Keep refs in sync
  useEffect(() => { qualityTierRef.current = qualityTier; }, [qualityTier]);
  useEffect(() => { flowParticlesRef.current = flowParticles; }, [flowParticles]);
  useEffect(() => { emergenceBurstsRef.current = emergenceBursts; }, [emergenceBursts]);
  useEffect(() => { hoveredNodeRef.current = hoveredNodeId; }, [hoveredNodeId]);

  // ── Initialize Web Worker ────────────────────────────────────────

  useEffect(() => {
    try {
      const worker = new Worker(
        new URL('../../workers/physics.worker.ts', import.meta.url),
      );
      workerRef.current = worker;

      worker.onmessage = (e: MessageEvent) => {
        if (e.data.type === 'ready') return;

        const { positions } = e.data as { positions?: Array<{ id: string; x: number; y: number; vx: number; vy: number }> };
        if (positions && positions.length > 0) {
          const posMap = new Map(positions.map((p) => [p.id, p] as const));
          nodesRef.current = nodesRef.current.map((n) => {
            const pos = posMap.get(n.id);
            if (pos) {
              return { ...n, x: pos.x, y: pos.y, vx: pos.vx, vy: pos.vy };
            }
            return n;
          });
          setRenderNodes([...nodesRef.current]);
        }
      };

      return () => {
        worker.terminate();
        workerRef.current = null;
      };
    } catch {
      console.warn('Web Worker not available, falling back to main-thread physics');
    }
  }, []);

  // ── Initialize PixiJS Application ────────────────────────────────

  useEffect(() => {
    const app = new Application();
    appRef.current = app;

    const initPixi = async () => {
      await app.init({
        width,
        height,
        backgroundAlpha: 0,
        antialias: true,
        resolution: Math.min(window.devicePixelRatio || 1, 2),
        autoDensity: true,
        eventMode: 'static',
        eventFeatures: {
          move: true,
          click: true,
          wheel: false,
        },
      });

      if (!containerRef.current) return;
      containerRef.current.appendChild(app.canvas);

      // ── Create layer structure ──────────────────────────────────
      const bgGlow = new Container();
      const edgeContainer = new Container();
      const nodeContainer = new Container();
      const effectContainer = new Container();

      app.stage.addChild(bgGlow, edgeContainer, nodeContainer, effectContainer);
      layersRef.current = {
        bgGlow,
        edges: edgeContainer,
        nodes: nodeContainer,
        effects: effectContainer,
      };

      // ── Create Graphics objects per layer ────────────────────────
      const bgGlowGfx = new Graphics();
      const edgeLines = new Graphics();
      const flowParticles = new Graphics();
      const glowRings = new Graphics();
      const glowOrbits = new Graphics();
      const nodeBodies = new Graphics();
      const emergenceGfx = new Graphics();

      bgGlow.addChild(bgGlowGfx);
      edgeContainer.addChild(edgeLines);
      edgeContainer.addChild(flowParticles);
      nodeContainer.addChild(glowRings);
      nodeContainer.addChild(glowOrbits);
      nodeContainer.addChild(nodeBodies);
      effectContainer.addChild(emergenceGfx);

      graphicsRef.current = {
        bgGlowGfx,
        edgeLines,
        flowParticles,
        glowRings,
        glowOrbits,
        nodeBodies,
        emergenceGfx,
      };

      // ── Hover detection ─────────────────────────────────────────
      app.stage.hitArea = { contains: () => true };
      app.stage.on('pointermove', (e) => {
        const pos = e.global;
        let found: string | null = null;
        for (const n of nodesRef.current) {
          const dx = pos.x - n.x;
          const dy = pos.y - n.y;
          if (Math.sqrt(dx * dx + dy * dy) <= n.radius + 4) {
            found = n.id;
            break;
          }
        }
        if (found !== hoveredNodeRef.current) {
          setHoveredNodeId(found);
        }
      });
      app.stage.on('pointerleave', () => {
        setHoveredNodeId(null);
      });
    };

    initPixi();

    return () => {
      // Clean up text objects
      for (const t of textsRef.current.nodeLabels.values()) t.destroy();
      for (const t of textsRef.current.edgeLabels.values()) t.destroy();
      for (const t of textsRef.current.nodeIcons.values()) t.destroy();
      textsRef.current.nodeLabels.clear();
      textsRef.current.edgeLabels.clear();
      textsRef.current.nodeIcons.clear();

      app.destroy(true, { children: true });
      appRef.current = null;
      layersRef.current = null;
      graphicsRef.current = null;
    };
  }, [width, height]);

  // ── Initialize / update nodes and edges ──────────────────────────

  useEffect(() => {
    edgesRef.current = edges;

    const newNodes: RenderNode[] = nodes.map((n) => {
      const existing = nodesRef.current.find((rn) => rn.id === n.id);
      if (existing && existing.status === n.status) {
        return { ...existing, type: n.type, status: n.status, label: n.label || n.id };
      }

      const radius = n.type === 'agent' ? 20 : n.type === 'task' ? 14 : 11;
      const glowCount = n.type === 'agent' ? 6 : n.type === 'task' ? 3 : 2;

      return {
        id: n.id,
        x: existing?.x ?? width / 2 + (Math.random() - 0.5) * width * 0.5,
        y: existing?.y ?? height / 2 + (Math.random() - 0.5) * height * 0.5,
        vx: 0,
        vy: 0,
        type: n.type,
        status: n.status,
        label: n.label || n.id,
        radius,
        glowPhase: Math.random() * Math.PI * 2,
        glowParticles: Array.from({ length: glowCount }, (_, i) => ({
          angle: (i / glowCount) * Math.PI * 2 + Math.random() * 0.5,
          speed: 0.008 + Math.random() * 0.015,
          radius: radius + 6 + Math.random() * 14,
          size: 2 + Math.random() * 3,
          alpha: 0.3 + Math.random() * 0.5,
        })),
      };
    });

    nodesRef.current = newNodes;
    setRenderNodes(newNodes);

    // Send init to worker
    if (workerRef.current) {
      workerRef.current.postMessage({
        type: 'init',
        nodes: newNodes.map((n) => ({
          id: n.id,
          x: n.x,
          y: n.y,
          vx: n.vx,
          vy: n.vy,
          radius: n.radius,
          type: n.type,
        })),
        edges: edges.map((e) => ({ from: e.from, to: e.to, weight: 1 })),
        width,
        height,
      });
    }

    // Initialize flow particles
    const flowCount = qualityTierRef.current.edgeFlowEnabled ? Math.min(edges.length * 3, 80) : 0;
    const initialFlows: FlowParticle[] = [];
    for (let i = 0; i < flowCount; i++) {
      const edgeIdx = i % edges.length;
      const from = newNodes.find((n) => n.id === edges[edgeIdx].from);
      const to = newNodes.find((n) => n.id === edges[edgeIdx].to);
      if (from && to) {
        initialFlows.push({
          id: `flow-${i}`,
          edgeIndex: edgeIdx,
          from,
          to,
          progress: Math.random(),
          speed: 0.003 + Math.random() * 0.01,
          size: 2 + Math.random() * 3,
          color: STATUS_COLORS[edges[edgeIdx].status] || '#6b7280',
        });
      }
    }
    setFlowParticles(initialFlows);

    // Rebuild node label texts in PixiJS
    rebuildNodeTexts(newNodes);
  }, [nodes, edges, width, height]);

  // ── Rebuild node label/icon Text objects ─────────────────────────

  const rebuildNodeTexts = useCallback((rnodes: RenderNode[]) => {
    const nodeLayer = layersRef.current?.nodes;
    if (!nodeLayer) return;

    // Destroy old texts
    for (const t of textsRef.current.nodeLabels.values()) t.destroy();
    for (const t of textsRef.current.nodeIcons.values()) t.destroy();
    textsRef.current.nodeLabels.clear();
    textsRef.current.nodeIcons.clear();

    for (const n of rnodes) {
      const icon = TYPE_ICON[n.type] || '[dna]';
      const iconText = new Text({
        text: icon,
        style: { fontSize: n.radius, fontFamily: 'system-ui' },
      });
      iconText.anchor.set(0.5);
      iconText.x = n.x;
      iconText.y = n.y;
      nodeLayer.addChild(iconText);
      textsRef.current.nodeIcons.set(n.id, iconText);

      const labelStr = n.label.length > 14 ? n.label.slice(0, 14) + '...' : n.label;
      const labelText = new Text({
        text: labelStr,
        style: new TextStyle({
          fontSize: 10,
          fontFamily: 'system-ui',
          fill: '#6b7280',
          align: 'center',
        }),
      });
      labelText.anchor.set(0.5, 0);
      labelText.x = n.x;
      labelText.y = n.y + n.radius + 6;
      nodeLayer.addChild(labelText);
      textsRef.current.nodeLabels.set(n.id, labelText);
    }
  }, []);

  // ── Animation loop ───────────────────────────────────────────────

  useEffect(() => {
    const tick = () => {
      tickCountRef.current++;

      // ── FPS Tracking ──────────────────────────────────────────
      fpsTrackerRef.current.tick();
      const currentFps = fpsTrackerRef.current.fps;

      if (tickCountRef.current % 60 === 0) {
        setQualityTier((prev) => {
          const next = getQualityTier(currentFps);
          if (next.label !== prev.label) return next;
          return prev;
        });
      }

      // ── Physics tick ──────────────────────────────────────────
      const qTier = qualityTierRef.current;
      if (workerRef.current && tickCountRef.current % 2 === 0) {
        workerRef.current.postMessage({ type: 'tick', iterations: 1 });
      } else if (!workerRef.current) {
        mainThreadPhysicsStep(nodesRef.current, edgesRef.current, width, height);
      }

      // ── Update flow particles ─────────────────────────────────
      const flows = flowParticlesRef.current.map((fp) => {
        let progress = fp.progress + fp.speed;
        if (progress > 1) progress -= 1;
        if (progress < 0) progress += 1;
        const fromNode = nodesRef.current.find((n) => n.id === fp.from.id);
        const toNode = nodesRef.current.find((n) => n.id === fp.to.id);
        return {
          ...fp,
          progress,
          from: fromNode || fp.from,
          to: toNode || fp.to,
        };
      });
      flowParticlesRef.current = flows;

      // ── Update emergence bursts ───────────────────────────────
      const bursts = emergenceBurstsRef.current
        .map((b) => ({
          ...b,
          age: b.age + 1,
          particles: b.particles.map((p) => ({ ...p, life: p.life + 1 })),
        }))
        .filter((b) => b.age < b.maxAge);
      emergenceBurstsRef.current = bursts;

      // ── Update glow phases ────────────────────────────────────
      for (const n of nodesRef.current) {
        n.glowPhase += 0.02;
      }

      // ── RENDER with PixiJS ────────────────────────────────────
      renderPixiFrame(
        nodesRef.current,
        edgesRef.current,
        flows,
        bursts,
        hoveredNodeRef.current,
        qTier,
      );

      // ── Update particle count for monitor ─────────────────────
      const totalParticles =
        nodesRef.current.length * 3 +
        flows.length * 2 +
        bursts.flatMap((b) => b.particles).length;
      setParticleCount(totalParticles);

      animRef.current = requestAnimationFrame(tick);
    };

    animRef.current = requestAnimationFrame(tick);
    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
  }, [width, height, rebuildNodeTexts]);

  // ── Render one PixiJS frame ──────────────────────────────────────

  function renderPixiFrame(
    rnodes: RenderNode[],
    redges: TopologyEdge[],
    flows: FlowParticle[],
    bursts: EmergenceBurst[],
    hoveredId: string | null,
    qTier: QualityTier,
  ): void {
    const gfx = graphicsRef.current;
    const texts = textsRef.current;
    if (!gfx) return;

    // ── Edge lines ──────────────────────────────────────────────
    gfx.edgeLines.clear();
    for (const e of redges) {
      const from = rnodes.find((n) => n.id === e.from);
      const to = rnodes.find((n) => n.id === e.to);
      if (!from || !to) continue;

      const isHovered = hoveredId === e.from || hoveredId === e.to;
      const edgeColor = STATUS_COLORS[e.status] || '#9ca3af';
      const strokeColor = isHovered ? hexToNumber(edgeColor) : 0x9ca3af;
      const alpha = isHovered ? 1 : 0.25;
      const strokeW = isHovered ? 2 : 1;

      gfx.edgeLines
        .moveTo(from.x, from.y)
        .lineTo(to.x, to.y)
        .stroke({ color: strokeColor, width: strokeW, alpha });
    }

    // ── Flow particles on edges ─────────────────────────────────
    gfx.flowParticles.clear();
    if (qTier.edgeFlowEnabled) {
      for (const fp of flows) {
        const x = fp.from.x + (fp.to.x - fp.from.x) * fp.progress;
        const y = fp.from.y + (fp.to.y - fp.from.y) * fp.progress;
        const color = hexToNumber(fp.color);
        gfx.flowParticles.circle(x, y, fp.size).fill({ color, alpha: 0.7 });
      }
    }

    // ── Background glow ─────────────────────────────────────────
    gfx.bgGlowGfx.clear();
    if (qTier.glowEnabled) {
      for (const n of rnodes) {
        const color = hexToNumber(STATUS_COLORS[n.status] || '#9ca3af');
        const alpha = 0.06 + Math.sin(n.glowPhase) * 0.03;
        gfx.bgGlowGfx.circle(n.x, n.y, n.radius + 8).fill({ color, alpha });
      }
    }

    // ── Glow rings (ambient node glow) ──────────────────────────
    gfx.glowRings.clear();
    if (qTier.glowEnabled) {
      for (const n of rnodes) {
        const color = hexToNumber(STATUS_COLORS[n.status] || '#9ca3af');
        const alpha = 0.12 + Math.sin(n.glowPhase) * 0.04;
        gfx.glowRings.circle(n.x, n.y, n.radius + 6).fill({ color, alpha });
      }
    }

    // ── Glow orbit particles ────────────────────────────────────
    gfx.glowOrbits.clear();
    if (qTier.glowEnabled) {
      for (const n of rnodes) {
        const color = hexToNumber(STATUS_COLORS[n.status] || '#9ca3af');
        for (let gi = 0; gi < n.glowParticles.length; gi++) {
          const gp = n.glowParticles[gi];
          const angle = gp.angle + n.glowPhase * gp.speed * 10;
          const px = n.x + Math.cos(angle) * gp.radius;
          const py = n.y + Math.sin(angle) * gp.radius;
          const alpha = Math.max(0, Math.min(1, gp.alpha + Math.sin(n.glowPhase + gi) * 0.15));
          gfx.glowOrbits.circle(px, py, gp.size).fill({ color, alpha });
        }
      }
    }

    // ── Node bodies ─────────────────────────────────────────────
    gfx.nodeBodies.clear();
    for (const n of rnodes) {
      const color = hexToNumber(STATUS_COLORS[n.status] || '#9ca3af');
      const isHovered = hoveredId === n.id;

      // Outer stroke (white ring)
      gfx.nodeBodies.circle(n.x, n.y, n.radius + 2).fill({ color: 0xffffff, alpha: isHovered ? 0.9 : 0.7 });
      // Main body
      gfx.nodeBodies.circle(n.x, n.y, n.radius).fill({ color, alpha: 1 });
    }

    // ── Update icon positions ───────────────────────────────────
    for (const n of rnodes) {
      const iconText = texts.nodeIcons.get(n.id);
      if (iconText) {
        iconText.x = n.x;
        iconText.y = n.y;
        iconText.visible = true;
      }

      const labelText = texts.nodeLabels.get(n.id);
      if (labelText) {
        labelText.x = n.x;
        labelText.y = n.y + n.radius + 6;
        const isHovered = hoveredId === n.id;
        labelText.style.fill = isHovered ? '#1f2937' : '#6b7280';
        labelText.style.fontWeight = isHovered ? '700' : '400';
      }
    }

    // ── Emergence bursts ────────────────────────────────────────
    gfx.emergenceGfx.clear();
    if (qTier.emergenceEnabled) {
      for (const burst of bursts) {
        for (const bp of burst.particles) {
          const dist = bp.speed * bp.life;
          const px = burst.x + Math.cos(bp.angle) * dist;
          const py = burst.y + Math.sin(bp.angle) * dist;
          const lifeRatio = 1 - bp.life / bp.maxLife;
          const alpha = Math.max(0, lifeRatio * 0.8);
          const color = hexToNumber(bp.color);
          gfx.emergenceGfx.circle(px, py, bp.size * lifeRatio).fill({ color, alpha });
        }
      }
    }
  }

  // ── Trigger emergence burst (exposed for external calls) ─────────

  const triggerEmergence = useCallback(
    (x: number, y: number) => {
      const qTier = qualityTierRef.current;
      if (!qTier.emergenceEnabled) return;
      const id = `burst-${Date.now()}`;
      const burst: EmergenceBurst = {
        id,
        x,
        y,
        particles: Array.from({ length: 20 }, (_, i) => ({
          angle: (i / 20) * Math.PI * 2,
          speed: 1 + Math.random() * 4,
          life: 0,
          maxLife: 30 + Math.random() * 20,
          size: 2 + Math.random() * 4,
          color: Math.random() > 0.5 ? '#8b5cf6' : '#06b6d4',
        })),
        age: 0,
        maxAge: 60,
      };
      setEmergenceBursts((prev) => [...prev, burst].slice(-5));
    },
    [],
  );

  // ── Hover tooltip ────────────────────────────────────────────────

  const hoveredNode = hoveredNodeId
    ? renderNodes.find((n) => n.id === hoveredNodeId)
    : null;

  const statusLabelMap: Record<string, string> = {
    idle: '空闲',
    busy: '忙碌',
    offline: '离线',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
  };

  return (
    <div style={{ position: 'relative', width, height }}>
      <PerformanceMonitor visible={perfVisible} particleCount={particleCount} />

      {/* PixiJS canvas container */}
      <div ref={containerRef} style={{ width, height }} />

      {/* Hover tooltip (React overlay) */}
      {hoveredNode && (
        <div
          style={{
            position: 'absolute',
            left: hoveredNode.x + hoveredNode.radius + 12,
            top: hoveredNode.y - 20,
            background: 'rgba(17,24,39,0.92)',
            color: '#f9fafb',
            borderRadius: 8,
            padding: '6px 10px',
            fontSize: 10,
            fontFamily: 'system-ui',
            pointerEvents: 'none',
            zIndex: 10,
            whiteSpace: 'nowrap',
          }}
        >
          <div style={{ fontWeight: 700, marginBottom: 2 }}>{hoveredNode.label}</div>
          <div style={{ opacity: 0.75 }}>
            类型: {hoveredNode.type} · 状态: {statusLabelMap[hoveredNode.status] || hoveredNode.status}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Fallback: Main-thread physics (when Web Worker unavailable) ──

function mainThreadPhysicsStep(
  simNodes: RenderNode[],
  edges: TopologyEdge[],
  width: number,
  height: number,
): void {
  const repulsion = 800;
  const attraction = 0.005;
  const damping = 0.85;
  const centerGravity = 0.01;

  const adj = new Map<string, Set<string>>();
  for (const e of edges) {
    if (!adj.has(e.from)) adj.set(e.from, new Set());
    adj.get(e.from)!.add(e.to);
    if (!adj.has(e.to)) adj.set(e.to, new Set());
    adj.get(e.to)!.add(e.from);
  }

  for (let i = 0; i < simNodes.length; i++) {
    const a = simNodes[i];

    for (let j = i + 1; j < simNodes.length; j++) {
      const b = simNodes[j];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const force = repulsion / (dist * dist);
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      a.vx -= fx;
      a.vy -= fy;
      b.vx += fx;
      b.vy += fy;
    }

    const neighbors = adj.get(a.id);
    if (neighbors) {
      for (const nid of neighbors) {
        const b = simNodes.find((n) => n.id === nid);
        if (!b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = dist * attraction;
        a.vx += (dx / dist) * force;
        a.vy += (dy / dist) * force;
      }
    }

    a.vx += (width / 2 - a.x) * centerGravity;
    a.vy += (height / 2 - a.y) * centerGravity;
    a.vx *= damping;
    a.vy *= damping;
    a.x += a.vx;
    a.y += a.vy;

    a.x = Math.max(a.radius, Math.min(width - a.radius, a.x));
    a.y = Math.max(a.radius, Math.min(height - a.radius, a.y));
  }
}
