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

function fmtErr(detail: unknown, fallback: string): string {
  return useAuthStore.getState().fmtErr(detail, fallback);
}

// ── Demo data generator (works without backend) ────────────────────

function generateDemoVersions(agentId: string): AgentVersion[] {
  const now = Date.now();
  const baseConfig = {
    agentId,
    domain: 'general',
    adapterType: 'deepseek',
    baseModelName: 'deepseek-v3',
    rankLevel: 'L1',
    displayName: 'Demo Agent',
    dutyNote: '通用助手',
    capabilityTags: ['chat', 'code'],
    systemPrompt: 'You are a helpful assistant.',
    userPrompt: '',
    assistantPrompt: '',
  };

  const versions: AgentVersion[] = [
    {
      id: `v-${agentId}-1`,
      agentId,
      version: 1,
      snapshot: { ...baseConfig },
      changeSummary: '初始创建',
      changedFields: ['agentId', 'domain', 'adapterType', 'baseModelName', 'rankLevel', 'displayName', 'dutyNote', 'capabilityTags', 'systemPrompt'],
      createdBy: 'admin',
      createdAt: new Date(now - 7 * 86400000).toISOString(),
    },
    {
      id: `v-${agentId}-2`,
      agentId,
      version: 2,
      snapshot: {
        ...baseConfig,
        displayName: 'Demo Agent v2',
        dutyNote: '增强版通用助手',
        systemPrompt: 'You are a helpful assistant. Always respond in a structured format.',
        capabilityTags: ['chat', 'code', 'analysis'],
      },
      changeSummary: '更新显示名称、职责说明、System Prompt，新增分析能力标签',
      changedFields: ['displayName', 'dutyNote', 'systemPrompt', 'capabilityTags'],
      createdBy: 'admin',
      createdAt: new Date(now - 5 * 86400000).toISOString(),
    },
    {
      id: `v-${agentId}-3`,
      agentId,
      version: 3,
      snapshot: {
        ...baseConfig,
        displayName: 'Demo Agent v2',
        dutyNote: '增强版通用助手',
        systemPrompt: 'You are a helpful assistant. Always respond in a structured format.',
        capabilityTags: ['chat', 'code', 'analysis'],
        baseModelName: 'deepseek-v4-pro',
        rankLevel: 'L2',
        userPrompt: 'Please analyze the following: {{input}}',
      },
      changeSummary: '升级模型到 deepseek-v4-pro，调整等级为 L2，新增 User Prompt 模板',
      changedFields: ['baseModelName', 'rankLevel', 'userPrompt'],
      createdBy: 'admin',
      createdAt: new Date(now - 2 * 86400000).toISOString(),
    },
    {
      id: `v-${agentId}-4`,
      agentId,
      version: 4,
      snapshot: {
        ...baseConfig,
        displayName: 'Demo Agent v2',
        dutyNote: '增强版通用助手',
        systemPrompt: 'You are a helpful assistant. Always respond in a structured JSON format.\n\n## Rules\n1. Be concise\n2. Use code blocks for code\n3. Cite sources when applicable',
        capabilityTags: ['chat', 'code', 'analysis'],
        baseModelName: 'deepseek-v4-pro',
        rankLevel: 'L2',
        userPrompt: 'Please analyze the following: {{input}}',
        assistantPrompt: 'I will analyze the input and provide a structured response.',
      },
      changeSummary: '增强 System Prompt（添加规则区块），新增 Assistant Prompt',
      changedFields: ['systemPrompt', 'assistantPrompt'],
      createdBy: 'dev-user',
      createdAt: new Date(now - 1 * 86400000).toISOString(),
    },
  ];

  return versions;
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
        set({ versions: data.versions, loading: false });
      } else {
        // Fallback: use demo data when backend is not available
        const demo = generateDemoVersions(agentId);
        set({ versions: demo, loading: false });
      }
    } catch {
      // Network error — use demo data
      const demo = generateDemoVersions(agentId);
      set({ versions: demo, loading: false });
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
        // Demo mode: simulate success
        useAdminStore.getState().setNotice(`[演示] 已回滚 Agent "${agentId}" 到版本 v${targetVersion}`);
        set({ rollingBack: false });
        return true;
      }
    } catch {
      // Demo mode fallback
      useAdminStore.getState().setNotice(`[演示] 已回滚 Agent "${agentId}" 到版本 v${targetVersion}`);
      set({ rollingBack: false });
      return true;
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
