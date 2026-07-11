import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { useAdminStore } from './adminStore';
import type { Workspace, WorkspaceMember } from '../types';

interface WorkspaceState {
  workspaces: Workspace[];
  currentWorkspaceId: string;
  currentWorkspace: Workspace | null;
  members: WorkspaceMember[];
  isLoading: boolean;
  inviteEmail: string;
  inviteRole: 'admin' | 'editor' | 'viewer';

  loadWorkspaces: () => Promise<void>;
  createWorkspace: (name: string, description: string) => Promise<void>;
  deleteWorkspace: (id: string) => Promise<void>;
  loadMembers: (id: string) => Promise<void>;
  inviteMember: (id: string, email: string, role: string) => Promise<void>;
  removeMember: (id: string, userId: string) => Promise<void>;
  setCurrentWorkspaceId: (id: string) => void;
  setInviteEmail: (v: string) => void;
  setInviteRole: (v: 'admin' | 'editor' | 'viewer') => void;
}

const BASE = '/platform/workspaces';
const STORAGE_KEY = 'agenthub_workspace_id';

async function api(path: string, options: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
    ...options,
  });
  return res;
}

export const useWorkspaceStore = create<WorkspaceState>()((set, get) => ({
  workspaces: [],
  currentWorkspaceId: (typeof window !== 'undefined' ? localStorage.getItem(STORAGE_KEY) : null) || 'ws-default',
  currentWorkspace: null,
  members: [],
  isLoading: false,
  inviteEmail: '',
  inviteRole: 'editor',

  loadWorkspaces: async () => {
    set({ isLoading: true });
    try {
      const res = await api('');
      if (res.ok) {
        const data = await res.json();
        const workspaces: Workspace[] = data.workspaces || [];
        set({ workspaces });
        // Ensure current workspace exists in list
        const currentId = get().currentWorkspaceId;
        if (currentId && !workspaces.find((w) => w.id === currentId)) {
          const defaultWs = workspaces.find((w) => w.id === 'ws-default') || workspaces[0];
          if (defaultWs) get().setCurrentWorkspaceId(defaultWs.id);
        }
        if (!get().currentWorkspaceId && workspaces.length > 0) {
          get().setCurrentWorkspaceId(workspaces[0].id);
        }
      }
    } catch {
      // Offline: use default workspace
      set({
        workspaces: [{
          id: 'ws-default', name: 'Default', description: 'Default workspace',
          owner_id: 'system', member_count: 1, created_at: new Date().toISOString(),
        }],
      });
    } finally {
      set({ isLoading: false });
    }
  },

  createWorkspace: async (name, description) => {
    const id = 'ws-' + Date.now().toString(36);
    try {
      const res = await api('', {
        method: 'POST',
        body: JSON.stringify({ id, name, description }),
      });
      if (res.ok) {
        useAdminStore.getState().setNotice(`已创建工作空间: ${name}`);
        await get().loadWorkspaces();
        get().setCurrentWorkspaceId(id);
      } else {
        const data = await res.json().catch(() => ({}));
        useAdminStore.getState().setNotice(`创建失败: ${(data as { detail?: string }).detail || '未知错误'}`);
      }
    } catch {
      // Add locally
      const ws: Workspace = { id, name, description, owner_id: 'local', member_count: 1, created_at: new Date().toISOString() };
      set((s) => ({ workspaces: [...s.workspaces, ws], currentWorkspaceId: id }));
      useAdminStore.getState().setNotice(`已创建工作空间: ${name}`);
    }
  },

  deleteWorkspace: async (id) => {
    if (id === 'ws-default') { useAdminStore.getState().setNotice('默认工作空间不可删除'); return; }
    if (typeof window !== 'undefined' && !window.confirm('确认删除此工作空间？')) return;
    try { await api(`/${encodeURIComponent(id)}`, { method: 'DELETE' }); } catch { /* ignore */ }
    set((s) => ({ workspaces: s.workspaces.filter((w) => w.id !== id) }));
    if (get().currentWorkspaceId === id) {
      const remaining = get().workspaces;
      get().setCurrentWorkspaceId(remaining.length > 0 ? remaining[0].id : 'ws-default');
    }
    useAdminStore.getState().setNotice('工作空间已删除');
  },

  loadMembers: async (id) => {
    try {
      const res = await api(`/${encodeURIComponent(id)}/members`);
      if (res.ok) {
        const data = await res.json();
        set({ members: data.members || [] });
      }
    } catch { /* ignore */ }
  },

  inviteMember: async (id, email, role) => {
    try {
      const res = await api(`/${encodeURIComponent(id)}/members`, {
        method: 'POST',
        body: JSON.stringify({ email, role }),
      });
      if (res.ok) {
        useAdminStore.getState().setNotice(`已邀请 ${email}`);
        await get().loadMembers(id);
        set({ inviteEmail: '', inviteRole: 'editor' });
      } else {
        useAdminStore.getState().setNotice('邀请失败');
      }
    } catch {
      useAdminStore.getState().setNotice('邀请失败，请检查网络');
    }
  },

  removeMember: async (id, userId) => {
    if (typeof window !== 'undefined' && !window.confirm('确认移除该成员？')) return;
    try {
      const res = await api(`/${encodeURIComponent(id)}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' });
      if (res.ok) {
        useAdminStore.getState().setNotice('成员已移除');
        await get().loadMembers(id);
      }
    } catch { /* ignore */ }
  },

  setCurrentWorkspaceId: (id) => {
    if (typeof window !== 'undefined') localStorage.setItem(STORAGE_KEY, id);
    set({ currentWorkspaceId: id });
  },
  setInviteEmail: (v) => set({ inviteEmail: v }),
  setInviteRole: (v) => set({ inviteRole: v }),
}));
