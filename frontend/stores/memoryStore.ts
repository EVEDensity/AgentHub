import { create } from 'zustand';
import type { MemoryFileInfo, MemoryDetail, MemorySearchResult, ConsolidationResult } from '../types';
import { useAuthStore } from './authStore';
import { useAdminStore } from './adminStore';

type SubTab = 'files' | 'sessions' | 'conversation' | 'consolidation';

interface TrashItem {
  trash_name: string; original_name: string; deleted_at: string;
  days_elapsed: number; days_remaining: number; expired: boolean;
}

interface MemoryState {
  // File list
  memoryLoading: boolean;
  memoryError: string;
  memoryKeyword: string;
  memoryFiles: MemoryFileInfo[];
  activeMemoryFile: string | null;
  // Detail editor
  memoryDetail: MemoryDetail | null;
  memoryBodyDraft: string;
  memoryDirty: boolean;
  memoryPreview: boolean;
  // Sub-tab
  memorySubTab: SubTab;
  // Sessions (LLM-generated summaries — legacy)
  sessionList: Array<{ session_id: string; preview: string; updated_at: string }>;
  sessionsLoading: boolean;
  activeSessionId: string | null;
  // Session Memory Store (append-only per-session conversation memory)
  sessionMemoryList: Array<{
    session_id: string; session_name: string; topic: string;
    created_at: string; updated_at: string;
    conversation_size_chars: number; turn_count: number; is_active: boolean;
  }>;
  sessionMemoryLoading: boolean;
  activeSessionMemoryId: string | null;
  sessionMemoryConversation: string;
  sessionMemoryConversationLoading: boolean;
  activeSessionSummary: string;
  globalSummary: string;
  globalSummaryLoading: boolean;
  // Consolidation
  consolidationLoading: boolean;
  consolidationResult: ConsolidationResult | null;
  consolidationError: string;
  consolidationDryRun: boolean;
  // Search
  memorySearchQuery: string;
  memorySearchResults: MemorySearchResult[] | null;
  memorySearchLoading: boolean;
  // Trash
  showTrash: boolean;
  trashItems: TrashItem[];
  trashLoading: boolean;
  showDeleteConfirm: boolean;
  pendingDeleteFile: { filename: string; name: string } | null;

  // Derived (getter)
  getFilteredMemoryFiles: () => MemoryFileInfo[];

  // Setters
  setMemoryKeyword: (v: string) => void;
  setActiveMemoryFile: (v: string | null) => void;
  setMemoryBodyDraft: (v: string) => void;
  setMemoryDirty: (v: boolean) => void;
  setMemoryPreview: (v: boolean) => void;
  setMemorySubTab: (v: SubTab) => void;
  setShowTrash: (v: boolean) => void;
  setShowDeleteConfirm: (v: boolean) => void;
  setPendingDeleteFile: (v: { filename: string; name: string } | null) => void;
  setConsolidationDryRun: (v: boolean) => void;
  setMemorySearchQuery: (v: string) => void;
  setMemorySearchResults: (v: MemorySearchResult[] | null) => void;

  // File actions
  loadMemoryFiles: () => Promise<void>;
  loadMemoryDetail: (filename: string) => Promise<void>;
  saveMemoryDetail: () => Promise<void>;
  handleExportMemory: () => void;
  handleImportMemory: (e: React.ChangeEvent<HTMLInputElement>) => void;
  confirmDeleteMemory: (filename: string, name: string) => void;
  handleDeleteMemory: () => Promise<void>;

  // Trash actions
  loadTrash: () => Promise<void>;
  handleRecoverFromTrash: (trashName: string) => Promise<void>;
  handlePurgeFromTrash: (trashName: string) => Promise<void>;

  // Session actions
  loadSessionSummaries: () => Promise<void>;
  loadSessionDetail: (sessionId: string) => Promise<void>;
  loadGlobalSummary: () => Promise<void>;
  refreshGlobalSummary: () => Promise<void>;

  // Session Memory Store actions (append-only per-session memory)
  loadSessionMemoryList: () => Promise<void>;
  loadSessionMemoryConversation: (sessionId: string) => Promise<void>;
  consolidateSessionMemory: (sessionId: string) => Promise<void>;
  createMemorySession: (sessionId: string, sessionName: string, topic: string) => Promise<void>;
  updateSessionTopic: (sessionId: string, topic: string) => Promise<void>;
  searchInSessionMemory: (sessionId: string, query: string) => Promise<{ matches: Array<{ line_number: number; snippet: string; turn_match: string }>; count: number } | undefined>;

  // Consolidation
  runConsolidation: (dryRun: boolean) => Promise<void>;

  // Search
  runMemorySearch: () => Promise<void>;

  // Init
  init: () => Promise<void>;
}

export const useMemoryStore = create<MemoryState>()((set, get) => ({
  memoryLoading: false,
  memoryError: '',
  memoryKeyword: '',
  memoryFiles: [],
  activeMemoryFile: null,
  memoryDetail: null,
  memoryBodyDraft: '',
  memoryDirty: false,
  memoryPreview: false,
  memorySubTab: 'files',
  sessionList: [],
  sessionsLoading: false,
  activeSessionId: null,
  sessionMemoryList: [],
  sessionMemoryLoading: false,
  activeSessionMemoryId: null,
  sessionMemoryConversation: '',
  sessionMemoryConversationLoading: false,
  activeSessionSummary: '',
  globalSummary: '',
  globalSummaryLoading: false,
  consolidationLoading: false,
  consolidationResult: null,
  consolidationError: '',
  consolidationDryRun: true,
  memorySearchQuery: '',
  memorySearchResults: null,
  memorySearchLoading: false,
  showTrash: false,
  trashItems: [],
  trashLoading: false,
  showDeleteConfirm: false,
  pendingDeleteFile: null,

  getFilteredMemoryFiles: () => {
    const { memoryFiles, memoryKeyword } = get();
    const kw = memoryKeyword.trim().toLowerCase();
    if (!kw) return memoryFiles;
    return memoryFiles.filter((f) =>
      f.name.toLowerCase().includes(kw) ||
      f.filename.toLowerCase().includes(kw) ||
      (f.description || '').toLowerCase().includes(kw),
    );
  },

  // Setters
  setMemoryKeyword: (v) => set({ memoryKeyword: v }),
  setActiveMemoryFile: (v) => set({ activeMemoryFile: v }),
  setMemoryBodyDraft: (v) => set({ memoryBodyDraft: v, memoryDirty: true }),
  setMemoryDirty: (v) => set({ memoryDirty: v }),
  setMemoryPreview: (v) => set({ memoryPreview: v }),
  setMemorySubTab: (v) => set({ memorySubTab: v }),
  setShowTrash: (v) => set({ showTrash: v }),
  setShowDeleteConfirm: (v) => set({ showDeleteConfirm: v }),
  setPendingDeleteFile: (v) => set({ pendingDeleteFile: v }),
  setConsolidationDryRun: (v) => set({ consolidationDryRun: v }),
  setMemorySearchQuery: (v) => set({ memorySearchQuery: v }),
  setMemorySearchResults: (v) => set({ memorySearchResults: v }),

  // ── File actions ───────────────────────────────────────────────

  loadMemoryFiles: async () => {
    set({ memoryLoading: true, memoryError: '' });
    try {
      const res = await fetch('/api/memory/files', { headers: useAuthStore.getState().authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as MemoryFileInfo[];
      set({ memoryFiles: data });
      const { activeMemoryFile } = get();
      const pick = activeMemoryFile && data.find((f) => f.filename === activeMemoryFile)
        ? activeMemoryFile
        : data[0]?.filename;
      if (pick) {
        set({ activeMemoryFile: pick });
        await get().loadMemoryDetail(pick);
      } else {
        set({ activeMemoryFile: null, memoryDetail: null, memoryBodyDraft: '', memoryDirty: false });
      }
    } catch (e: unknown) {
      set({ memoryError: e instanceof Error ? e.message : '加载记忆失败' });
    } finally {
      set({ memoryLoading: false });
    }
  },

  loadMemoryDetail: async (filename) => {
    try {
      const res = await fetch(
        `/api/memory/files/${encodeURIComponent(filename)}`,
        { headers: useAuthStore.getState().authHeaders() },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const detail = (await res.json()) as MemoryDetail;
      set({ memoryDetail: detail, memoryBodyDraft: detail.body || '', memoryDirty: false, memoryPreview: false });
    } catch (e: unknown) {
      set({ memoryError: e instanceof Error ? e.message : '读取记忆详情失败' });
    }
  },

  saveMemoryDetail: async () => {
    const { activeMemoryFile, memoryDetail, memoryBodyDraft } = get();
    if (!activeMemoryFile || !memoryDetail) return;
    try {
      const res = await fetch(
        `/api/memory/files/${encodeURIComponent(activeMemoryFile)}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
          body: JSON.stringify({
            name: memoryDetail.meta.name,
            description: memoryDetail.meta.description,
            type: memoryDetail.meta.type,
            body: memoryBodyDraft,
          }),
        },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      useAdminStore.getState().setNotice('记忆已保存');
      await get().loadMemoryFiles();
      await get().loadMemoryDetail(activeMemoryFile);
    } catch (e: unknown) {
      set({ memoryError: e instanceof Error ? e.message : '保存失败' });
    }
  },

  handleExportMemory: () => {
    const { activeMemoryFile } = get();
    if (!activeMemoryFile) return;
    const a = document.createElement('a');
    a.href = `/api/memory/files/${encodeURIComponent(activeMemoryFile)}/export`;
    a.download = activeMemoryFile;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  },

  handleImportMemory: async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    const { activeMemoryFile } = get();
    const url = activeMemoryFile
      ? `/api/memory/import?target=${encodeURIComponent(activeMemoryFile)}`
      : '/api/memory/import';
    set({ memoryError: '' });
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: useAuthStore.getState().authHeaders(),
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) {
        set({ memoryError: (data.detail as string) || '导入失败' });
        return;
      }
      if (data.mode === 'append') {
        useAdminStore.getState().setNotice(
          `已拼接到 ${activeMemoryFile}（导入 ${data.imported_chars} 字符，合计 ${data.total_chars} 字符）`
        );
      } else {
        useAdminStore.getState().setNotice(
          `记忆导入完成：成功 ${data.imported_count} 条` +
          (data.skipped_count > 0 ? `，跳过 ${data.skipped_count} 条` : '')
        );
      }
      await get().loadMemoryFiles();
      const fileToLoad = data.mode === 'append'
        ? activeMemoryFile
        : (data.imported?.[0] as string | undefined);
      if (fileToLoad) {
        set({ activeMemoryFile: fileToLoad });
        await get().loadMemoryDetail(fileToLoad);
      }
    } catch (err: unknown) {
      set({ memoryError: err instanceof Error ? err.message : '导入失败' });
    } finally {
      e.target.value = '';
    }
  },

  confirmDeleteMemory: (filename, name) => {
    set({ pendingDeleteFile: { filename, name }, showDeleteConfirm: true });
  },

  handleDeleteMemory: async () => {
    const { pendingDeleteFile } = get();
    if (!pendingDeleteFile) return;
    set({ showDeleteConfirm: false });
    try {
      const res = await fetch(
        `/api/memory/files/${encodeURIComponent(pendingDeleteFile.filename)}`,
        { method: 'DELETE', headers: useAuthStore.getState().authHeaders() },
      );
      const data = await res.json();
      const { fmtErr } = useAuthStore.getState();
      useAdminStore.getState().setNotice(
        res.ok
          ? `已删除记忆：${pendingDeleteFile.name}`
          : fmtErr((data as { detail?: string }).detail, '删除失败')
      );
      if (res.ok) await get().loadMemoryFiles();
    } catch {
      useAdminStore.getState().setNotice('删除失败，请检查网络');
    } finally {
      set({ pendingDeleteFile: null });
    }
  },

  // ── Trash actions ──────────────────────────────────────────────

  loadTrash: async () => {
    set({ trashLoading: true });
    try {
      const res = await fetch('/api/memory/trash', { headers: useAuthStore.getState().authHeaders() });
      if (res.ok) {
        const data = await res.json() as { trash: TrashItem[]; count: number; retention_days: number };
        set({ trashItems: data.trash || [] });
      }
    } catch { /* ignore */ }
    finally { set({ trashLoading: false }); }
  },

  handleRecoverFromTrash: async (trashName) => {
    try {
      const res = await fetch(
        `/api/memory/trash/recover/${encodeURIComponent(trashName)}`,
        { method: 'POST', headers: useAuthStore.getState().authHeaders() },
      );
      const data = await res.json();
      const { fmtErr } = useAuthStore.getState();
      useAdminStore.getState().setNotice(
        res.ok ? `已恢复：${trashName}` : fmtErr((data as { detail?: string }).detail, '恢复失败')
      );
      if (res.ok) {
        await get().loadMemoryFiles();
        await get().loadTrash();
      }
    } catch {
      useAdminStore.getState().setNotice('恢复失败');
    }
  },

  handlePurgeFromTrash: async (trashName) => {
    if (typeof window !== 'undefined' && !window.confirm(`确认永久删除 ${trashName}？此操作无法撤销。`)) return;
    try {
      const res = await fetch(
        `/api/memory/trash/${encodeURIComponent(trashName)}`,
        { method: 'DELETE', headers: useAuthStore.getState().authHeaders() },
      );
      const data = await res.json();
      const { fmtErr } = useAuthStore.getState();
      useAdminStore.getState().setNotice(
        res.ok ? `已永久删除：${trashName}` : fmtErr((data as { detail?: string }).detail, '删除失败')
      );
      if (res.ok) await get().loadTrash();
    } catch {
      useAdminStore.getState().setNotice('删除失败');
    }
  },

  // ── Session actions ─────────────────────────────────────────────

  loadSessionSummaries: async () => {
    set({ sessionsLoading: true });
    try {
      const res = await fetch('/api/memory/sessions', { headers: useAuthStore.getState().authHeaders() });
      if (res.ok) {
        const data = await res.json() as { sessions: Array<{ session_id: string; preview: string; updated_at: string }> };
        set({ sessionList: data.sessions || [] });
      }
    } catch { /* ignore */ }
    finally { set({ sessionsLoading: false }); }
  },

  loadSessionDetail: async (sessionId) => {
    set({ activeSessionId: sessionId });
    try {
      const res = await fetch(
        `/api/memory/sessions/${encodeURIComponent(sessionId)}`,
        { headers: useAuthStore.getState().authHeaders() },
      );
      if (res.ok) {
        const data = await res.json() as { summary: string };
        set({ activeSessionSummary: data.summary || '' });
      } else {
        set({ activeSessionSummary: '' });
      }
    } catch {
      set({ activeSessionSummary: '' });
    }
  },

  loadGlobalSummary: async () => {
    set({ globalSummaryLoading: true });
    try {
      const res = await fetch('/api/memory/sessions/global-summary', { headers: useAuthStore.getState().authHeaders() });
      if (res.ok) {
        const data = await res.json() as { summary: string };
        set({ globalSummary: data.summary || '' });
      }
    } catch { /* ignore */ }
    finally { set({ globalSummaryLoading: false }); }
  },

  refreshGlobalSummary: async () => {
    set({ globalSummaryLoading: true });
    try {
      const res = await fetch('/api/memory/sessions/global-summary', {
        method: 'POST',
        headers: useAuthStore.getState().authHeaders(),
      });
      if (res.ok) {
        const data = await res.json() as { summary: string };
        set({ globalSummary: data.summary || '' });
        useAdminStore.getState().setNotice('全局摘要已刷新');
      }
    } catch {
      useAdminStore.getState().setNotice('刷新失败');
    }
    finally { set({ globalSummaryLoading: false }); }
  },

  // ── Session Memory Store actions ────────────────────────────────

  loadSessionMemoryList: async () => {
    set({ sessionMemoryLoading: true });
    try {
      const res = await fetch('/api/memory/session-store', {
        headers: useAuthStore.getState().authHeaders(),
      });
      if (res.ok) {
        const data = await res.json() as {
          sessions: Array<{
            session_id: string; session_name: string; topic: string;
            created_at: string; updated_at: string;
            conversation_size_chars: number; turn_count: number; is_active: boolean;
          }>;
        };
        set({ sessionMemoryList: data.sessions || [] });
      }
    } catch { /* ignore */ }
    finally { set({ sessionMemoryLoading: false }); }
  },

  loadSessionMemoryConversation: async (sessionId) => {
    set({
      activeSessionMemoryId: sessionId,
      sessionMemoryConversation: '',
      sessionMemoryConversationLoading: true,
    });
    try {
      const convRes = await fetch(
        `/api/memory/session-store/${encodeURIComponent(sessionId)}/conversation`,
        { headers: useAuthStore.getState().authHeaders() },
      );
      if (convRes.ok) {
        const data = await convRes.json() as { content: string; turn_count: number };
        set({ sessionMemoryConversation: data.content || '' });
      } else if (convRes.status === 404) {
        set({ sessionMemoryConversation: '（此会话暂无对话记忆）' });
      }
    } catch {
      set({ sessionMemoryConversation: '加载失败' });
    }
    finally { set({ sessionMemoryConversationLoading: false }); }
  },

  consolidateSessionMemory: async (sessionId) => {
    try {
      const res = await fetch(
        `/api/memory/session-store/${encodeURIComponent(sessionId)}/consolidate`,
        { method: 'POST', headers: useAuthStore.getState().authHeaders() },
      );
      const data = await res.json();
      if (res.ok) {
        useAdminStore.getState().setNotice(
          data.status === 'skipped'
            ? `整合跳过：${data.message || '无需整合'}`
            : `会话 ${sessionId} 记忆已整合（${data.size_chars} 字符）`
        );
      } else {
        useAdminStore.getState().setNotice(
          useAuthStore.getState().fmtErr((data as { detail?: string }).detail, '整合失败')
        );
      }
      await get().loadSessionMemoryConversation(sessionId);
      await get().loadSessionMemoryList();
    } catch {
      useAdminStore.getState().setNotice('整合失败');
    }
  },

  createMemorySession: async (sessionId, sessionName, topic) => {
    try {
      const res = await fetch('/api/memory/session-store', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
        body: JSON.stringify({ session_id: sessionId, session_name: sessionName, topic }),
      });
      const data = await res.json();
      if (res.ok) {
        useAdminStore.getState().setNotice(`记忆会话已创建：${sessionName || sessionId}`);
        await get().loadSessionMemoryList();
      } else {
        useAdminStore.getState().setNotice(
          useAuthStore.getState().fmtErr((data as { detail?: string }).detail, '创建失败')
        );
      }
    } catch {
      useAdminStore.getState().setNotice('创建失败');
    }
  },

  updateSessionTopic: async (sessionId, topic) => {
    try {
      const res = await fetch(
        `/api/memory/session-store/${encodeURIComponent(sessionId)}/topic`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
          body: JSON.stringify({ topic }),
        },
      );
      if (res.ok) {
        useAdminStore.getState().setNotice('话题标签已更新');
        await get().loadSessionMemoryList();
      }
    } catch {
      useAdminStore.getState().setNotice('更新失败');
    }
  },

  searchInSessionMemory: async (sessionId, query) => {
    if (!query.trim()) return;
    try {
      const res = await fetch(
        `/api/memory/session-store/${encodeURIComponent(sessionId)}/search?q=${encodeURIComponent(query)}`,
        { headers: useAuthStore.getState().authHeaders() },
      );
      if (res.ok) {
        const data = await res.json() as {
          matches: Array<{ line_number: number; snippet: string; turn_match: string }>;
          count: number;
        };
        if (data.count === 0) {
          useAdminStore.getState().setNotice(`在会话中未找到 "${query}"`);
        } else {
          useAdminStore.getState().setNotice(`在会话中找到 ${data.count} 处匹配`);
        }
        return data;
      }
    } catch { /* ignore */ }
  },

  // ── Consolidation ──────────────────────────────────────────────

  runConsolidation: async (dryRun) => {
    set({ consolidationLoading: true, consolidationError: '', consolidationResult: null });
    try {
      const res = await fetch('/api/memory/consolidate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
        body: JSON.stringify({ dry_run: dryRun }),
      });
      const data = await res.json();
      if (res.ok) {
        set({ consolidationResult: data });
        if (!dryRun) await get().loadMemoryFiles();
      } else {
        set({ consolidationError: useAuthStore.getState().fmtErr(data.detail, '分析失败') });
      }
    } catch (e: unknown) {
      set({ consolidationError: e instanceof Error ? e.message : '分析失败' });
    }
    finally { set({ consolidationLoading: false }); }
  },

  // ── Search ─────────────────────────────────────────────────────

  runMemorySearch: async () => {
    const { memorySearchQuery } = get();
    if (!memorySearchQuery.trim()) return;
    set({ memorySearchLoading: true });
    try {
      const res = await fetch(
        `/api/memory/search?q=${encodeURIComponent(memorySearchQuery)}`,
        { headers: useAuthStore.getState().authHeaders() },
      );
      if (res.ok) {
        const data = await res.json() as { results: MemorySearchResult[] };
        set({ memorySearchResults: data.results || [] });
      }
    } catch {
      set({ memorySearchResults: [] });
    }
    finally { set({ memorySearchLoading: false }); }
  },

  // ── Init ───────────────────────────────────────────────────────

  init: async () => {
    await Promise.all([
      get().loadMemoryFiles(),
      get().loadSessionSummaries(),
      get().loadGlobalSummary(),
      get().loadSessionMemoryList(),
    ]);
    set({ consolidationResult: null, consolidationError: '', memorySearchResults: null, memorySearchQuery: '' });
  },
}));
