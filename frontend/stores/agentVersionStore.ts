import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { useAdminStore } from './adminStore';
import type { AgentVersion, AgentVersionDiff, AgentVersionListResponse } from '../types';

// ── Types ──────────────────────────────────────────────────────────

interface AgentVersionState {
  // Data
  versions: AgentVersion[];
  selectedVersionId: string | null;
  diffBaseVersionId: string | null;     // "compare from" selection
  diffTargetVersionId: string | null;   // "compare to" selection
  currentDiff: AgentVersionDiff | null;
  loading: boolean;
  diffLoading: boolean;
  rollingBack: boolean;
  error: string;

  // Actions
  loadVersions: (agentId: string) => Promise<void>;
  getVersion: (agentId: string, version: number) => Promise<AgentVersion | null>;
  compareVersions: (agentId: string, versionA: number, versionB: number) => Promise<void>;
  rollback: (agentId: string, targetVersion: number) => Promise<boolean>;
  clearDiff: () => void;
  setSelectedVersion: (id: string | null) => void;
  setDiffBase: (id: string | null) => void;
  setDiffTarget: (id: string | null) => void;
}

// ── Helpers ────────────────────────────────────────────────────────

function apiHeaders(): Record<string, string> {
  const auth = useAuthStore.getState().authHeaders();
  return { 'Content-Type': 'application/json', ...auth };
}

// ── Store ──────────────────────────────────────────────────────────

export const useAgentVersionStore = create<AgentVersionState>()((set, get) => ({
  versions: [],
  selectedVersionId: null,
  diffBaseVersionId: null,
  diffTargetVersionId: null,
  currentDiff: null,
  loading: false,
  diffLoading: false,
  rollingBack: false,
  error: '',

  loadVersions: async (agentId: string) => {
    set({ loading: true, error: '' });
    try {
      const res = await fetch(
        `/api/platform/agent-versions/${encodeURIComponent(agentId)}`,
        { headers: apiHeaders() }
      );

      if (res.ok) {
        const data = (await res.json()) as AgentVersionListResponse;
        if (!Array.isArray(data.versions)) throw new Error('版本响应无效');
        set({ versions: data.versions, loading: false });
      } else {
        throw new Error(`加载版本失败 (HTTP ${res.status})`);
      }
    } catch (err) {
      set({ versions: [], loading: false, error: err instanceof Error ? err.message : '版本服务不可用' });
    }
  },

  getVersion: async (agentId: string, version: number) => {
    try {
      const res = await fetch(
        `/api/platform/agent-versions/${encodeURIComponent(agentId)}/${version}`,
        { headers: apiHeaders() }
      );
      if (res.ok) return (await res.json()) as AgentVersion;
      return null;
    } catch {
      return null;
    }
  },

  compareVersions: async (agentId: string, versionA: number, versionB: number) => {
    set({ diffLoading: true, error: '' });
    try {
      const res = await fetch(
        `/api/platform/agent-versions/${encodeURIComponent(agentId)}/diff?vA=${versionA}&vB=${versionB}`,
        { headers: apiHeaders() }
      );

      if (res.ok) {
        const diff = (await res.json()) as AgentVersionDiff;
        set({ currentDiff: diff, diffLoading: false });
      } else {
        // Fallback: compute diff client-side from loaded versions
        const { versions } = get();
        const va = versions.find(v => v.version === versionA);
        const vb = versions.find(v => v.version === versionB);
        if (va && vb) {
          set({ currentDiff: computeClientDiff(va, vb), diffLoading: false });
        } else {
          set({ error: '无法加载版本数据以进行对比', diffLoading: false });
        }
      }
    } catch {
      // Fallback to client-side diff
      const { versions } = get();
      const va = versions.find(v => v.version === versionA);
      const vb = versions.find(v => v.version === versionB);
      if (va && vb) {
        set({ currentDiff: computeClientDiff(va, vb), diffLoading: false });
      } else {
        set({ error: '无法加载版本数据以进行对比', diffLoading: false });
      }
    }
  },

  rollback: async (agentId: string, targetVersion: number) => {
    set({ rollingBack: true, error: '' });
    try {
      const res = await fetch(
        `/api/platform/agent-versions/${encodeURIComponent(agentId)}/rollback`,
        {
          method: 'POST',
          headers: apiHeaders(),
          body: JSON.stringify({ agentId, targetVersion }),
        }
      );

      if (res.ok) {
        useAdminStore.getState().setNotice(`已回滚 Agent "${agentId}" 到版本 v${targetVersion}`);
        set({ rollingBack: false });
        return true;
      } else {
        throw new Error(`回滚失败 (HTTP ${res.status})`);
      }
    } catch (err) {
      set({ rollingBack: false, error: err instanceof Error ? err.message : '回滚服务不可用' });
      return false;
    }
  },

  clearDiff: () => set({
    currentDiff: null,
    diffBaseVersionId: null,
    diffTargetVersionId: null,
  }),

  setSelectedVersion: (id) => set({ selectedVersionId: id }),
  setDiffBase: (id) => set({ diffBaseVersionId: id }),
  setDiffTarget: (id) => set({ diffTargetVersionId: id }),
}));

// ── Client-side diff computation (fallback) ────────────────────────

const FIELD_LABELS: Record<string, string> = {
  agentId: 'Agent ID',
  domain: '领域',
  adapterType: '适配器类型',
  baseModelName: '基础模型',
  rankLevel: '等级',
  displayName: '显示名称',
  dutyNote: '职责说明',
  avatarUrl: '头像 URL',
  capabilityTags: '能力标签',
  baseUrl: 'Base URL',
  systemPrompt: 'System Prompt',
  userPrompt: 'User Prompt',
  assistantPrompt: 'Assistant Prompt',
};

function computeClientDiff(va: AgentVersion, vb: AgentVersion): AgentVersionDiff {
  const allFields = new Set([
    ...Object.keys(va.snapshot),
    ...Object.keys(vb.snapshot),
  ]);

  const fieldDiffs = Array.from(allFields).map((field) => {
    const oldVal = va.snapshot[field];
    const newVal = vb.snapshot[field];
    const oldStr = JSON.stringify(oldVal);
    const newStr = JSON.stringify(newVal);

    let type: 'added' | 'removed' | 'modified' | 'unchanged';
    if (oldVal === undefined && newVal !== undefined) {
      type = 'added';
    } else if (oldVal !== undefined && newVal === undefined) {
      type = 'removed';
    } else if (oldStr !== newStr) {
      type = 'modified';
    } else {
      type = 'unchanged';
    }

    return {
      field,
      label: FIELD_LABELS[field] || field,
      oldValue: oldVal,
      newValue: newVal,
      type,
    };
  });

  return {
    versionA: va.version,
    versionB: vb.version,
    fieldDiffs: fieldDiffs.filter(d => d.type !== 'unchanged'),
    createdAtA: va.createdAt,
    createdAtB: vb.createdAt,
  };
}
