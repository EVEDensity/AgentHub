import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { useAdminStore } from './adminStore';

interface TokenData {
  range: { start: string; end: string };
  today: { sessions: number; messages: number; tokens: number };
  yesterday: { sessions: number; messages: number; tokens: number };
  last30: { sessions: number; messages: number; tokens: number };
  days: Array<{ date: string; sessions: number; messages: number; tokens: number }>;
  generatedAt: string;
}

interface UserRow {
  id: string; name: string; role: string; created_at: string;
}

interface UserManagementState {
  // Token
  tokenData: TokenData | null;
  tokenLoading: boolean;
  tokenError: string;
  // Profile
  profileBio: string;
  profileEditingField: string | null;
  profileFieldDraft: string;
  profileLocation: string;
  profileEmail: string;
  profileOrg: string;
  profileAvatarUrl: string;
  profileUploading: boolean;
  // User list
  userList: UserRow[];
  userListLoading: boolean;
  userListError: string;
  // Create user form
  newUserName: string;
  newUserPassword: string;
  newUserRole: string;
  creatingUser: boolean;

  // Profile setters
  setProfileEditingField: (v: string | null) => void;
  setProfileFieldDraft: (v: string) => void;
  setNewUserName: (v: string) => void;
  setNewUserPassword: (v: string) => void;
  setNewUserRole: (v: string) => void;

  // Profile actions
  handleStartEditField: (field: string, currentValue: string) => void;
  handleSaveField: () => Promise<void>;
  handleCancelEditField: () => void;
  handleUploadProfileAvatar: (e: React.ChangeEvent<HTMLInputElement>) => void;

  // User CRUD
  handleCreateUser: (e: React.FormEvent) => Promise<void>;
  handleChangeUserRole: (userId: string, newRole: string) => Promise<void>;
  handleDeleteUser: (userId: string, userName: string) => Promise<void>;

  // Data loaders
  loadTokenUsage: () => Promise<void>;
  loadProfile: () => Promise<void>;
  loadUsers: () => Promise<void>;

  // Init
  init: () => Promise<void>;
}

function saveProfileSetting(key: string, value: string): Promise<void> {
  return fetch('/api/user/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
    body: JSON.stringify({ key, value }),
  }).then(() => {}).catch(() => {});
}

export const useUserManagementStore = create<UserManagementState>()((set, get) => ({
  tokenData: null,
  tokenLoading: false,
  tokenError: '',
  profileBio: 'Full‑stack developer & AI enthusiast. Building the future of collaborative agent‑driven development.',
  profileEditingField: null,
  profileFieldDraft: '',
  profileLocation: '',
  profileEmail: '',
  profileOrg: '',
  profileAvatarUrl: '',
  profileUploading: false,
  userList: [],
  userListLoading: false,
  userListError: '',
  newUserName: '',
  newUserPassword: '',
  newUserRole: 'developer',
  creatingUser: false,

  // ── Profile setters ────────────────────────────────────────────

  setProfileEditingField: (v) => set({ profileEditingField: v }),
  setProfileFieldDraft: (v) => set({ profileFieldDraft: v }),
  setNewUserName: (v) => set({ newUserName: v }),
  setNewUserPassword: (v) => set({ newUserPassword: v }),
  setNewUserRole: (v) => set({ newUserRole: v }),

  // ── Profile actions ─────────────────────────────────────────────

  handleStartEditField: (field, currentValue) => {
    set({ profileEditingField: field, profileFieldDraft: currentValue });
  },

  handleSaveField: async () => {
    const { profileEditingField, profileFieldDraft } = get();
    if (!profileEditingField) return;
    const field = profileEditingField;
    const value = profileFieldDraft;
    switch (field) {
      case 'bio': set({ profileBio: value }); break;
      case 'location': set({ profileLocation: value }); break;
      case 'email': set({ profileEmail: value }); break;
      case 'org': set({ profileOrg: value }); break;
    }
    set({ profileEditingField: null, profileFieldDraft: '' });
    await saveProfileSetting(field, value);
  },

  handleCancelEditField: () => {
    set({ profileEditingField: null, profileFieldDraft: '' });
  },

  handleUploadProfileAvatar: async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    set({ profileUploading: true });
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await fetch('/api/user/profile/avatar', {
        method: 'POST',
        headers: useAuthStore.getState().authHeaders(),
        body: formData,
      });
      if (res.ok) {
        const data = await res.json() as { avatarUrl: string };
        set({ profileAvatarUrl: data.avatarUrl || '' });
        await saveProfileSetting('avatarUrl', data.avatarUrl || '');
        useAdminStore.getState().setNotice('头像已更新');
      }
    } catch {
      useAdminStore.getState().setNotice('上传失败');
    } finally {
      set({ profileUploading: false });
      e.target.value = '';
    }
  },

  // ── User CRUD ──────────────────────────────────────────────────

  handleCreateUser: async (e) => {
    e.preventDefault();
    const { newUserName, newUserPassword, newUserRole } = get();
    set({ creatingUser: true });
    try {
      const res = await fetch('/api/user/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
        body: JSON.stringify({ name: newUserName, password: newUserPassword, role: newUserRole }),
      });
      const data = await res.json();
      const { fmtErr } = useAuthStore.getState();
      useAdminStore.getState().setNotice(
        res.ok ? `已创建用户：${newUserName}` : fmtErr(data.detail, '创建失败')
      );
      if (res.ok) {
        set({ newUserName: '', newUserPassword: '', newUserRole: 'developer' });
        await get().loadUsers();
      }
    } catch {
      useAdminStore.getState().setNotice('创建失败，请检查网络');
    } finally {
      set({ creatingUser: false });
    }
  },

  handleChangeUserRole: async (userId, newRole) => {
    try {
      const res = await fetch(`/api/user/${encodeURIComponent(userId)}/role`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
        body: JSON.stringify({ role: newRole }),
      });
      if (res.ok) {
        useAdminStore.getState().setNotice('角色已更新');
        await get().loadUsers();
      }
    } catch {
      useAdminStore.getState().setNotice('更新失败');
    }
  },

  handleDeleteUser: async (userId, userName) => {
    if (typeof window !== 'undefined' && !window.confirm(
      `确认删除用户 ${userName}？\n该用户的所有数据将被永久删除且不可恢复。`
    )) return;
    try {
      const res = await fetch(`/api/user/${encodeURIComponent(userId)}`, {
        method: 'DELETE',
        headers: useAuthStore.getState().authHeaders(),
      });
      if (res.ok) {
        useAdminStore.getState().setNotice(`已删除用户：${userName}`);
        await get().loadUsers();
      }
    } catch {
      useAdminStore.getState().setNotice('删除失败');
    }
  },

  // ── Data loaders ────────────────────────────────────────────────

  loadUsers: async () => {
    set({ userListLoading: true, userListError: '' });
    try {
      const res = await fetch('/api/user/list', { headers: useAuthStore.getState().authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      set({ userList: data.users || [] });
    } catch (e) {
      set({ userListError: e instanceof Error ? e.message : 'Failed to load users' });
    } finally {
      set({ userListLoading: false });
    }
  },

  loadTokenUsage: async () => {
    set({ tokenLoading: true, tokenError: '' });
    try {
      const token = useAuthStore.getState().token || (typeof window !== 'undefined' ? localStorage.getItem('agenthub_token') || '' : '');
      const res = await fetch('/api/admin/analytics/token-usage', {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data && typeof data === 'object' && Array.isArray(data.days)) {
        set({ tokenData: data });
      }
    } catch (e) {
      set({ tokenError: e instanceof Error ? e.message : 'Failed to load token data' });
    } finally {
      set({ tokenLoading: false });
    }
  },

  loadProfile: async () => {
    try {
      const res = await fetch('/api/user/settings', { headers: useAuthStore.getState().authHeaders() });
      if (res.ok) {
        const data = await res.json() as { settings?: Record<string, string> };
        if (data?.settings) {
          const s = data.settings;
          set({
            ...(s.bio !== undefined ? { profileBio: s.bio } : {}),
            ...(s.location !== undefined ? { profileLocation: s.location } : {}),
            ...(s.email !== undefined ? { profileEmail: s.email } : {}),
            ...(s.org !== undefined ? { profileOrg: s.org } : {}),
            ...(s.avatarUrl !== undefined ? { profileAvatarUrl: s.avatarUrl } : {}),
          });
        }
      }
    } catch { /* use defaults */ }
  },

  // ── Init ───────────────────────────────────────────────────────

  init: async () => {
    await Promise.all([
      get().loadUsers(),
      get().loadTokenUsage(),
      get().loadProfile(),
    ]);
  },
}));
