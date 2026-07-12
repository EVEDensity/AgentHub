'use client';

import { create } from 'zustand';

// ── Types ──────────────────────────────────────────────────────────

export interface GoldenDataset {
  id: string;
  tenant_id: string;
  name: string;
  description: string;
  version: number;
  item_count: number;
  tags: string[];
  created_at: string;
  updated_at: string;
}

export interface GoldenItem {
  id: string;
  dataset_id: string;
  query: string;
  expected_response: string;
  expected_chunk_ids: string[];
  expected_tool_calls: Record<string, unknown>[];
  metadata: Record<string, unknown>;
  index: number;
}

export interface EvalRun {
  id: string;
  dataset_id: string;
  tenant_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  config: Record<string, unknown>;
  results: Record<string, unknown>;
  item_results: Record<string, unknown>[];
  started_at?: string;
  completed_at?: string;
  created_at: string;
}

export interface ItemScore {
  item_index: number;
  query: string;
  actual_response: string;
  expected_response: string;
  scores: Record<string, number>;
  duration_ms: number;
  tool_calls_match: boolean;
}

export interface RegrDetail {
  metric: string;
  baseline: number;
  current: number;
  change_pct: number;
}

// ── Store ──────────────────────────────────────────────────────────

interface EvalStoreState {
  // Datasets
  datasets: GoldenDataset[];
  datasetsLoading: boolean;
  datasetsError: string | null;
  currentDataset: { dataset: GoldenDataset; items: GoldenItem[] } | null;
  currentDatasetLoading: boolean;

  // Items
  items: GoldenItem[];
  itemsLoading: boolean;

  // Runs
  runs: EvalRun[];
  runsLoading: boolean;
  runsError: string | null;
  currentRun: EvalRun | null;
  currentRunLoading: boolean;

  // Import/Export
  importResult: { imported: number; total: number } | null;

  // Validation preview
  validationResult: Record<string, unknown> | null;
  validationLoading: boolean;

  // Coverage
  coverageResult: Record<string, unknown> | null;
  coverageLoading: boolean;

  // ── Dataset Actions ──────────────────────────────────────────────

  loadDatasets: (tenantId?: string) => Promise<void>;
  createDataset: (data: Partial<GoldenDataset>) => Promise<GoldenDataset | null>;
  getDataset: (id: string) => Promise<void>;
  updateDataset: (id: string, updates: Partial<GoldenDataset>) => Promise<void>;
  deleteDataset: (id: string) => Promise<void>;

  // ── Item Actions ─────────────────────────────────────────────────

  loadItems: (datasetId: string) => Promise<void>;
  addItem: (datasetId: string, item: Partial<GoldenItem>) => Promise<GoldenItem | null>;
  updateItem: (datasetId: string, itemId: string, updates: Partial<GoldenItem>) => Promise<void>;
  deleteItem: (datasetId: string, itemId: string) => Promise<void>;

  // ── Import / Export ──────────────────────────────────────────────

  importItems: (datasetId: string, items: GoldenItem[]) => Promise<void>;
  exportItems: (datasetId: string) => Promise<GoldenItem[]>;

  // ── Validation / Coverage ────────────────────────────────────────

  validateDataset: (datasetId: string, model?: string, sampleSize?: number) => Promise<void>;
  getCoverage: (datasetId: string) => Promise<void>;

  // ── Run Actions ──────────────────────────────────────────────────

  loadRuns: (datasetId?: string, status?: string, tenantId?: string) => Promise<void>;
  createRun: (datasetId: string, config?: Record<string, unknown>, tenantId?: string) => Promise<EvalRun | null>;
  getRun: (id: string) => Promise<void>;
  cancelRun: (id: string) => Promise<void>;
  pollRunUntilComplete: (id: string, intervalMs?: number) => Promise<EvalRun>;

  // ── Helpers ──────────────────────────────────────────────────────

  clearCurrent: () => void;
}

export const useEvalStore = create<EvalStoreState>((set, get) => ({
  datasets: [],
  datasetsLoading: false,
  datasetsError: null,
  currentDataset: null,
  currentDatasetLoading: false,

  items: [],
  itemsLoading: false,

  runs: [],
  runsLoading: false,
  runsError: null,
  currentRun: null,
  currentRunLoading: false,

  importResult: null,

  validationResult: null,
  validationLoading: false,

  coverageResult: null,
  coverageLoading: false,

  // ── Dataset CRUD ──────────────────────────────────────────────────

  loadDatasets: async (tenantId = '') => {
    set({ datasetsLoading: true, datasetsError: null });
    try {
      const params = new URLSearchParams();
      if (tenantId) params.set('tenant_id', tenantId);
      const res = await fetch(`/platform/eval/datasets?${params.toString()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      set({ datasets: data.datasets || [], datasetsLoading: false });
    } catch (err) {
      set({ datasetsError: (err as Error).message, datasetsLoading: false });
    }
  },

  createDataset: async (data) => {
    try {
      const res = await fetch('/platform/eval/datasets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const created = await res.json();
      await get().loadDatasets();
      return created as GoldenDataset;
    } catch (err) {
      set({ datasetsError: (err as Error).message });
      return null;
    }
  },

  getDataset: async (id) => {
    set({ currentDatasetLoading: true });
    try {
      const res = await fetch(`/platform/eval/datasets/${id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      set({ currentDataset: data, currentDatasetLoading: false });
    } catch (err) {
      set({ datasetsError: (err as Error).message, currentDatasetLoading: false });
    }
  },

  updateDataset: async (id, updates) => {
    try {
      await fetch(`/platform/eval/datasets/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      });
      await get().loadDatasets();
    } catch (err) {
      set({ datasetsError: (err as Error).message });
    }
  },

  deleteDataset: async (id) => {
    try {
      await fetch(`/platform/eval/datasets/${id}`, { method: 'DELETE' });
      set({ currentDataset: null });
      await get().loadDatasets();
    } catch (err) {
      set({ datasetsError: (err as Error).message });
    }
  },

  // ── Item CRUD ─────────────────────────────────────────────────────

  loadItems: async (datasetId) => {
    set({ itemsLoading: true });
    try {
      const res = await fetch(`/platform/eval/datasets/${datasetId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      set({ items: data.items || [], itemsLoading: false });
    } catch (err) {
      set({ datasetsError: (err as Error).message, itemsLoading: false });
    }
  },

  addItem: async (datasetId, item) => {
    try {
      const res = await fetch(`/platform/eval/datasets/${datasetId}/items`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(item),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const created = await res.json();
      await get().getDataset(datasetId);
      return created as GoldenItem;
    } catch (err) {
      set({ datasetsError: (err as Error).message });
      return null;
    }
  },

  updateItem: async (datasetId, itemId, updates) => {
    try {
      await fetch(`/platform/eval/datasets/${datasetId}/items/${itemId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      });
      await get().getDataset(datasetId);
    } catch (err) {
      set({ datasetsError: (err as Error).message });
    }
  },

  deleteItem: async (datasetId, itemId) => {
    try {
      await fetch(`/platform/eval/datasets/${datasetId}/items/${itemId}`, {
        method: 'DELETE',
      });
      await get().getDataset(datasetId);
    } catch (err) {
      set({ datasetsError: (err as Error).message });
    }
  },

  // ── Import / Export ───────────────────────────────────────────────

  importItems: async (datasetId, items) => {
    try {
      const res = await fetch(`/platform/eval/datasets/${datasetId}/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(items),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      set({ importResult: data });
      await get().getDataset(datasetId);
    } catch (err) {
      set({ datasetsError: (err as Error).message });
    }
  },

  exportItems: async (datasetId) => {
    try {
      const res = await fetch(`/platform/eval/datasets/${datasetId}/export`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      return data as GoldenItem[];
    } catch (err) {
      set({ datasetsError: (err as Error).message });
      return [];
    }
  },

  // ── Validation / Coverage ─────────────────────────────────────────

  validateDataset: async (datasetId, model = 'mock-gpt', sampleSize = 3) => {
    set({ validationLoading: true });
    try {
      const res = await fetch('http://127.0.0.1:8001/evaluation/datasets/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dataset_id: datasetId, model, sample_size: sampleSize }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      set({ validationResult: data, validationLoading: false });
    } catch (err) {
      set({ datasetsError: (err as Error).message, validationLoading: false });
    }
  },

  getCoverage: async (datasetId) => {
    set({ coverageLoading: true });
    try {
      const res = await fetch(`http://127.0.0.1:8001/evaluation/datasets/${datasetId}/coverage`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      set({ coverageResult: data, coverageLoading: false });
    } catch (err) {
      set({ datasetsError: (err as Error).message, coverageLoading: false });
    }
  },

  // ── Run CRUD ──────────────────────────────────────────────────────

  loadRuns: async (datasetId = '', status = '', tenantId = '') => {
    set({ runsLoading: true, runsError: null });
    try {
      const params = new URLSearchParams();
      if (datasetId) params.set('dataset_id', datasetId);
      if (status) params.set('status', status);
      if (tenantId) params.set('tenant_id', tenantId);
      const res = await fetch(`/platform/eval/runs?${params.toString()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      set({ runs: data.runs || [], runsLoading: false });
    } catch (err) {
      set({ runsError: (err as Error).message, runsLoading: false });
    }
  },

  createRun: async (datasetId, config = {}, tenantId = '') => {
    try {
      const res = await fetch('/platform/eval/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dataset_id: datasetId, config, tenant_id: tenantId }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const run = await res.json();
      await get().loadRuns(datasetId);
      return run as EvalRun;
    } catch (err) {
      set({ runsError: (err as Error).message });
      return null;
    }
  },

  getRun: async (id) => {
    set({ currentRunLoading: true });
    try {
      const res = await fetch(`/platform/eval/runs/${id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const run = await res.json();
      set({ currentRun: run, currentRunLoading: false });
    } catch (err) {
      set({ runsError: (err as Error).message, currentRunLoading: false });
    }
  },

  cancelRun: async (id) => {
    try {
      await fetch(`/platform/eval/runs/${id}/cancel`, { method: 'POST' });
      await get().getRun(id);
    } catch (err) {
      set({ runsError: (err as Error).message });
    }
  },

  pollRunUntilComplete: async (id, intervalMs = 2000) => {
    return new Promise((resolve, reject) => {
      const poll = async () => {
        try {
          const res = await fetch(`/platform/eval/runs/${id}`);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const run: EvalRun = await res.json();
          set({ currentRun: run });
          if (run.status === 'completed' || run.status === 'failed' || run.status === 'cancelled') {
            await get().loadRuns(run.dataset_id);
            resolve(run);
          } else {
            setTimeout(poll, intervalMs);
          }
        } catch (err) {
          set({ runsError: (err as Error).message });
          reject(err);
        }
      };
      poll();
    });
  },

  // ── Helpers ──────────────────────────────────────────────────────

  clearCurrent: () => set({
    currentDataset: null,
    currentRun: null,
    validationResult: null,
    coverageResult: null,
    importResult: null,
  }),
}));
