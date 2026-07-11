// Agent A/B Testing Store (P2-3 → L5)
// Manages traffic-split experiments comparing agent variants with
// quality scoring and statistical significance analysis.
// L5: Real backend API with demo-mode fallback.

import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { useAdminStore } from './adminStore';

// ── Types ──────────────────────────────────────────────────────────────

export interface ABTestConfig {
  id: string;
  name: string;
  description: string;
  tenant_id: string;
  agent_id: string;
  status: 'draft' | 'running' | 'paused' | 'completed';
  variants: ABTestVariant[];
  traffic_split: number;
  metrics_config?: Record<string, number>;
  created_at: string;
  started_at?: string;
  ended_at?: string;
  total_impressions?: number;
  // Legacy fields kept for demo-mode compatibility.
  metrics?: ABTestMetrics;
  winnerId?: string;
}

export interface ABTestVariant {
  id: string;
  label?: string;
  name?: string;
  model?: string;
  systemPrompt?: string;
  temperature?: number;
  tools?: string[];
  config: Record<string, unknown>;
}

export interface ABTestMetrics {
  variantA: VariantMetrics;
  variantB: VariantMetrics;
  significance: number;
  totalRequests: number;
  winner: string | null;
}

export interface VariantMetrics {
  requests: number;
  avgQuality: number;
  avgLatencyMs: number;
  avgTokenUsage: number;
  successRate: number;
  userSatisfaction: number;
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

// L5: Backend-compatible result types.
export interface ABTestComputedResult {
  experiment_id: string;
  winner_variant_id: string;
  confidence_level: number;
  p_value: number;
  effect_size: number;
  test_method: string;
  variant_stats: Record<string, BackendVariantStats>;
}

export interface BackendVariantStats {
  count: number;
  mean_quality: number;
  mean_latency_ms: number;
  mean_tokens: number;
  success_rate: number;
  mean_satisfaction: number;
}

const API_BASE = '/platform/ab-tests';

// ── Demo Data (fallback when backend is unavailable) ───────────────────

const DEMO_TESTS: ABTestConfig[] = [
  {
    id: 'abt-001',
    name: 'System Prompt 优化对比',
    description: '对比精简 prompt (A) 与详细 prompt (B) 对代码审查质量的影响',
    tenant_id: 'demo',
    agent_id: 'agent-code-reviewer',
    status: 'running',
    variants: [
      { id: 'A', label: '精简 Prompt', config: { systemPrompt: 'You are a code reviewer. Be concise.', temperature: 0.3 } },
      { id: 'B', label: '详细 Prompt', config: { systemPrompt: 'You are an expert senior code reviewer with 15 years of experience. Analyze the code for security vulnerabilities, performance issues, and maintainability problems. Provide detailed actionable feedback with code examples.', temperature: 0.3 } },
    ],
    traffic_split: 50,
    metrics: {
      variantA: { requests: 142, avgQuality: 7.2, avgLatencyMs: 850, avgTokenUsage: 1200, successRate: 0.99, userSatisfaction: 7.0 },
      variantB: { requests: 138, avgQuality: 8.1, avgLatencyMs: 1100, avgTokenUsage: 2100, successRate: 0.98, userSatisfaction: 8.3 },
      significance: 94,
      totalRequests: 280,
      winner: null,
    },
    created_at: '2026-07-01T10:00:00Z',
    started_at: '2026-07-01T12:00:00Z',
    total_impressions: 280,
  },
  {
    id: 'abt-002',
    name: 'Temperature 参数测试',
    description: '低温度(0.1) vs 高温度(0.7) 对创意写作场景的影响',
    tenant_id: 'demo',
    agent_id: 'agent-writer',
    status: 'completed',
    variants: [
      { id: 'A', label: 'Temperature = 0.1', config: { temperature: 0.1 } },
      { id: 'B', label: 'Temperature = 0.7', config: { temperature: 0.7 } },
    ],
    traffic_split: 50,
    metrics: {
      variantA: { requests: 200, avgQuality: 6.5, avgLatencyMs: 720, avgTokenUsage: 900, successRate: 1.0, userSatisfaction: 6.2 },
      variantB: { requests: 200, avgQuality: 8.5, avgLatencyMs: 750, avgTokenUsage: 1050, successRate: 0.99, userSatisfaction: 8.8 },
      significance: 99,
      totalRequests: 400,
      winner: 'B',
    },
    created_at: '2026-06-28T08:00:00Z',
    started_at: '2026-06-28T09:00:00Z',
    ended_at: '2026-07-02T18:00:00Z',
    winnerId: 'B',
    total_impressions: 400,
  },
  {
    id: 'abt-003',
    name: '知识库检索 Top-K 对比',
    description: 'K=5 vs K=10 对回答完整性的影响',
    tenant_id: 'demo',
    agent_id: 'agent-rag-assistant',
    status: 'draft',
    variants: [
      { id: 'A', label: 'Top-K = 5', config: { retrievalK: 5 } },
      { id: 'B', label: 'Top-K = 10', config: { retrievalK: 10 } },
    ],
    traffic_split: 50,
    metrics: {
      variantA: { requests: 0, avgQuality: 0, avgLatencyMs: 0, avgTokenUsage: 0, successRate: 0, userSatisfaction: 0 },
      variantB: { requests: 0, avgQuality: 0, avgLatencyMs: 0, avgTokenUsage: 0, successRate: 0, userSatisfaction: 0 },
      significance: 0,
      totalRequests: 0,
      winner: null,
    },
    created_at: '2026-07-03T09:00:00Z',
    total_impressions: 0,
  },
];

// ── Store ──────────────────────────────────────────────────────────────

interface ABTestState {
  tests: ABTestConfig[];
  selectedTestId: string | null;
  recentResults: ABTestResult[];
  computedResults: Record<string, ABTestComputedResult>;
  loading: boolean;
  error: string | null;
  demoMode: boolean;

  // Actions
  loadTests: () => Promise<void>;
  selectTest: (id: string | null) => void;
  createTest: (test: Partial<ABTestConfig>) => Promise<void>;
  updateTest: (id: string, updates: Partial<ABTestConfig>) => Promise<void>;
  startTest: (id: string) => Promise<void>;
  pauseTest: (id: string) => Promise<void>;
  completeTest: (id: string) => Promise<ABTestComputedResult | null>;
  deleteTest: (id: string) => Promise<void>;
  recordImpression: (experimentId: string, variantId: string, sessionId: string, metrics: Record<string, unknown>) => Promise<void>;
  getResults: (experimentId: string) => Promise<ABTestComputedResult | null>;
  getWinner: (test: ABTestConfig) => string | undefined;
}

export const useABTestStore = create<ABTestState>()((set, get) => ({
  tests: [],
  selectedTestId: null,
  recentResults: [],
  computedResults: {},
  loading: false,
  error: null,
  demoMode: false,

  loadTests: async () => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(API_BASE, { headers: useAuthStore.getState().authHeaders() });
      if (res.ok) {
        const data = await res.json();
        const experiments: ABTestConfig[] = (data.experiments || []).map((exp: Record<string, unknown>) => ({
          ...exp,
          // Normalize fields for frontend compatibility.
          traffic_split: (exp.traffic_split as number) ?? 50,
          variants: Array.isArray(exp.variants) ? (exp.variants as Array<Record<string, unknown>>).map((v) => ({
            ...v,
            config: (v.config as Record<string, unknown>) || {},
          })) : [],
          total_impressions: (exp.total_impressions as number) ?? 0,
        })) as ABTestConfig[];
        set({ tests: experiments, demoMode: false, loading: false });
      } else if (res.status === 404 || res.status === 503) {
        set({ tests: DEMO_TESTS, demoMode: true, loading: false });
      } else {
        set({ tests: DEMO_TESTS, demoMode: true, loading: false });
      }
    } catch {
      set({ tests: DEMO_TESTS, demoMode: true, loading: false });
    }
  },

  selectTest: (id) => set({ selectedTestId: id }),

  createTest: async (test) => {
    set({ error: null });
    try {
      const res = await fetch(API_BASE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
        body: JSON.stringify({
          name: test.name,
          description: test.description || '',
          tenant_id: test.tenant_id || 'default',
          agent_id: test.agent_id || '',
          status: 'draft',
          traffic_split: test.traffic_split ?? 50,
          variants: test.variants || [
            { id: 'A', name: '对照组', config: {} },
            { id: 'B', name: '实验组', config: {} },
          ],
          metrics_config: test.metrics_config || { quality: 0.4, latency: 0.2, token_usage: 0.15, success_rate: 0.15, user_satisfaction: 0.1 },
        }),
      });
      if (res.ok) {
        useAdminStore.getState().setNotice('A/B 测试已创建');
        await get().loadTests();
      } else {
        // Demo fallback.
        const newTest: ABTestConfig = {
          id: `abt-${Date.now()}`,
          name: test.name || '新测试',
          description: test.description || '',
          tenant_id: test.tenant_id || 'demo',
          agent_id: test.agent_id || '',
          status: 'draft',
          variants: test.variants || [
            { id: 'A', label: '对照组', config: {} },
            { id: 'B', label: '实验组', config: {} },
          ],
          traffic_split: test.traffic_split ?? 50,
          metrics: {
            variantA: { requests: 0, avgQuality: 0, avgLatencyMs: 0, avgTokenUsage: 0, successRate: 0, userSatisfaction: 0 },
            variantB: { requests: 0, avgQuality: 0, avgLatencyMs: 0, avgTokenUsage: 0, successRate: 0, userSatisfaction: 0 },
            significance: 0, totalRequests: 0, winner: null,
          },
          created_at: new Date().toISOString(),
          total_impressions: 0,
        };
        set((s) => ({ tests: [...s.tests, newTest] }));
        useAdminStore.getState().setNotice('A/B 测试已创建 (Demo 模式)');
      }
    } catch {
      // Demo fallback.
      const newTest: ABTestConfig = {
        id: `abt-${Date.now()}`,
        name: test.name || '新测试',
        description: test.description || '',
        tenant_id: test.tenant_id || 'demo',
        agent_id: test.agent_id || '',
        status: 'draft',
        variants: test.variants || [
          { id: 'A', label: '对照组', config: {} },
          { id: 'B', label: '实验组', config: {} },
        ],
        traffic_split: test.traffic_split ?? 50,
        metrics: {
          variantA: { requests: 0, avgQuality: 0, avgLatencyMs: 0, avgTokenUsage: 0, successRate: 0, userSatisfaction: 0 },
          variantB: { requests: 0, avgQuality: 0, avgLatencyMs: 0, avgTokenUsage: 0, successRate: 0, userSatisfaction: 0 },
          significance: 0, totalRequests: 0, winner: null,
        },
        created_at: new Date().toISOString(),
        total_impressions: 0,
      };
      set((s) => ({ tests: [...s.tests, newTest] }));
      useAdminStore.getState().setNotice('A/B 测试已创建 (Demo 模式)');
    }
  },

  updateTest: async (id, updates) => {
    set({ error: null });
    try {
      const res = await fetch(`${API_BASE}/${encodeURIComponent(id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
        body: JSON.stringify(updates),
      });
      if (res.ok) {
        useAdminStore.getState().setNotice('A/B 测试已更新');
        await get().loadTests();
      } else {
        // Demo: update locally.
        set((s) => ({
          tests: s.tests.map((t) => t.id === id ? { ...t, ...updates } : t),
        }));
      }
    } catch {
      set((s) => ({
        tests: s.tests.map((t) => t.id === id ? { ...t, ...updates } : t),
      }));
    }
  },

  startTest: async (id) => {
    set({ error: null });
    try {
      const res = await fetch(`${API_BASE}/${encodeURIComponent(id)}/start`, {
        method: 'POST',
        headers: useAuthStore.getState().authHeaders(),
      });
      if (res.ok) {
        useAdminStore.getState().setNotice('A/B 测试已启动');
        await get().loadTests();
      } else {
        // Demo fallback.
        set((s) => ({
          tests: s.tests.map((t) => t.id === id ? { ...t, status: 'running' as const, started_at: new Date().toISOString() } : t),
        }));
      }
    } catch {
      set((s) => ({
        tests: s.tests.map((t) => t.id === id ? { ...t, status: 'running' as const, started_at: new Date().toISOString() } : t),
      }));
      useAdminStore.getState().setNotice('A/B 测试已启动 (Demo 模式)');
    }
  },

  pauseTest: async (id) => {
    set({ error: null });
    try {
      const res = await fetch(`${API_BASE}/${encodeURIComponent(id)}/pause`, {
        method: 'POST',
        headers: useAuthStore.getState().authHeaders(),
      });
      if (res.ok) {
        useAdminStore.getState().setNotice('A/B 测试已暂停');
        await get().loadTests();
      } else {
        set((s) => ({
          tests: s.tests.map((t) => t.id === id ? { ...t, status: 'paused' as const } : t),
        }));
      }
    } catch {
      set((s) => ({
        tests: s.tests.map((t) => t.id === id ? { ...t, status: 'paused' as const } : t),
      }));
      useAdminStore.getState().setNotice('A/B 测试已暂停 (Demo 模式)');
    }
  },

  completeTest: async (id) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(`${API_BASE}/${encodeURIComponent(id)}/complete`, {
        method: 'POST',
        headers: useAuthStore.getState().authHeaders(),
      });
      if (res.ok) {
        const result: ABTestComputedResult = await res.json();
        set((s) => ({
          computedResults: { ...s.computedResults, [id]: result },
        }));
        useAdminStore.getState().setNotice(
          result.winner_variant_id
            ? `测试完成 — 胜出方: Variant ${result.winner_variant_id} (置信度 ${result.confidence_level.toFixed(1)}%)`
            : '测试完成 — 无统计显著差异'
        );
        await get().loadTests();
        set({ loading: false });
        return result;
      } else {
        // Demo fallback.
        const test = get().tests.find((t) => t.id === id);
        const winner = test ? get().getWinner(test) : undefined;
        set((s) => ({
          tests: s.tests.map((t) => t.id === id ? { ...t, status: 'completed' as const, ended_at: new Date().toISOString(), winnerId: winner } : t),
          loading: false,
        }));
        useAdminStore.getState().setNotice(winner ? `测试完成，胜出: Variant ${winner}` : '测试完成 (Demo 模式)');
        return null;
      }
    } catch {
      const test = get().tests.find((t) => t.id === id);
      const winner = test ? get().getWinner(test) : undefined;
      set((s) => ({
        tests: s.tests.map((t) => t.id === id ? { ...t, status: 'completed' as const, ended_at: new Date().toISOString(), winnerId: winner } : t),
        loading: false,
      }));
      useAdminStore.getState().setNotice(winner ? `测试完成，胜出: Variant ${winner}` : '测试完成 (Demo 模式)');
      return null;
    }
  },

  deleteTest: async (id) => {
    set({ error: null });
    try {
      const res = await fetch(`${API_BASE}/${encodeURIComponent(id)}`, {
        method: 'DELETE',
        headers: useAuthStore.getState().authHeaders(),
      });
      if (res.ok) {
        useAdminStore.getState().setNotice('A/B 测试已删除');
        await get().loadTests();
      }
    } catch { /* demo fallback below */ }
    set((s) => ({
      tests: s.tests.filter((t) => t.id !== id),
      selectedTestId: s.selectedTestId === id ? null : s.selectedTestId,
    }));
    useAdminStore.getState().setNotice('A/B 测试已删除');
  },

  // L5: Record a single metric impression for a variant.
  recordImpression: async (experimentId, variantId, sessionId, metrics) => {
    try {
      const res = await fetch(`${API_BASE}/${encodeURIComponent(experimentId)}/impression`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
        body: JSON.stringify({
          variant_id: variantId,
          session_id: sessionId,
          metrics,
        }),
      });
      if (res.ok) {
        // Update local impression count.
        set((s) => ({
          tests: s.tests.map((t) =>
            t.id === experimentId
              ? { ...t, total_impressions: (t.total_impressions ?? 0) + 1 }
              : t
          ),
        }));
      }
    } catch { /* silently ignore in demo mode */ }
  },

  // L5: Fetch computed statistical results.
  getResults: async (experimentId) => {
    set({ error: null });
    try {
      const res = await fetch(`${API_BASE}/${encodeURIComponent(experimentId)}/results`, {
        headers: useAuthStore.getState().authHeaders(),
      });
      if (res.ok) {
        const result: ABTestComputedResult = await res.json();
        set((s) => ({
          computedResults: { ...s.computedResults, [experimentId]: result },
        }));
        return result;
      }
      return null;
    } catch {
      return null;
    }
  },

  getWinner: (test: ABTestConfig): string | undefined => {
    const { variantA, variantB, significance } = test.metrics || {
      variantA: { requests: 0, avgQuality: 0, avgLatencyMs: 0, avgTokenUsage: 0, successRate: 0, userSatisfaction: 0 },
      variantB: { requests: 0, avgQuality: 0, avgLatencyMs: 0, avgTokenUsage: 0, successRate: 0, userSatisfaction: 0 },
      significance: 0,
      totalRequests: 0,
      winner: null,
    };
    if (!variantA || !variantB) return undefined;
    if (significance < 90) return undefined;
    const scoreA = variantA.avgQuality * 0.5 + variantA.userSatisfaction * 0.3 + variantA.successRate * 10 * 0.2;
    const scoreB = variantB.avgQuality * 0.5 + variantB.userSatisfaction * 0.3 + variantB.successRate * 10 * 0.2;
    if (Math.abs(scoreA - scoreB) < 0.3) return undefined;
    return scoreB > scoreA ? 'B' : 'A';
  },
}));
