import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { useAdminStore } from './adminStore';
import type { AgentTemplate } from '../types';
import { PRESET_TEMPLATES } from '../data/presetTemplates';

interface TemplateState {
  templates: AgentTemplate[];
  activeCategory: string;
  searchKeyword: string;
  isImportModalOpen: boolean;
  isCreateModalOpen: boolean;
  selectedTemplate: AgentTemplate | null;
  importError: string;
  loading: boolean;

  loadTemplates: () => Promise<void>;
  createAgentFromTemplate: (template: AgentTemplate, agentId: string, domain: string) => Promise<boolean>;
  exportTemplate: (template: AgentTemplate) => void;
  importTemplate: (jsonStr: string) => Promise<boolean>;
  deleteTemplate: (id: string) => Promise<void>;
  setActiveCategory: (c: string) => void;
  setSearchKeyword: (k: string) => void;
  setIsImportModalOpen: (v: boolean) => void;
  setIsCreateModalOpen: (v: boolean) => void;
  setSelectedTemplate: (t: AgentTemplate | null) => void;
  setImportError: (e: string) => void;
  getFilteredTemplates: () => AgentTemplate[];
}

const BASE = '/platform/templates';

async function api(path: string, options: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
    ...options,
  });
  return res;
}

export const useTemplateStore = create<TemplateState>()((set, get) => ({
  templates: [],
  activeCategory: 'all',
  searchKeyword: '',
  isImportModalOpen: false,
  isCreateModalOpen: false,
  selectedTemplate: null,
  importError: '',
  loading: false,

  loadTemplates: async () => {
    set({ loading: true });
    try {
      const res = await api('');
      if (res.ok) {
        const data = await res.json();
        const serverTemplates: AgentTemplate[] = data.templates || [];
        // Merge server templates with presets (presets as fallback)
        const merged = [...serverTemplates];
        for (const preset of PRESET_TEMPLATES) {
          if (!merged.find((t) => t.id === preset.id)) {
            merged.push(preset as unknown as AgentTemplate);
          }
        }
        set({ templates: merged });
      } else {
        // Fallback to presets
        set({ templates: PRESET_TEMPLATES as unknown as AgentTemplate[] });
      }
    } catch {
      // Offline fallback
      set({ templates: PRESET_TEMPLATES as unknown as AgentTemplate[] });
    } finally {
      set({ loading: false });
    }
  },

  createAgentFromTemplate: async (template, agentId, domain) => {
    try {
      const config = typeof template.agent_config === 'string'
        ? JSON.parse(template.agent_config)
        : template.agent_config;
      const prompts = typeof template.prompt_json === 'string'
        ? JSON.parse(template.prompt_json)
        : template.prompt_json;

      const payload = {
        agentId: agentId || `${template.id}-${Date.now().toString(36)}`,
        domain: domain || 'general',
        adapterType: (config as Record<string, unknown>).adapterType || 'deepseek',
        baseModelName: (config as Record<string, unknown>).baseModelName || 'deepseek-chat',
        rankLevel: 'L1',
        displayName: template.name,
        capabilityTags: template.tags || [],
        dutyNote: template.description,
        systemPrompt: (prompts as Record<string, unknown>).system || '',
      };

      const res = await fetch('/api/agent/registry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        useAdminStore.getState().setNotice(`已从模板 "${template.name}" 创建 Agent: ${payload.agentId}`);
        // Update usage count locally
        set((s) => ({
          templates: s.templates.map((t) =>
            t.id === template.id ? { ...t, usage_count: t.usage_count + 1 } : t
          ),
        }));
        return true;
      } else {
        const data = await res.json().catch(() => ({}));
        useAdminStore.getState().setNotice(`创建失败: ${(data as { detail?: string }).detail || '未知错误'}`);
        return false;
      }
    } catch {
      useAdminStore.getState().setNotice('创建失败，请检查网络');
      return false;
    }
  },

  exportTemplate: (template) => {
    const json = JSON.stringify(template, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${template.id}.json`;
    a.click();
    URL.revokeObjectURL(url);
    useAdminStore.getState().setNotice(`已导出模板: ${template.name}`);
  },

  importTemplate: async (jsonStr) => {
    set({ importError: '' });
    try {
      const parsed = JSON.parse(jsonStr);
      if (!parsed.name || !parsed.id) {
        set({ importError: 'JSON 缺少必填字段: id 和 name' });
        return false;
      }
      // Assign new id to avoid conflicts
      parsed.id = `${parsed.id}-imported-${Date.now().toString(36)}`;
      parsed.source = 'user';
      parsed.author = 'imported';

      const res = await api('', {
        method: 'POST',
        body: JSON.stringify(parsed),
      });
      if (res.ok) {
        const created = await res.json();
        set((s) => ({ templates: [...s.templates, created as AgentTemplate], isImportModalOpen: false }));
        useAdminStore.getState().setNotice(`已导入模板: ${parsed.name}`);
        return true;
      } else {
        // Add locally
        set((s) => ({ templates: [...s.templates, parsed as AgentTemplate], isImportModalOpen: false }));
        useAdminStore.getState().setNotice(`已本地导入模板: ${parsed.name}`);
        return true;
      }
    } catch (e) {
      set({ importError: `JSON 解析失败: ${(e as Error).message}` });
      return false;
    }
  },

  deleteTemplate: async (id) => {
    const template = get().templates.find((t) => t.id === id);
    if (template?.source === 'builtin') {
      useAdminStore.getState().setNotice('内置模板不可删除');
      return;
    }
    if (typeof window !== 'undefined' && !window.confirm('确认删除此模板？')) return;
    try {
      await api(`/${encodeURIComponent(id)}`, { method: 'DELETE' });
    } catch { /* ignore */ }
    set((s) => ({ templates: s.templates.filter((t) => t.id !== id) }));
    useAdminStore.getState().setNotice('模板已删除');
  },

  setActiveCategory: (c) => set({ activeCategory: c }),
  setSearchKeyword: (k) => set({ searchKeyword: k }),
  setIsImportModalOpen: (v) => set({ isImportModalOpen: v }),
  setIsCreateModalOpen: (v) => set({ isCreateModalOpen: v }),
  setSelectedTemplate: (t) => set({ selectedTemplate: t }),
  setImportError: (e) => set({ importError: e }),

  getFilteredTemplates: () => {
    const { templates, activeCategory, searchKeyword } = get();
    return templates.filter((t) => {
      if (activeCategory !== 'all' && t.category !== activeCategory) return false;
      if (searchKeyword) {
        const kw = searchKeyword.toLowerCase();
        return (
          t.name.toLowerCase().includes(kw) ||
          t.description.toLowerCase().includes(kw) ||
          t.tags.some((tag) => tag.toLowerCase().includes(kw))
        );
      }
      return true;
    });
  },
}));
