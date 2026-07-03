'use client';

import { useEffect, useRef, useCallback, useState } from 'react';
import { Stage, Layer, Circle, Line, Text, Group } from 'react-konva';
import type { KonvaEventObject } from 'konva/lib/Node';
import type { TopologyNode, TopologyEdge } from '../../types';
import {
  FPSTracker,
  getQualityTier,
  supportsWebGL,
  type QualityTier,
} from '../../lib/performance/adaptiveQuality';
import PerformanceMonitor, { usePerformanceMonitorToggle } from './PerformanceMonitor';

/**
 * Particle-Enhanced Agent Topology Canvas
 *
 * Upgrades the previous Canvas 2D force-directed graph with:
 * - Web Worker physics simulation (off-main-thread)
 * - Glow particles around agent nodes
 * - Data-flow particles traveling along edges
 * - Emergence burst effects on shared memory events
 * - Adaptive quality based on FPS
 *
 * Part of AgentHub V5.1 P0 — Particle Topology Visualization
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

// ── SimNode with rendering state ────────────────────────────────────

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

export default function ParticleTopologyCanvas({
  nodes,
  edges,
  width = 780,
  height = 560,
}: ParticleTopologyCanvasProps): JSX.Element {
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

  const [perfVisible] = usePerformanceMonitorToggle();

  // ── Initialize Web Worker ──────────────────────────────────────

  useEffect(() => {
    try {
      const worker = new Worker(
        new URL('../../workers/physics.worker.ts', import.meta.url),
      );
      workerRef.current = worker;

      worker.onmessage = (e: MessageEvent) => {
        if (e.data.type === 'ready') return;

        const { positions, settled } = e.data;
        if (positions && positions.length > 0) {
          setRenderNodes((prev) =>
            prev.map((n) => {
              const pos = positions.find((p: { id: string }) => p.id === n.id);
              if (pos) {
                return { ...n, x: pos.x, y: pos.y, vx: pos.vx, vy: pos.vy };
              }
              return n;
            }),
          );
        }
      };

      return () => {
        worker.terminate();
        workerRef.current = null;
      };
    } catch (err) {
      console.warn('Web Worker not available, falling back to main-thread physics');
    }
  }, []);

  // ── Initialize / update nodes ──────────────────────────────────

  useEffect(() => {
    edgesRef.current = edges;

    const newNodes: RenderNode[] = nodes.map((n) => {
      const existing = nodesRef.current.find((rn) => rn.id === n.id);
      if (existing && existing.status === n.status) {
        return { ...existing, type: n.type, status: n.status, label: n.label || n.id };
      }

      // Create with glow particles
      const radius = n.type === 'agent' ? 20 : n.type === 'task' ? 14 : 11;
      const glowCount = n.type === 'agent' ? 6 : n.type === 'task' ? 3 : 2;
      const baseColor = STATUS_COLORS[n.status] || '#9ca3af';

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
    const flowCount = qualityTier.edgeFlowEnabled ? Math.min(edges.length * 3, 80) : 0;
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
  }, [nodes, edges, width, height, qualityTier.edgeFlowEnabled]);

  // ── Animation loop ─────────────────────────────────────────────

  useEffect(() => {
    const tick = () => {
      tickCountRef.current++;

      // Update FPS tracker
      fpsTrackerRef.current.tick();
      const currentFps = fpsTrackerRef.current.fps;

      // Update quality tier every 60 frames
      if (tickCountRef.current % 60 === 0) {
        setQualityTier((prev) => {
          const next = getQualityTier(currentFps);
          if (next.label !== prev.label) return next;
          return prev;
        });
      }

      // Tick worker for physics
      if (workerRef.current && tickCountRef.current % 2 === 0) {
        workerRef.current.postMessage({ type: 'tick', iterations: 1 });
      } else if (!workerRef.current) {
        // Fallback: main-thread physics (simplified)
        mainThreadPhysicsStep(nodesRef.current, edgesRef.current, width, height);
        // Update positions
        setRenderNodes([...nodesRef.current]);
      }

      // Update flow particles
      setFlowParticles((prev) =>
        prev.map((fp) => {
          let progress = fp.progress + fp.speed;
          if (progress > 1) progress -= 1;
          if (progress < 0) progress += 1;
          // Update from/to references
          const fromNode = nodesRef.current.find((n) => n.id === fp.from.id);
          const toNode = nodesRef.current.find((n) => n.id === fp.to.id);
          return {
            ...fp,
            progress,
            from: fromNode || fp.from,
            to: toNode || fp.to,
          };
        }),
      );

      // Update emergence bursts (decay)
      setEmergenceBursts((prev) =>
        prev
          .map((b) => ({
            ...b,
            age: b.age + 1,
            particles: b.particles.map((p) => ({
              ...p,
              life: p.life + 1,
            })),
          }))
          .filter((b) => b.age < b.maxAge),
      );

      // Update particle count for monitor
      const totalParticles =
        renderNodes.length * 3 + // glow particles (avg 3 per node)
        flowParticles.length * 2 + // flow + trail
        emergenceBursts.flatMap((b) => b.particles).length;
      setParticleCount(totalParticles);

      // Update glow phases
      setRenderNodes((prev) =>
        prev.map((n) => ({
          ...n,
          glowPhase: n.glowPhase + 0.02,
        })),
      );

      animRef.current = requestAnimationFrame(tick);
    };

    animRef.current = requestAnimationFrame(tick);
    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
  }, [width, height]);

  // ── Trigger emergence burst (exposed for external calls) ───────

  const triggerEmergence = useCallback(
    (x: number, y: number) => {
      if (!qualityTier.emergenceEnabled) return;
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
    [qualityTier.emergenceEnabled],
  );

  // ── Edge label visibility ──────────────────────────────────────

  const showEdgeLabels = qualityTier.label !== 'Low';

  // ── Render with Konva ──────────────────────────────────────────

  return (
    <div style={{ position: 'relative' }}>
      <PerformanceMonitor visible={perfVisible} particleCount={particleCount} />

      <Stage width={width} height={height}>
        {/* ── Background glow layer ─────────────────────────── */}
        <Layer>
          {qualityTier.glowEnabled &&
            renderNodes.map((n) => {
              const color = STATUS_COLORS[n.status] || '#9ca3af';
              return (
                <Circle
                  key={`glow-${n.id}`}
                  x={n.x}
                  y={n.y}
                  radius={n.radius + 8}
                  fill={color}
                  opacity={0.06 + Math.sin(n.glowPhase) * 0.03}
                  listening={false}
                />
              );
            })}
        </Layer>

        {/* ── Edges layer ──────────────────────────────────── */}
        <Layer>
          {edges.map((e, i) => {
            const from = renderNodes.find((n) => n.id === e.from);
            const to = renderNodes.find((n) => n.id === e.to);
            if (!from || !to) return null;

            const isHovered =
              hoveredNodeId === e.from || hoveredNodeId === e.to;
            const edgeColor = STATUS_COLORS[e.status] || '#9ca3af';

            return (
              <Group key={`edge-${i}`}>
                {/* Edge line */}
                <Line
                  points={[from.x, from.y, to.x, to.y]}
                  stroke={isHovered ? edgeColor : 'rgba(156,163,175,0.25)'}
                  strokeWidth={isHovered ? 2 : 1}
                  hitStrokeWidth={8}
                  listening={true}
                  onMouseEnter={() => {}}
                />

                {/* Label at midpoint */}
                {showEdgeLabels && e.label && (
                  <Text
                    x={(from.x + to.x) / 2 - 15}
                    y={(from.y + to.y) / 2 - 8}
                    text={e.label}
                    fontSize={9}
                    fontFamily="system-ui"
                    fill={isHovered ? '#6b7280' : '#9ca3af'}
                    align="center"
                    listening={false}
                  />
                )}
              </Group>
            );
          })}

          {/* Flow particles on edges */}
          {qualityTier.edgeFlowEnabled &&
            flowParticles.map((fp) => {
              const x = fp.from.x + (fp.to.x - fp.from.x) * fp.progress;
              const y = fp.from.y + (fp.to.y - fp.from.y) * fp.progress;
              return (
                <Circle
                  key={fp.id}
                  x={x}
                  y={y}
                  radius={fp.size}
                  fill={fp.color}
                  opacity={0.7}
                  listening={false}
                />
              );
            })}
        </Layer>

        {/* ── Nodes layer ──────────────────────────────────── */}
        <Layer>
          {renderNodes.map((n) => {
            const color = STATUS_COLORS[n.status] || '#9ca3af';
            const isHovered = hoveredNodeId === n.id;
            const icon =
              n.type === 'agent' ? '🤖' : n.type === 'task' ? '📋' : '🧬';

            return (
              <Group
                key={`node-${n.id}`}
                onMouseEnter={() => setHoveredNodeId(n.id)}
                onMouseLeave={() => setHoveredNodeId(null)}
              >
                {/* Glow particles (orbit) */}
                {qualityTier.glowEnabled &&
                  n.glowParticles.map((gp, gi) => {
                    const angle = gp.angle + n.glowPhase * gp.speed * 10;
                    const px = n.x + Math.cos(angle) * gp.radius;
                    const py = n.y + Math.sin(angle) * gp.radius;
                    return (
                      <Circle
                        key={`gp-${n.id}-${gi}`}
                        x={px}
                        y={py}
                        radius={gp.size}
                        fill={color}
                        opacity={gp.alpha + Math.sin(n.glowPhase + gi) * 0.15}
                        listening={false}
                      />
                    );
                  })}

                {/* Outer glow ring */}
                <Circle
                  x={n.x}
                  y={n.y}
                  radius={n.radius + 6}
                  fill={color}
                  opacity={0.12 + Math.sin(n.glowPhase) * 0.04}
                  listening={false}
                />

                {/* Node circle */}
                <Circle
                  x={n.x}
                  y={n.y}
                  radius={n.radius}
                  fill={color}
                  stroke="#fff"
                  strokeWidth={isHovered ? 3 : 2}
                  shadowColor={color}
                  shadowBlur={isHovered ? 16 : 6}
                  shadowOpacity={0.4}
                />

                {/* Type emoji */}
                <Text
                  x={n.x - n.radius * 0.5}
                  y={n.y - n.radius * 0.5}
                  text={icon}
                  fontSize={n.radius}
                  fontFamily="system-ui"
                  align="center"
                  verticalAlign="middle"
                  width={n.radius}
                  height={n.radius}
                  listening={false}
                />

                {/* Label */}
                <Text
                  x={n.x - 30}
                  y={n.y + n.radius + 6}
                  text={n.label.length > 14 ? n.label.slice(0, 14) + '...' : n.label}
                  fontSize={10}
                  fontFamily="system-ui"
                  fill={isHovered ? '#1f2937' : '#6b7280'}
                  fontWeight={isHovered ? 700 : 400}
                  align="center"
                  width={60}
                  listening={false}
                />
              </Group>
            );
          })}

          {/* Emergence bursts */}
          {qualityTier.emergenceEnabled &&
            emergenceBursts.map((burst) =>
              burst.particles.map((bp, i) => {
                const dist = bp.speed * bp.life;
                const px = burst.x + Math.cos(bp.angle) * dist;
                const py = burst.y + Math.sin(bp.angle) * dist;
                const lifeRatio = 1 - bp.life / bp.maxLife;
                const alpha = Math.max(0, lifeRatio * 0.8);
                return (
                  <Circle
                    key={`${burst.id}-${i}`}
                    x={px}
                    y={py}
                    radius={bp.size * lifeRatio}
                    fill={bp.color}
                    opacity={alpha}
                    listening={false}
                  />
                );
              }),
            )}
        </Layer>

        {/* ── Hover info tooltip ────────────────────────────── */}
        <Layer>
          {hoveredNodeId &&
            (() => {
              const n = renderNodes.find((rn) => rn.id === hoveredNodeId);
              if (!n) return null;
              const statusLabel =
                {
                  idle: '空闲',
                  busy: '忙碌',
                  offline: '离线',
                  running: '运行中',
                  completed: '已完成',
                  failed: '失败',
                }[n.status] || n.status;
              return (
                <Group x={n.x + n.radius + 12} y={n.y - 20}>
                  {/* Tooltip bg */}
                  <Circle x={0} y={10} radius={3} fill="rgba(17,24,39,0.9)" />
                  <Text
                    x={6}
                    y={0}
                    text={`${n.label}\n类型: ${n.type} · 状态: ${statusLabel}`}
                    fontSize={10}
                    fontFamily="system-ui"
                    fill="#f9fafb"
                    padding={6}
                    fillAfterStrokeEnabled
                  />
                </Group>
              );
            })()}
        </Layer>
      </Stage>
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

  // Build adjacency
  const adj = new Map<string, Set<string>>();
  for (const e of edges) {
    if (!adj.has(e.from)) adj.set(e.from, new Set());
    adj.get(e.from)!.add(e.to);
    if (!adj.has(e.to)) adj.set(e.to, new Set());
    adj.get(e.to)!.add(e.from);
  }

  for (let i = 0; i < simNodes.length; i++) {
    const a = simNodes[i];

    // Repulsion
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

    // Attraction
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

    // Center gravity
    a.vx += (width / 2 - a.x) * centerGravity;
    a.vy += (height / 2 - a.y) * centerGravity;

    // Damping
    a.vx *= damping;
    a.vy *= damping;
    a.x += a.vx;
    a.y += a.vy;

    // Boundary
    a.x = Math.max(a.radius, Math.min(width - a.radius, a.x));
    a.y = Math.max(a.radius, Math.min(height - a.radius, a.y));
  }
}
