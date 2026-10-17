import { create } from 'zustand';
import { useAuthStore } from './authStore';

export interface UsageDay {
  date: string;
  tokens: number;
  sessions: number;
  agents: number;
}

export interface TenantBilling {
  tenant_id: string;
  plan: string;
  daily_tokens: number;
  monthly_tokens: number;
  used_tokens: number;
  used_sessions: number;
  used_agents: number;
  cycle_start: string;
  cycle_end: string;
  status: string;
}

export interface CostEstimate {
  total: number;
  byModel: { model: string; tokens: number; cost: number }[];
  byAgent: { agent_id: string; tokens: number; cost: number }[];
  sandboxHours: number;
  sandboxCost: number;
}

interface CostState {
  // State
  costLoading: boolean;
  costError: string;
  tokenData: { days: UsageDay[] } | null;
  billingByTenant: TenantBilling[];
  costEstimate: CostEstimate | null;

  // Models
  modelPrices: Record<string, number>;

  // Actions
  loadTokenUsage: () => Promise<void>;
  loadBilling: () => Promise<void>;
  computeCostEstimate: () => void;
  init: () => Promise<void>;
}

// Default model pricing per 1K tokens (USD)
const DEFAULT_MODEL_PRICES: Record<string, number> = {
  'gpt-4': 0.03,
  'gpt-4-turbo': 0.01,
  'gpt-4o': 0.005,
  'gpt-4o-mini': 0.00015,
  'gpt-3.5-turbo': 0.0005,
  'claude-3-opus': 0.015,
  'claude-3-sonnet': 0.003,
  'claude-3-haiku': 0.00025,
  'claude-3.5-sonnet': 0.003,
  'deepseek-v3': 0.00027,
  'deepseek-r1': 0.00055,
  'gemini-2.0-flash': 0.0001,
  'bge-large-zh-v1.5': 0.00002,
  default: 0.001,
};

export const useCostStore = create<CostState>()((set, get) => ({
  costLoading: false,
  costError: '',
  tokenData: null,
  billingByTenant: [],
  costEstimate: null,
  modelPrices: DEFAULT_MODEL_PRICES,

  loadTokenUsage: async () => {
    set({ costLoading: true, costError: '' });
    try {
      const token = useAuthStore.getState().token;
      const res = await fetch('/api/admin/analytics/token-usage', {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data && typeof data === 'object' && Array.isArray(data.days)) {
        set({ tokenData: data });
        get().computeCostEstimate();
      } else {
        throw new Error('用量响应无效');
      }
    } catch (e) {
      set({ tokenData: null, costEstimate: null, costError: e instanceof Error ? e.message : 'Failed to load token data' });
    } finally {
      set({ costLoading: false });
    }
  },

  loadBilling: async () => {
    try {
      const token = useAuthStore.getState().token;
      const res = await fetch('/iam/billing', {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) throw new Error(`账单加载失败 (HTTP ${res.status})`);
      const data = await res.json();
      if (!Array.isArray(data?.cycles)) throw new Error('账单响应无效');
      set({ billingByTenant: data.cycles });
    } catch (err) {
      set({ billingByTenant: [], costError: err instanceof Error ? err.message : '账单服务不可用' });
    }
  },

  computeCostEstimate: () => {
    const { tokenData, modelPrices } = get();
    if (!tokenData || !tokenData.days.length) { set({ costEstimate: null }); return; }

    const totalTokens = tokenData.days.reduce((sum, d) => sum + d.tokens, 0);
    const avgPricePerK = modelPrices.default || 0.001;
    const estimatedCost = (totalTokens / 1000) * avgPricePerK;

    set({
      costEstimate: {
        total: estimatedCost,
        byModel: [{ model: 'default', tokens: totalTokens, cost: estimatedCost }],
        byAgent: [],
        sandboxHours: 0,
        sandboxCost: 0,
      },
    });
  },

  init: async () => {
    await Promise.all([get().loadTokenUsage(), get().loadBilling()]);
  },
}));
