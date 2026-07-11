/**
 * Web Worker: Physics Simulation Engine
 *
 * Offloads force-directed graph physics from the main thread
 * to avoid blocking UI rendering. Communicates via postMessage.
 *
 * Part of AgentHub V5.1 P0 Performance Optimization
 *
 * Usage:
 *   const worker = new Worker(new URL('./physics.worker.ts', import.meta.url));
 *   worker.postMessage({ type: 'init', nodes: [...], edges: [...], width, height });
 *   worker.onmessage = (e) => { updatePositions(e.data.positions); };
 *   worker.postMessage({ type: 'tick' }); // each animation frame
 */

export interface WorkerSimNode {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  type: string; // 'agent' | 'task' | 'spawn'
  fixed?: boolean;
}

export interface WorkerSimEdge {
  from: string;
  to: string;
  weight?: number; // 0-1, higher = stronger attraction
}

interface InitMessage {
  type: 'init';
  nodes: WorkerSimNode[];
  edges: WorkerSimEdge[];
  width: number;
  height: number;
}

interface UpdateMessage {
  type: 'update';
  nodes?: WorkerSimNode[];
  edges?: WorkerSimEdge[];
  width?: number;
  height?: number;
}

interface TickMessage {
  type: 'tick';
  iterations?: number; // how many sim steps to run (default 1)
}

interface SetParamMessage {
  type: 'setParam';
  repulsion?: number;
  attraction?: number;
  damping?: number;
  centerGravity?: number;
}

interface ResetMessage {
  type: 'reset';
}

type WorkerMessage = InitMessage | UpdateMessage | TickMessage | SetParamMessage | ResetMessage;

interface WorkerResponse {
  positions: Array<{
    id: string;
    x: number;
    y: number;
    vx: number;
    vy: number;
  }>;
  /** Whether simulation has settled (total kinetic energy below threshold) */
  settled: boolean;
  /** Total kinetic energy for debugging */
  energy: number;
}

// ── Simulation State ───────────────────────────────────────────────

let simNodes: WorkerSimNode[] = [];
const adj = new Map<string, Set<string>>();
let simEdges: WorkerSimEdge[] = [];
let simWidth = 800;
let simHeight = 600;

let repulsion = 800;
let attraction = 0.005;
let damping = 0.85;
let centerGravity = 0.01;

const SETTLED_THRESHOLD = 0.5; // total kinetic energy below this = settled

// ── Build adjacency map from edges ──────────────────────────────────

function buildAdjacency(): void {
  adj.clear();
  for (const e of simEdges) {
    if (!adj.has(e.from)) adj.set(e.from, new Set());
    adj.get(e.from)!.add(e.to);
    if (!adj.has(e.to)) adj.set(e.to, new Set());
    adj.get(e.to)!.add(e.from);
  }
}

// ── Single simulation step ──────────────────────────────────────────

function simulateStep(): { energy: number } {
  let totalEnergy = 0;

  for (let i = 0; i < simNodes.length; i++) {
    const a = simNodes[i];
    if (a.fixed) continue;

    // Repulsion between all pairs (optimized: only j > i to avoid double computation)
    for (let j = i + 1; j < simNodes.length; j++) {
      const b = simNodes[j];
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      const distSq = dx * dx + dy * dy;
      const dist = Math.sqrt(distSq) || 1;
      const minDist = a.radius + b.radius + 10; // minimum separation
      const effectiveDist = Math.max(dist, minDist);
      const force = repulsion / (effectiveDist * effectiveDist);

      // Normalize direction
      const nx = dx / dist;
      const ny = dy / dist;

      if (!a.fixed) {
        a.vx -= nx * force;
        a.vy -= ny * force;
      }
      if (!b.fixed) {
        b.vx += nx * force;
        b.vy += ny * force;
      }
    }

    // Attraction along edges
    const neighbors = adj.get(a.id);
    if (neighbors) {
      for (const neighborId of neighbors) {
        const b = simNodes.find((n) => n.id === neighborId);
        if (!b) continue;
        const edge = simEdges.find(
          (e) =>
            (e.from === a.id && e.to === b.id) ||
            (e.from === b.id && e.to === a.id),
        );
        const weight = edge?.weight ?? 1;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = dist * attraction * weight;
        a.vx += (dx / dist) * force;
        a.vy += (dy / dist) * force;
      }
    }

    // Center gravity
    a.vx += (simWidth / 2 - a.x) * centerGravity;
    a.vy += (simHeight / 2 - a.y) * centerGravity;

    // Apply velocity with damping
    a.vx *= damping;
    a.vy *= damping;
    a.x += a.vx;
    a.y += a.vy;

    // Boundary constraints
    const margin = a.radius;
    if (a.x < margin) { a.x = margin; a.vx *= -0.3; }
    if (a.x > simWidth - margin) { a.x = simWidth - margin; a.vx *= -0.3; }
    if (a.y < margin) { a.y = margin; a.vy *= -0.3; }
    if (a.y > simHeight - margin) { a.y = simHeight - margin; a.vy *= -0.3; }

    // Accumulate kinetic energy
    totalEnergy += Math.abs(a.vx) + Math.abs(a.vy);
  }

  return { energy: totalEnergy };
}

// ── Build response payload ──────────────────────────────────────────

function buildResponse(energy: number): WorkerResponse {
  return {
    positions: simNodes.map((n) => ({
      id: n.id,
      x: Math.round(n.x * 100) / 100,
      y: Math.round(n.y * 100) / 100,
      vx: n.vx,
      vy: n.vy,
    })),
    settled: energy < SETTLED_THRESHOLD,
    energy: Math.round(energy * 100) / 100,
  };
}

// ── Message Handler ─────────────────────────────────────────────────

self.onmessage = (e: MessageEvent<WorkerMessage>): void => {
  const msg = e.data;

  switch (msg.type) {
    case 'init': {
      simNodes = msg.nodes.map((n) => ({
        ...n,
        x: n.x + (Math.random() - 0.5) * 10, // small initial jitter
        y: n.y + (Math.random() - 0.5) * 10,
        vx: 0,
        vy: 0,
      }));
      simEdges = msg.edges;
      simWidth = msg.width;
      simHeight = msg.height;
      buildAdjacency();

      // Run initial iterations to spread nodes
      for (let i = 0; i < 50; i++) {
        simulateStep();
      }
      const { energy } = simulateStep();
      (self as unknown as Worker).postMessage(buildResponse(energy));
      break;
    }

    case 'update': {
      if (msg.nodes) {
        // Merge new/updated nodes while preserving positions of existing nodes
        const existingMap = new Map(simNodes.map((n) => [n.id, n]));
        simNodes = msg.nodes.map((n) => {
          const existing = existingMap.get(n.id);
          if (existing) {
            return {
              ...n,
              x: existing.x,
              y: existing.y,
              vx: existing.vx,
              vy: existing.vy,
            };
          }
          return { ...n, vx: 0, vy: 0 };
        });
        // Remove nodes no longer present
        const newIds = new Set(msg.nodes.map((n) => n.id));
        simNodes = simNodes.filter((n) => newIds.has(n.id));
      }
      if (msg.edges) {
        simEdges = msg.edges;
        buildAdjacency();
      }
      if (msg.width) simWidth = msg.width;
      if (msg.height) simHeight = msg.height;

      const { energy } = simulateStep();
      (self as unknown as Worker).postMessage(buildResponse(energy));
      break;
    }

    case 'tick': {
      const iterations = msg.iterations ?? 1;
      let energy = 0;
      for (let i = 0; i < iterations; i++) {
        const result = simulateStep();
        energy = result.energy;
      }
      (self as unknown as Worker).postMessage(buildResponse(energy));
      break;
    }

    case 'setParam': {
      if (msg.repulsion !== undefined) repulsion = msg.repulsion;
      if (msg.attraction !== undefined) attraction = msg.attraction;
      if (msg.damping !== undefined) damping = msg.damping;
      if (msg.centerGravity !== undefined) centerGravity = msg.centerGravity;
      break;
    }

    case 'reset': {
      // Re-randomize positions
      for (const n of simNodes) {
        if (!n.fixed) {
          n.x = simWidth / 2 + (Math.random() - 0.5) * simWidth * 0.6;
          n.y = simHeight / 2 + (Math.random() - 0.5) * simHeight * 0.6;
          n.vx = 0;
          n.vy = 0;
        }
      }
      for (let i = 0; i < 50; i++) simulateStep();
      const { energy } = simulateStep();
      (self as unknown as Worker).postMessage(buildResponse(energy));
      break;
    }

    default:
      break;
  }
};

// Signal that the worker is ready
(self as unknown as Worker).postMessage({ type: 'ready' });
