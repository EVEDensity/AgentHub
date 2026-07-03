// Agent A/B Testing Store (P2-3)
// Manages traffic-split experiments comparing agent variants with
// quality scoring and statistical significance analysis.

import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { useAdminStore } from './adminStore';

// ── Types ──────────────────────────────────────────────────────────────

export interface ABTestConfig {
  id: string;
  name: string;
  description: string;
  agentId: string; // base agent
  status: 'draft' | 'running' | 'paused' | 'completed';
  variants: ABTestVariant[];
  trafficSplit: number; // percentage going to variant B (0-100)
  metrics: ABTestMetrics;
  createdAt: string;
  startedAt?: string;
  endedAt?: string;
  winnerId?: string;
}

export interface ABTestVariant {
  id: string; // "A" | "B"
  label: string;
  model?: string;
  systemPrompt?: string;
  temperature?: number;
  tools?: string[];
  config: Record<string, unknown>;
}

export interface ABTestMetrics {
  variantA: VariantMetrics;
  variantB: VariantMetrics;
  significance: number; // p-value × 100 (e.g. 95 = 95% confidence)
  totalRequests: number;
  winner: string | null;
}

export interface VariantMetrics {
  requests: number;
  avgQuality: number; // 1-10
  avgLatencyMs: number;
  avgTokenUsage: number;
  successRate: number; // 0-1
  userSatisfaction: number; // 1-10
}

export interface ABTestResult {
  testId: string;
  requestId: string;
  variantId: string;
  quality: number;
  latencyMs: number;
  tokenUsage: number;
  success: boolean;
  userRating?: number;
  timestamp: string;
}

const API_BASE = '/platform/ab-tests';

// ── Demo Data ──────────────────────────────────────────────────────────

const DEMO_TESTS: ABTestConfig[] = [
  {
    id: 'abt-001',
    name: 'System Prompt 优化对比',
    description: '对比精简 prompt (A) 与详细 prompt (B) 对代码审查质量的影响',
    agentId: 'agent-code-reviewer',
    status: 'running',
    variants: [
      { id: 'A', label: '精简 Prompt', config: { systemPrompt: 'You are a code reviewer. Be concise.', temperature: 0.3 } },
      { id: 'B', label: '详细 Prompt', config: { systemPrompt: 'You are an expert senior code reviewer with 15 years of experience. Analyze the code for security vulnerabilities, performance issues, and maintainability problems. Provide detailed actionable feedback with code examples.', temperature: 0.3 } },
    ],
    trafficSplit: 50,
    metrics: {
      variantA: { requests: 142, avgQuality: 7.2, avgLatencyMs: 850, avgTokenUsage: 1200, successRate: 0.99, userSatisfaction: 7.0 },
      variantB: { requests: 138, avgQuality: 8.1, avgLatencyMs: 1100, avgTokenUsage: 2100, successRate: 0.98, userSatisfaction: 8.3 },
      significance: 94,
      totalRequests: 280,
      winner: null,
    },
    createdAt: '2026-07-01T10:00:00Z',
    startedAt: '2026-07-01T12:00:00Z',
  },
  {
    id: 'abt-002',
    name: 'Temperature 参数测试',
    description: '低温度(0.1) vs 高温度(0.7) 对创意写作场景的影响',
    agentId: 'agent-writer',
    status: 'completed',
    variants: [
      { id: 'A', label: 'Temperature = 0.1', config: { temperature: 0.1 } },
      { id: 'B', label: 'Temperature = 0.7', config: { temperature: 0.7 } },
    ],
    trafficSplit: 50,
    metrics: {
      variantA: { requests: 200, avgQuality: 6.5, avgLatencyMs: 720, avgTokenUsage: 900, successRate: 1.0, userSatisfaction: 6.2 },
      variantB: { requests: 200, avgQuality: 8.5, avgLatencyMs: 750, avgTokenUsage: 1050, successRate: 0.99, userSatisfaction: 8.8 },
      significance: 99,
      totalRequests: 400,
      winner: 'B',
    },
    createdAt: '2026-06-28T08:00:00Z',
    startedAt: '2026-06-28T09:00:00Z',
    endedAt: '2026-07-02T18:00:00Z',
    winnerId: 'B',
  },
  {
    id: 'abt-003',
    name: '知识库检索 Top-K 对比',
    description: 'K=5 vs K=10 对回答完整性的影响',
    agentId: 'agent-rag-assistant',
    status: 'draft',
    variants: [
      { id: 'A', label: 'Top-K = 5', config: { retrievalK: 5 } },
      { id: 'B', label: 'Top-K = 10', config: { retrievalK: 10 } },
    ],
    trafficSplit: 50,
    metrics: {
      variantA: { requests: 0, avgQuality: 0, avgLatencyMs: 0, avgTokenUsage: 0, successRate: 0, userSatisfaction: 0 },
      variantB: { requests: 0, avgQuality: 0, avgLatencyMs: 0, avgTokenUsage: 0, successRate: 0, userSatisfaction: 0 },
      significance: 0,
      totalRequests: 0,
      winner: null,
    },
    createdAt: '2026-07-03T09:00:00Z',
  },
];

// ── Store ──────────────────────────────────────────────────────────────

interface ABTestState {
  tests: ABTestConfig[];
  selectedTestId: string | null;
  recentResults: ABTestResult[];
  loading: boolean;
  demoMode: boolean;

  // Actions
  loadTests: () => Promise<void>;
  selectTest: (id: string | null) => void;
  createTest: (test: Partial<ABTestConfig>) => Promise<void>;
  startTest: (id: string) => Promise<void>;
  pauseTest: (id: string) => Promise<void>;
  completeTest: (id: string) => Promise<void>;
  deleteTest: (id: string) => Promise<void>;
  getWinner: (test: ABTestConfig) => string | null;
}

export const useABTestStore = create<ABTestState>()((set, get) => ({
  tests: [],
  selectedTestId: null,
  recentResults: [],
  loading: false,
  demoMode: false,

  loadTests: async () => {
    set({ loading: true });
    try {
      const res = await fetch(API_BASE, { headers: useAuthStore.getState().authHeaders() });
      if (res.ok) {
        const data = await res.json();
        set({ tests: data.tests || [], demoMode: false });
      } else {
        set({ tests: DEMO_TESTS, demoMode: true });
      }
    } catch {
      set({ tests: DEMO_TESTS, demoMode: true });
    } finally {
      set({ loading: false });
    }
  },

  selectTest: (id) => set({ selectedTestId: id }),

  createTest: async (test) => {
    try {
      const res = await fetch(API_BASE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
        body: JSON.stringify(test),
      });
      if (res.ok) {
        useAdminStore.getState().setNotice('A/B 测试已创建');
        await get().loadTests();
      }
    } catch {
      // Demo: add locally
      const newTest: ABTestConfig = {
        id: `abt-${Date.now()}`,
        name: test.name || '新测试',
        description: test.description || '',
        agentId: test.agentId || '',
        status: 'draft',
        variants: test.variants || [
          { id: 'A', label: '对照组', config: {} },
          { id: 'B', label: '实验组', config: {} },
        ],
        trafficSplit: test.trafficSplit || 50,
        metrics: {
          variantA: { requests: 0, avgQuality: 0, avgLatencyMs: 0, avgTokenUsage: 0, successRate: 0, userSatisfaction: 0 },
          variantB: { requests: 0, avgQuality: 0, avgLatencyMs: 0, avgTokenUsage: 0, successRate: 0, userSatisfaction: 0 },
          significance: 0, totalRequests: 0, winner: null,
        },
        createdAt: new Date().toISOString(),
      };
      set((s) => ({ tests: [...s.tests, newTest] }));
      useAdminStore.getState().setNotice('A/B 测试已创建 (Demo 模式)');
    }
  },

  startTest: async (id) => {
    try {
      await fetch(`${API_BASE}/${id}/start`, { method: 'POST', headers: useAuthStore.getState().authHeaders() });
    } catch { /* demo */ }
    set((s) => ({
      tests: s.tests.map((t) => t.id === id ? { ...t, status: 'running' as const, startedAt: new Date().toISOString() } : t),
    }));
    useAdminStore.getState().setNotice('A/B 测试已启动');
  },

  pauseTest: async (id) => {
    try {
      await fetch(`${API_BASE}/${id}/pause`, { method: 'POST', headers: useAuthStore.getState().authHeaders() });
    } catch { /* demo */ }
    set((s) => ({
      tests: s.tests.map((t) => t.id === id ? { ...t, status: 'paused' as const } : t),
    }));
    useAdminStore.getState().setNotice('A/B 测试已暂停');
  },

  completeTest: async (id) => {
    try {
      await fetch(`${API_BASE}/${id}/complete`, { method: 'POST', headers: useAuthStore.getState().authHeaders() });
    } catch { /* demo */ }
    const test = get().tests.find((t) => t.id === id);
    const winner = test ? get().getWinner(test) : null;
    set((s) => ({
      tests: s.tests.map((t) => t.id === id ? { ...t, status: 'completed' as const, endedAt: new Date().toISOString(), winnerId: winner } : t),
    }));
    useAdminStore.getState().setNotice(winner ? `测试完成，胜出: Variant ${winner}` : '测试完成');
  },

  deleteTest: async (id) => {
    try {
      await fetch(`${API_BASE}/${id}`, { method: 'DELETE', headers: useAuthStore.getState().authHeaders() });
    } catch { /* demo */ }
    set((s) => ({ tests: s.tests.filter((t) => t.id !== id), selectedTestId: s.selectedTestId === id ? null : s.selectedTestId }));
    useAdminStore.getState().setNotice('A/B 测试已删除');
  },

  getWinner: (test: ABTestConfig): string | null => {
    const { variantA, variantB, significance } = test.metrics;
    if (significance < 90) return null;
    const scoreA = variantA.avgQuality * 0.5 + variantA.userSatisfaction * 0.3 + variantA.successRate * 10 * 0.2;
    const scoreB = variantB.avgQuality * 0.5 + variantB.userSatisfaction * 0.3 + variantB.successRate * 10 * 0.2;
    if (Math.abs(scoreA - scoreB) < 0.3) return null;
    return scoreB > scoreA ? 'B' : 'A';
  },
}));
