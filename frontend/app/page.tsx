'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react';
import dynamic from 'next/dynamic';
import AuthForm from '../components/chat/AuthForm';
import ChatHeader from '../components/chat/ChatHeader';
import ChatInput from '../components/chat/ChatInput';

import UserRoster from '../components/collaboration/UserRoster';
import TypingIndicator from '../components/collaboration/TypingIndicator';
import ShareDialog from '../components/collaboration/ShareDialog';
import OneClickDeployModal from '../components/chat/OneClickDeployModal';
// FIX: MessageList SSR causes hydration mismatch — disable SSR
const MessageList = dynamic(() => import('../components/chat/MessageList'), {
  ssr: false,
  loading: () => null,
});
import { type ExecPermission } from '../components/chat/PermissionModePopover';
import WorkspaceSidebar from '../components/chat/WorkspaceSidebar';
import AgentCollaborationPanel from '../components/chat/AgentCollaborationPanel';
import PreviewSidebar from '../components/shared/PreviewSidebar';
import ConfirmDialog from '../components/ui/ConfirmDialog';
import ResizableDivider from '../components/common/ResizableDivider';
import { useResizableSize } from '../hooks/useResizableSize';
import { useAuthPanel } from '../hooks/useAuthPanel';
import { useFileUpload } from '../hooks/useFileUpload';
import { useSessionRecovery } from '../hooks/useSessionRecovery';
import { useSessionWebSocket } from '../hooks/useSessionWebSocket';
import { useMissionChat } from '../hooks/useMissionChat';
import { useMessageAutoScroll } from '../hooks/useMessageAutoScroll';
import { AGENTS, FALLBACK_AGENTS, sortSessions } from '../lib/agents';
import {
  detectMentionTrigger,
  filterAgentsForMention,
  filterSkillsForMention,
  filterWorkflowsForMention,
  isObserverRestrictedSession,
} from '../lib/mention';
import { authHeaders, fetchAuth as fetchAuthWithCallback } from '../lib/api';
import { agentsFromMembers, fetchWorkspaceMembers } from '../lib/workspaceMembers';
import { buildOutgoingMessageDraft } from '../lib/outgoingMessageDraft';
import { clearDagSession, useDagState } from '../lib/dagStore';
import { handleSharedWebSocketEvent } from '../lib/websocketSharedEvents';
import { flushAllPendingStreamBuffer, handleStreamWebSocketEvent } from '../lib/websocketStreamEvents';
import {
  useSessionMessages,
  useSessionStreaming,
  updateSessionMessages,
  setSessionStreaming,
  getSessionBuffer,
  replaceSessionMessages,
  clearSession,
} from '../lib/sessionStore';
import type { Agent, AttachedFile, ChatSession, FileReference, GeneratedData, Message, PendingMessage, QuoteReference, SkillMeta, User, WorkflowSummary, WorkspacePreviewTab } from '../types';
import type { FilePreviewTarget } from '../components/chat/FilePreviewModal';
import { ToastProvider, useAddToast } from '../components/ui/Toast';

// ── Dynamic imports for heavy / conditionally-rendered components ──
const FilePreviewModal = dynamic(() => import('../components/chat/FilePreviewModal'), {
  ssr: false,
  loading: () => null,
});
const FilePreviewPanel = dynamic(() => import('../components/chat/FilePreviewPanel'), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-warm-300 border-t-primary-500" />
        <span className="text-sm text-warm-400">加载预览面板...</span>
      </div>
    </div>
  ),
});
const DagModal = dynamic(() => import('../components/chat/DagModal'), {
  ssr: false,
  loading: () => null,
});

export default function AgentHubIM(): JSX.Element {
  // ── 当前 session 的 messages / isStreaming 从 SessionStore 派生 ────────────
  // 这样切到别的 session 时，旧 session 的 messages 和流状态会保留在 Map 里，
  // 切回来时直接命中缓存，流的 chunks 不会丢。
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionId, setSessionId] = useState<string>('');
  const messages = useSessionMessages(sessionId);
  const isStreaming = useSessionStreaming(sessionId);
  const dag = useDagState(sessionId);
  const { addToast } = useAddToast();
  const [sessionQuery, setSessionQuery] = useState<string>('');
  const [input, setInput] = useState<string>('@CodeGen Generate a FastAPI health route file, save as health_router.py');
  const [taskOpen, setTaskOpen] = useState<boolean>(false);
  const [previewOpen, setPreviewOpen] = useState<boolean>(false);
  const [previewUrl, setPreviewUrl] = useState<string>('');
  const [connected, setConnected] = useState<boolean>(false);
  const [notice, setNotice] = useState<string>('');
  const [generated, setGenerated] = useState<GeneratedData | null>(null);
  const [agents, setAgents] = useState<Agent[]>(FALLBACK_AGENTS);
  const [mentionSearch, setMentionSearch] = useState<string>('');
  const [confirmDelete, setConfirmDelete] = useState<{ id: string; name: string } | null>(null);
  const [deleting, setDeleting] = useState<boolean>(false);
  const [selectedRiskLevel, setSelectedRiskLevel] = useState<string>('all');
  const [mentionOpen, setMentionOpen] = useState<boolean>(false);
  const [mentionActiveIndex, setMentionActiveIndex] = useState<number>(0);
  const [mentionTrigger, setMentionTrigger] = useState<'@' | '#' | '/'>( '@');
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [skills, setSkills] = useState<SkillMeta[]>([]);
  const [editingId, setEditingId] = useState<string>('');
  const [editName, setEditName] = useState<string>('');
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const [quoteReferences, setQuoteReferences] = useState<QuoteReference[]>([]);
  const [pmState, setPmState] = useState<import('../types').PMState>('IDLE');
  const [degradationStatus, setDegradationStatus] = useState<import('../types').DegradationStatus | null>(null);
  // ── Streaming UX state ────────────────────────────────────────
  // Phase-based progress: tracks the current stage of the agent pipeline
  const [streamPhase, setStreamPhase] = useState<'idle' | 'thinking' | 'executing' | 'generating' | 'done'>('idle');
  // Currently executing tool names (shown in ChatHeader)
  const [activeTools, setActiveTools] = useState<string[]>([]);
  // WebSocket send state: idle → sending → sent
  const [sendState, setSendState] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle');
  // Current agent name extracted from the message content
  const [currentAgentName, setCurrentAgentName] = useState<string>('');
  // ── Share dialog state ─────────────────────────────────────────
  const [shareOpen, setShareOpen] = useState(false);
  const [sessionVisibility, setSessionVisibility] = useState<string>('');
  // ── One-click deploy dialog state ───────────────────────────────
  const [deployOpen, setDeployOpen] = useState(false);
  // 附件卡片的全屏预览弹窗 (点击眼睛按钮触发)
  const [previewFile, setPreviewFile] = useState<FilePreviewTarget | null>(null);
  const [isAutoNaming, setIsAutoNaming] = useState<boolean>(false);
  const [previewTabs, setPreviewTabs] = useState<WorkspacePreviewTab[]>([]);
  const [activePreviewTabId, setActivePreviewTabId] = useState<string | null>(null);
  // Workspace change counter — incremented on each file write/delete to
  // trigger auto-refresh in the file tree panel without full page reload.
  const [workspaceVersion, setWorkspaceVersion] = useState(0);
  const [fileReferences, setFileReferences] = useState<FileReference[]>([]);
  const [previewPanelOpen, setPreviewPanelOpen] = useState<boolean>(false);
  const [agentPanelCollapsed, setAgentPanelCollapsed] = useState<boolean>(false);
  /**
   * 触发预览面板跳转到某条引用对应的行号。
   * 写一个递增 counter，让 FilePreviewPanel 用 effect 监听变化并执行滚动。
   */
  const [pendingScrollRef, setPendingScrollRef] = useState<{ id: string; nonce: number } | null>(null);

  // ── 执行权限模式 ─────────────────────────────────────────────────
  // 1=询问权限  2=跳过权限  3=计划模式
  const [execPermission, setExecPermission] = useState<ExecPermission>(1);
  // 自动回复模式：为 true 时，无@Agent的对话自动使用默认Agent回复
  const [autoReply, setAutoReply] = useState(true);
  const autoReplyRef = useRef(autoReply);
  autoReplyRef.current = autoReply;

  // ── 可调整布局尺寸（localStorage 持久化） ──────────────
  // 左侧会话栏宽度：默认 320px，可在 240-480 之间调整
  const [sidebarWidth, setSidebarWidth, resetSidebarWidth] = useResizableSize(
    'agenthub.layout.sidebarWidth',
    320,
    240,
    480,
  );
  // 右侧预览面板宽度：默认 540px，可在 360-960 之间调整
  const [previewWidth, setPreviewWidth, resetPreviewWidth] = useResizableSize(
    'agenthub.layout.previewWidth',
    540,
    360,
    960,
  );
  // 拖动过程中的临时值（实时预览，松手才提交）
  const [sidebarWidthLive, setSidebarWidthLive] = useState<number | null>(null);
  const [previewWidthLive, setPreviewWidthLive] = useState<number | null>(null);
  // 整个外层 flex 容器的引用（用于响应式断点计算）
  const rootRef = useRef<HTMLDivElement>(null);

  // ── per-session stream lifecycle ──────────────────────────────────
  // streamFlushRafRef 改为 per-session Map，让每个 session 独立 RAF 调度 flush。
  const wsRef = useRef<Map<string, WebSocket>>(new Map());
  const streamFlushRafRef = useRef<Map<string, number>>(new Map());
  // per-session setTimeout IDs for progressive chunk release.
  const progressiveFlushTimersRef = useRef<Map<string, number>>(new Map());
  // Track the last stream_interrupted timestamp per session.
  const streamInterruptedAtRef = useRef<Map<string, number>>(new Map());
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const messagesContainerRef = useRef<HTMLElement | null>(null);
  const currentSessionRef = useRef<string>(sessionId);
  // 初始 ''，由下方 effect 与最新 token 同步
  const tokenRef = useRef<string>('');
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const mentionStartRef = useRef<number>(-1);
  const mentionPanelRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const inputRef = useRef(input);
  inputRef.current = input;
  const attachedFilesRef = useRef(attachedFiles);
  attachedFilesRef.current = attachedFiles;
  const previewTabsRef = useRef(previewTabs);
  previewTabsRef.current = previewTabs;
  // 关键修复：始终以 ref 形式保留最新的 sessionId，
  // 让 handleSend / handleRetryMessage 等回调即使在 useCallback 闭包过期时
  // 也能拿到“此时此刻”真实的 sessionId，而不是上一次渲染的快照。
  const activeSessionIdRef = useRef<string>(sessionId);
  activeSessionIdRef.current = sessionId;
  const sessionsRef = useRef(sessions);
  sessionsRef.current = sessions;
  const blurTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  
  function handleSocketSessionClosed(sid: string): void {
    const raf = streamFlushRafRef.current.get(sid);
    if (raf != null) {
      window.cancelAnimationFrame(raf);
      streamFlushRafRef.current.delete(sid);
    }
    const timer = progressiveFlushTimersRef.current.get(sid);
    if (timer != null) {
      window.clearTimeout(timer);
      progressiveFlushTimersRef.current.delete(sid);
    }
    flushAllPendingStreamBuffer(sid, {
      streamFlushRafRef,
      progressiveFlushTimersRef,
    });
    streamInterruptedAtRef.current.delete(sid);
    if (currentSessionRef.current === sid) {
      setStreamPhase('idle');
      setActiveTools([]);
      setCurrentAgentName('');
    }
  }

  function handleSocketMessage(raw: Record<string, unknown>, evt: string | undefined, chunkSessionId: string, ws: WebSocket): void {
    void ws;
    if (handleSharedWebSocketEvent(raw, evt, chunkSessionId, {
      wsRef,
      setWorkspaceVersion,
      setPreviewTabs,
      setNotice,
      setPmState,
      setDegradationStatus,
      setSessions,
      setIsAutoNaming,
      setExecPermission,
      sortSessions,
    })) {
      return;
    }

    if (handleStreamWebSocketEvent(raw, evt, chunkSessionId, {
      streamFlushRafRef,
      progressiveFlushTimersRef,
      streamInterruptedAtRef,
      setStreamPhase,
      setActiveTools,
      setCurrentAgentName,
      setSessions,
      sortSessions,
      addToast,
      setGenerated,
      handleOpenFilePreview,
      handleOpenDiffPreview,
    })) {
      return;
    }
  }

  const {
    connectSession,
    closeSession,
    disconnectAll,
    sendOrQueue,
  } = useSessionWebSocket({
    wsRef,
    currentSessionRef,
    tokenRef,
    setConnected,
    setNotice,
    addToast,
    onSessionClosed: handleSocketSessionClosed,
    onMessage: handleSocketMessage,
  });

  // ── 登录/注册表单 + 会话凭证状态（抽出为 useAuthPanel） ──────────
  const {
    token,
    user,
    authMode,
    authForm,
    handleAuthFormChange,
    handleToggleAuthMode,
    handleAuthSubmit,
    handleTokenExpired,
    handleLogout,
  } = useAuthPanel({
    wsRef,
    disconnectAll,
    clearSession,
    clearDagSession,
    setNotice,
  });

  // ── Effects ──────────────────────────────────────────────

  useEffect(() => {
    currentSessionRef.current = sessionId;
    tokenRef.current = token;
  }, [sessionId, token]);

  useEffect(() => {
    document.documentElement.lang = 'zh-CN';
  }, []);

  const { reloadMessages } = useSessionRecovery({
    sessionId,
    token,
    messages,
    onTokenExpired: handleTokenExpired,
  });

  // ── P1/P0: Mission/v1 chat for @mention routing ────────────────
  // @mention of an Agent takes this path (POST /chat/mission → SSE)
  // instead of the legacy WebSocket.  Non-mention messages still use
  // WebSocket — gradual migration off the orchestrator.
  const {
    sendMission,
    cancel: cancelMission,
    streamState: missionStreamState,
    missionId: activeMissionId,
    events: missionEvents,
    mentions: missionMentions,
  } = useMissionChat({
    token,
    workspaceId: 'local-admin',
    sessionId,
    authHeaders,
  });

  // ── Wire Mission SSE events into session message store ───────────
  // Every time the SSE stream delivers a new mapped event, append it
  // to the current session so MessageList renders it alongside WS
  // messages.  The `missionEvents` array is append-only, so `prev`
  // always contains all prior events for the active mission.
  const prevMissionCountRef = useRef(0);
  useEffect(() => {
    const activeSessionId = activeSessionIdRef.current;
    if (!activeSessionId) return;
    const seen = prevMissionCountRef.current;
    if (missionEvents.length <= seen) return;
    const newEvents = missionEvents.slice(seen);
    prevMissionCountRef.current = missionEvents.length;
    for (const evt of newEvents) {
      const msg: Message = {
        id: `evt-${evt.missionId}-${evt.timestamp}-${evt.type}`,
        event: 'mission',
        sessionId: activeSessionId,
        sender: 'Mission',
        content: evt.content,
        type: 'text',
        timestamp: evt.timestamp || new Date().toISOString(),
      };
      updateSessionMessages(activeSessionId, (prev) => [...prev, msg]);
    }
  }, [missionEvents]);

  useEffect(() => {
    if (!token) return;
    // ── Fetch sessions ──────────────────────────────────────────────
    fetch('/api/chat/sessions', { headers: authHeaders() })
      .then((r) => {
        if (!r.ok) {
          if (r.status === 401) throw new Error('TOKEN_EXPIRED');
          throw new Error(`HTTP ${r.status}`);
        }
        return r.json();
      })
      .then((data: ChatSession[]) => {
        if (!Array.isArray(data)) return; // defensive: 401 返回的不是数组
        setSessions(sortSessions(data));
        if (!data.find((s) => s.id === sessionId) && data.length) {
          setSessionId(data[0].id);
        }
      })
      .catch((err) => {
        if ((err as Error).message === 'TOKEN_EXPIRED') {
          handleTokenExpired();
        }
      });
    // ── Fetch agents (v1 unified member roster — P1 ADR-0108 §3.3) ──
    fetchWorkspaceMembers('local-admin')
      .then((roster) => {
        const mapped = agentsFromMembers(roster.members);
        setAgents(mapped.length ? mapped : FALLBACK_AGENTS);
      })
      .catch(() => setAgents(FALLBACK_AGENTS));
    // ── Fetch workflows ─────────────────────────────────────────────
    fetch('/api/chat/workflows', { headers: authHeaders() })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: WorkflowSummary[]) => setWorkflows(data))
      .catch(() => {});
    // ── Fetch skills (versioned, authenticated v1 surface) ──────────
    fetch('/api/v1/skills', { headers: authHeaders() })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: { skills: SkillMeta[] }) => setSkills(data.skills || []))
      .catch(() => {});
  }, [token]);

  // fetchAuth: 统一 401 处理见 lib/api.ts — 组件内包装以接入 handleTokenExpired。
  async function fetchAuth(url: string, init: RequestInit = {}): Promise<Response> {
    return fetchAuthWithCallback(url, handleTokenExpired, init);
  }

  useEffect(() => {
    if (!token || !sessionId) return;
    setFileReferences([]);
    const cached = getSessionBuffer(sessionId);
    if (!cached) {
      void reloadMessages(false);
    }
    connectSession(sessionId);
  }, [token, sessionId, reloadMessages, connectSession]);

  // ── Message auto-scroll (owned by useMessageAutoScroll) ──────────
  useMessageAutoScroll(messages, sessionId, messagesContainerRef);

  // ── Mention detection ────────────────────────────────────

  function detectMention(value: string, cursor: number): void {
    // ── Observer restriction in multi-user sessions ──────────────────
    // Only allow plain text — block @mentions, #workflows, /skills
    // Use refs to avoid stale-closure issues (this function is not a
    // useCallback, but consistent with the handler guards below).
    const sid = activeSessionIdRef.current;
    const currentSession = sessionsRef.current.find((s) => s.id === sid);
    if (isObserverRestrictedSession(currentSession)) {
      setMentionOpen(false);
      setMentionActiveIndex(0);
      mentionStartRef.current = -1;
      return;
    }

    const hit = detectMentionTrigger(value, cursor);
    if (hit) {
      setMentionSearch(hit.search);
      setMentionOpen(true);
      setMentionActiveIndex(0);
      setMentionTrigger(hit.trigger);
      mentionStartRef.current = hit.pos;
      return;
    }
    setMentionOpen(false);
    setMentionActiveIndex(0);
    mentionStartRef.current = -1;
  }

  // ── Callbacks ────────────────────────────────────────────

  const handleCreateSession = useCallback(async () => {
    const name = `Untitled Session ${sessions.length + 1}`;
    let res: Response;
    try {
      res = await fetchAuth('/api/chat/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
    } catch {
      return; // fetchAuth already called handleTokenExpired on 401
    }
    const data = await res.json();
    if (!res.ok) {
      setNotice(data.detail || 'Create session failed');
      return;
    }
    const created = data as ChatSession;
    setSessions((prev) => sortSessions([created, ...prev]));
    setSessionId(created.id);
    // 新 session：清掉 Store 里的占位（其实还没建过）
    replaceSessionMessages(created.id, []);
  }, [sessions.length]);

  const handleSelectSession = useCallback((id: string) => {
    // eslint-disable-next-line no-console
    if (process.env.NODE_ENV === "development") { console.log("[agenthub] select session", { from: currentSessionRef.current, to: id }); }
    currentSessionRef.current = id;
    activeSessionIdRef.current = id;  // 立即同步给”当前活跃 session” ref，避免下一帧前就发消息
    setSessionId(id);
    setTaskOpen(false);
    setQuoteReferences([]);
  }, []);

  const handleDeleteSession = useCallback((id: string) => {
    const session = sessions.find((s) => s.id === id);
    const name = session?.name || '未命名会话';
    setConfirmDelete({ id, name });
  }, [sessions]);

  const performDeleteSession = useCallback(async () => {
    if (!confirmDelete || deleting) return;
    const { id } = confirmDelete;
    setDeleting(true);
    try {
      let res: Response;
      try {
        res = await fetchAuth(`/api/chat/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' });
      } catch {
        setDeleting(false);
        return; // fetchAuth already called handleTokenExpired on 401
      }
      const data = await res.json();
      if (!res.ok) {
        setNotice(data.detail || 'Delete failed');
        setDeleting(false);
        return;
      }
      // 关闭这个 session 的 WebSocket、清理 Store
      closeSession(id);
      clearSession(id);
      clearDagSession(id);
      setSessions((prev) => prev.filter((s) => s.id !== id));
      if (sessionId === id) {
        const next = sessions.find((s) => s.id !== id);
        const nextId = next?.id || '';
        setSessionId(nextId);
        // 用 useSessionMessages(nextId) 自动从 Store 读，命中缓存
        // 如果 nextId 不在 Store 里，sessionId 切换 effect 会 reloadMessages
      }
      const cleanedCount = Array.isArray(data.cleaned) ? data.cleaned.length : 0;
      setNotice(cleanedCount > 0 ? `已删除会话并清理 ${cleanedCount} 项记忆文件` : '已删除会话');
      setConfirmDelete(null);
    } catch (e) {
      setNotice('Delete failed: ' + String(e));
    } finally {
      setDeleting(false);
    }
  }, [closeSession, confirmDelete, deleting, sessionId, sessions]);

  const cancelDeleteSession = useCallback(() => {
    if (deleting) return;
    setConfirmDelete(null);
  }, [deleting]);

  const handleRenameSession = useCallback(async (id: string, newName?: string) => {
    const name = (newName || editName).trim();
    if (!name) {
      setEditingId('');
      return;
    }
    const res = await fetch(`/api/chat/sessions/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ name }),
    });
    const data = await res.json();
    if (!res.ok) {
      setNotice(data.detail || 'Rename failed');
      return;
    }
    setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, name } : s)));
    setEditingId('');
  }, [editName]);

  // Direct rename for ChatHeader (takes explicit name, bypasses editName state)
  const handleChatHeaderRename = useCallback(async (id: string, name: string) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    const res = await fetch(`/api/chat/sessions/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ name: trimmed }),
    });
    const data = await res.json();
    if (!res.ok) {
      setNotice(data.detail || 'Rename failed');
      return;
    }
    setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, name: trimmed } : s)));
  }, []);

  const handleTogglePin = useCallback(async (id: string, _current: number) => {
    const res = await fetch(`/api/chat/sessions/${id}/pin`, { method: 'PUT', headers: authHeaders() });
    const data = await res.json();
    if (!res.ok) {
      setNotice(data.detail || 'Pin toggle failed');
      return;
    }
    setSessions((prev) => {
      const updated = prev.map((s) => (s.id === id ? { ...s, isPinned: data.isPinned as number } : s));
      return sortSessions(updated);
    });
  }, []);

  const handleStartRename = useCallback((s: ChatSession) => {
    setEditingId(s.id);
    setEditName(s.name);
  }, []);

  const handleAutoName = useCallback(async (id?: string) => {
    const targetId = id || sessionId;
    if (!targetId) return;
    setIsAutoNaming(true);
    try {
      const res = await fetch(`/api/chat/sessions/${targetId}/auto-name`, {
        method: 'POST',
        headers: authHeaders(),
      });
      const data = await res.json();
      if (res.ok && data.status === 'success' && data.name) {
        setSessions((prev) => prev.map((s) => (s.id === targetId ? { ...s, name: data.name as string } : s)));
      }
    } catch { /* ignore */ }
    finally { setIsAutoNaming(false); }
  }, [sessionId]);

  const handleRegenerateName = useCallback((id?: string) => {
    void handleAutoName(id);
  }, [handleAutoName]);

  const handleSessionQueryChange = useCallback((q: string) => {
    setSessionQuery(q);
  }, []);

  const handleEditNameChange = useCallback((name: string) => {
    setEditName(name);
  }, []);

  const handleEditNameKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>, id: string) => {
    if (e.key === 'Enter') {
      setEditingId('');
      const name = editName.trim();
      if (!name) return;
      // Trigger rename via the async function — we inline the logic here
      // since handleRenameSession reads editName from closure (stale here).
      // Instead, use a ref-based approach: call handleRenameSession and let it
      // read editName via the async flow.
      void handleRenameSession(id);
    }
    if (e.key === 'Escape') setEditingId('');
  }, [editName, handleRenameSession]);

  const handleEditNameBlur = useCallback((id: string) => {
    void handleRenameSession(id);
  }, [handleRenameSession]);

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    const cursor = e.target.selectionStart || 0;
    setInput(value);
    detectMention(value, cursor);
  }, []);

  const handleBlur = useCallback(() => {
    if (blurTimerRef.current) clearTimeout(blurTimerRef.current);
    blurTimerRef.current = setTimeout(() => {
      blurTimerRef.current = null;
      const activeEl = document.activeElement;
      if (mentionPanelRef.current?.contains(activeEl)) return;
      setMentionOpen(false);
      setMentionActiveIndex(0);
      mentionStartRef.current = -1;
    }, 200);
  }, []);

  const filteredAgents = useMemo(
    () => filterAgentsForMention(agents, mentionSearch, selectedRiskLevel),
    [agents, mentionSearch, selectedRiskLevel],
  );

  const filteredWorkflows = useMemo(
    () => filterWorkflowsForMention(workflows, mentionSearch),
    [workflows, mentionSearch],
  );

  const filteredSkills = useMemo(
    () => filterSkillsForMention(skills, mentionSearch),
    [skills, mentionSearch],
  );

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mentionOpen) {
      const itemCount = mentionTrigger === '@' ? filteredAgents.length
        : mentionTrigger === '#' ? filteredWorkflows.length
        : filteredSkills.length;
      if (itemCount > 0) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setMentionActiveIndex((prev) => (prev + 1) % itemCount);
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setMentionActiveIndex((prev) => (prev - 1 + itemCount) % itemCount);
          return;
        }
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          if (mentionTrigger === '@') {
            handleInsertMention(filteredAgents[mentionActiveIndex].agentId);
          } else if (mentionTrigger === '#') {
            handleInsertWorkflow(filteredWorkflows[mentionActiveIndex]);
          } else {
            handleInsertSkill(filteredSkills[mentionActiveIndex]);
          }
          return;
        }
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setMentionOpen(false);
        setMentionActiveIndex(0);
        mentionStartRef.current = -1;
        return;
      }
    }
    // Ctrl+Enter (Cmd+Enter on macOS) always sends, regardless of
    // whether the mention panel is open or the shift state.
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      setMentionOpen(false);
      handleSend();
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      setMentionOpen(false);
      handleSend();
    }
  }, [mentionOpen, mentionTrigger, mentionActiveIndex, filteredAgents, filteredWorkflows, filteredSkills]);

  const handleInsertMention = useCallback((agentId: string) => {
    // ── Observer restriction ───────────────────────────────────────
    const sid = activeSessionIdRef.current;
    const currentSession = sessionsRef.current.find((s) => s.id === sid);
    if ((currentSession?.myRole || 'viewer') === 'viewer' && (currentSession?.memberCount ?? 0) > 1) return;

    setMentionOpen(false);
    setMentionSearch('');
    const mention = `@${agentId} `;
    const start = mentionStartRef.current;
    mentionStartRef.current = -1;

    if (start >= 0) {
      const ta = textareaRef.current;
      setInput((prev) => {
        const cursor = ta ? ta.selectionEnd : prev.length;
        return prev.slice(0, start) + mention + prev.slice(cursor);
      });
      const newPos = start + mention.length;
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (el) {
          el.focus();
          el.selectionStart = newPos;
          el.selectionEnd = newPos;
        }
      });
    } else {
      setInput((prev) => (prev.includes(mention.trim()) ? prev : `${mention}${prev}`));
    }
  }, []);

  const handleInsertAllMentions = useCallback(() => {
    // ── Observer restriction ───────────────────────────────────────
    const sid = activeSessionIdRef.current;
    const currentSession = sessionsRef.current.find((s) => s.id === sid);
    if ((currentSession?.myRole || 'viewer') === 'viewer' && (currentSession?.memberCount ?? 0) > 1) return;

    setMentionOpen(false);
    setMentionSearch('');
    const mentions = agents.map((a) => `@${a.agentId} `).join('');
    const start = mentionStartRef.current;
    mentionStartRef.current = -1;

    if (start >= 0) {
      const ta = textareaRef.current;
      setInput((prev) => {
        const cursor = ta ? ta.selectionEnd : prev.length;
        return prev.slice(0, start) + mentions + prev.slice(cursor);
      });
      const newPos = start + mentions.length;
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (el) {
          el.focus();
          el.selectionStart = newPos;
          el.selectionEnd = newPos;
        }
      });
    } else {
      setInput((prev) => `${mentions}${prev}`);
    }
  }, [agents]);

  const handleInsertWorkflow = useCallback((wf: WorkflowSummary) => {
    // ── Observer restriction ───────────────────────────────────────
    const sid = activeSessionIdRef.current;
    const currentSession = sessionsRef.current.find((s) => s.id === sid);
    if ((currentSession?.myRole || 'viewer') === 'viewer' && (currentSession?.memberCount ?? 0) > 1) return;

    setMentionOpen(false);
    setMentionSearch('');
    const name = `#route:${wf.name}`;
    const start = mentionStartRef.current;
    mentionStartRef.current = -1;

    if (start >= 0) {
      const ta = textareaRef.current;
      setInput((prev) => {
        const cursor = ta ? ta.selectionEnd : prev.length;
        return prev.slice(0, start) + name + ' ' + prev.slice(cursor);
      });
      const newPos = start + name.length + 1;
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (el) {
          el.focus();
          el.selectionStart = newPos;
          el.selectionEnd = newPos;
        }
      });
    } else {
      setInput((prev) => (prev.includes(name) ? prev : `${name} ${prev}`));
    }
  }, []);

  const handleInsertSkill = useCallback((skill: SkillMeta) => {
    // ── Observer restriction ───────────────────────────────────────
    const sid = activeSessionIdRef.current;
    const currentSession = sessionsRef.current.find((s) => s.id === sid);
    if ((currentSession?.myRole || 'viewer') === 'viewer' && (currentSession?.memberCount ?? 0) > 1) return;

    setMentionOpen(false);
    setMentionSearch('');
    const trigger = `/${skill.name} `;
    const start = mentionStartRef.current;
    mentionStartRef.current = -1;

    if (start >= 0) {
      const ta = textareaRef.current;
      setInput((prev) => {
        const cursor = ta ? ta.selectionEnd : prev.length;
        return prev.slice(0, start) + trigger + prev.slice(cursor);
      });
      const newPos = start + trigger.length;
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (el) {
          el.focus();
          el.selectionStart = newPos;
          el.selectionEnd = newPos;
        }
      });
    } else {
      setInput((prev) => (prev.includes(trigger.trim()) ? prev : `${trigger}${prev}`));
    }
  }, []);

  const handleMentionSearchChange = useCallback((q: string) => {
    setMentionSearch(q);
  }, []);

  const handleMentionActiveIndexChange = useCallback((idx: number) => {
    setMentionActiveIndex(idx);
  }, []);

  const handleRiskLevelChange = useCallback((level: string) => {
    setSelectedRiskLevel(level);
  }, []);

  const handleSend = useCallback((customText?: string) => {
    // ★ 关键修复：用 ref 取“此时此刻”真实的 sessionId，绕过 useCallback 闭包过期问题。
    // 表现：切换 / 新建会话后立刻发消息，sessionId 不会用上一次渲染的旧值。
    const activeSessionId = activeSessionIdRef.current;
    if (!activeSessionId) {
      setNotice('当前没有可用会话，请先选择或新建一个会话');
      return;
    }
    const currentInput = inputRef.current;
    const currentFiles = attachedFilesRef.current;
    const rawText = typeof customText === 'string'
      ? customText
      : typeof currentInput === 'string'
        ? currentInput
        : '';
    const text = rawText.trim();
    if (!text && currentFiles.length === 0) return;
    const draft = buildOutgoingMessageDraft({
      text,
      files: currentFiles,
      references: fileReferences,
    });

    if (process.env.NODE_ENV === 'development') {
      console.log('[agenthub] handleSend', { sessionId: activeSessionId, text });
    }

    const clientId = `client-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    const localMsg: Message = {
      id: clientId,
      event: 'message',
      sessionId: activeSessionId,
      content: draft.displayContent,
      sender: user?.name || 'user',
      userId: user?.id || '',
      timestamp: new Date().toISOString(),
      type: 'text',
      attachments: draft.attachments.length > 0 ? draft.attachments : undefined,
    };

    const wsMsg: PendingMessage = {
      sessionId: activeSessionId,
      content: draft.aiContent,
      sender: user?.name || 'user',
      timestamp: new Date().toISOString(),
      type: 'text',
      attachments: currentFiles,
      quoteReferences: quoteReferences.length > 0 ? quoteReferences : undefined,
      exec_permission: execPermission,
      auto_reply: autoReplyRef.current,
    };

    if (isStreaming) {
      setSessionStreaming(activeSessionId, false);
    }
    // ── Reset streaming UX state for new message ──
    setStreamPhase('idle');
    setActiveTools([]);
    setSendState('sending');

    updateSessionMessages(activeSessionId, (prev) => [...prev, localMsg]);

    // ★ 方案1: 乐观 Thinking 占位 — 发送后立即显示 "AI思考中" 消除黑洞期
    const optimisticMsgId = `optimistic-${Date.now()}`;
    // Detect which agent is being mentioned
    let detectedAgentName = 'AI';
    const mentionMatch = text.match(/@(\w+)/);
    if (mentionMatch) {
      const agent = agents.find(a => a.agentId === mentionMatch[1]);
      if (agent) {
        detectedAgentName = agent.displayName || agent.agentId;
        setCurrentAgentName(detectedAgentName);
      }
    }
    updateSessionMessages(activeSessionId, (prev) => [
      ...prev,
      {
        event: 'message',
        sessionId: activeSessionId,
        sender: detectedAgentName,
        content: '正在理解你的需求...',
        type: 'text' as const,
        timestamp: new Date().toISOString(),
        messageId: optimisticMsgId,
        isStreaming: true,
        _optimistic: true as any,
      },
    ]);

    setSessions((prev) => sortSessions(prev.map((s) => (s.id === activeSessionId ? { ...s, lastMessageAt: localMsg.timestamp } : s))));

    // ── Mission/SSE is the only route — legacy WebSocket removed
    //    P0 migration complete: every message creates a Mission and streams SSE events.
    //    sendMission rejects on 4xx/5xx — we surface the error in-place and let
    //    the user retry rather than silently falling back.
    sendMission(draft.aiContent).then((result) => {
      if (!result) {
        setSendState('error');
        setNotice('Mission create failed — check console for details');
      } else {
        setSendState('sent');
      }
    }).catch(() => {
      setSendState('error');
      setNotice('Mission stream error — retry or refresh the page');
    });
    setInput('');
    setAttachedFiles([]);
    setFileReferences([]);
    setQuoteReferences([]);
  }, [sessionId, user, isStreaming, fileReferences, quoteReferences, sendMission]);

  const handleRetryMessage = useCallback((msg: PendingMessage) => {
    // P0: Retry also goes through Mission/SSE — same route as the
    // original send, but a fresh Mission with the same objective.
    sendMission(msg.content).then((result) => {
      if (result) {
        setNotice('Message resent via Mission');
      } else {
        setNotice('Retry failed — check console for details');
      }
    }).catch(() => {
      setNotice('Retry stream error — refresh the page');
    });
  }, [sendMission]);

  const handlePreview = useCallback(async () => {
    const res = await fetch('/api/preview/local-task', { headers: authHeaders() });
    const data = await res.json();
    setPreviewUrl((data.url as string) || '');
    setPreviewOpen(true);
  }, []);

  const handleCommit = useCallback(async () => {
    if (!generated?.files?.length) return;
    const res = await fetch('/api/git/commit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        sessionId,
        message: 'Confirm commit of CodeGen generated files',
        paths: generated.files,
      }),
    });
    const data = await res.json();
    setNotice(res.ok ? `Committed: ${data.commit_hash || data.message}` : data.detail || 'Commit failed');
  }, [generated, sessionId]);

  const handleTaskClick = useCallback(() => setTaskOpen(true), []);
  const handleTaskClose = useCallback(() => setTaskOpen(false), []);
  const handlePreviewClose = useCallback(() => setPreviewOpen(false), []);

  // ── File Preview handlers ─────────────────────────────────

  const handleOpenFilePreview = useCallback((path: string, content: string, language?: string, status?: string) => {
    setPreviewPanelOpen(true);
    setPreviewTabs((prev) => {
      // Avoid duplicate tabs for the same path+kind
      const existing = prev.find((t) => t.path === path && t.kind === 'file');
      if (existing) {
        // Update existing tab content
        setActivePreviewTabId(existing.id);
        return prev.map((t) => (t.id === existing.id ? { ...t, content, state: 'ok' as const, status: status as WorkspacePreviewTab['status'] } : t));
      }
      const newTab: WorkspacePreviewTab = {
        id: `file-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        path,
        kind: 'file',
        content,
        language,
        state: 'ok',
        status: status as WorkspacePreviewTab['status'],
      };
      setActivePreviewTabId(newTab.id);
      return [...prev, newTab];
    });
  }, []);

  const handleOpenDiffPreview = useCallback((path: string, diffText: string, language?: string) => {
    setPreviewPanelOpen(true);
    setPreviewTabs((prev) => {
      const existing = prev.find((t) => t.path === path && t.kind === 'diff');
      if (existing) {
        setActivePreviewTabId(existing.id);
        return prev.map((t) => (t.id === existing.id ? { ...t, content: diffText, state: 'ok' as const } : t));
      }
      const newTab: WorkspacePreviewTab = {
        id: `diff-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        path,
        kind: 'diff',
        content: diffText,
        language,
        state: 'ok',
      };
      setActivePreviewTabId(newTab.id);
      return [...prev, newTab];
    });
  }, []);

  const handleSelectPreviewTab = useCallback((tabId: string) => {
    setActivePreviewTabId(tabId);
    if (!previewPanelOpen) setPreviewPanelOpen(true);
  }, [previewPanelOpen]);

  const handleClosePreviewTab = useCallback((tabId: string) => {
    setPreviewTabs((prev) => {
      const filtered = prev.filter((t) => t.id !== tabId);
      if (activePreviewTabId === tabId) {
        setActivePreviewTabId(filtered.length > 0 ? filtered[filtered.length - 1].id : null);
      }
      if (filtered.length === 0) {
        setPreviewPanelOpen(false);
      }
      return filtered;
    });
  }, [activePreviewTabId]);

  const handleAddReference = useCallback((ref: FileReference) => {
    setFileReferences((prev) => {
      // Avoid exact duplicates
      const exists = prev.some((r) => r.path === ref.path && r.quote === ref.quote);
      if (exists) return prev;
      return [...prev, ref];
    });
  }, []);

  const handleRemoveReference = useCallback((index: number) => {
    setFileReferences((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleClearAllReferences = useCallback(() => {
    setFileReferences([]);
  }, []);

  // Global nav navigation handler
  const handleGlobalNavigate = useCallback((target: string) => {
    switch (target) {
      case 'admin':
        window.location.href = '/admin';
        break;
      case 'canvas':
        window.location.href = '/canvas';
        break;
      case 'memory':
        window.location.href = '/admin?menu=%E8%AE%B0%E5%BF%86';
        break;
      case 'tasks':
        handleTaskClick();
        break;
    }
  }, []);

  // Toggle preview panel
  const handleTogglePreviewPanel = useCallback(() => {
    setPreviewPanelOpen((prev) => !prev);
  }, []);

  // Open workspace file in preview panel (fetched by FilePreviewPanel)
  const handleOpenWorkspaceFile = useCallback((path: string, content: string, language: string, state: string, meta?: Record<string, unknown>) => {
    setPreviewPanelOpen(true);
    setPreviewTabs((prev) => {
      const existing = prev.find((t) => t.path === path && t.kind === 'file');
      if (existing) {
        setActivePreviewTabId(existing.id);
        return prev.map((t) => (t.id === existing.id ? {
          ...t, content, language, state: state as WorkspacePreviewTab['state'],
          contentType: (meta?.contentType as string) || t.contentType,
          slideCount: (meta?.slideCount as number) ?? t.slideCount,
          imageCount: (meta?.imageCount as number) ?? t.imageCount,
          textLength: (meta?.textLength as number) ?? t.textLength,
          totalChars: (meta?.totalChars as number) ?? t.totalChars,
          truncated: (meta?.truncated as boolean) ?? t.truncated,
        } : t));
      }
      const newTab: WorkspacePreviewTab = {
        id: `ws-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        path,
        kind: 'file',
        content,
        language,
        state: state as WorkspacePreviewTab['state'],
        contentType: meta?.contentType as string | undefined,
        slideCount: meta?.slideCount as number | undefined,
        imageCount: meta?.imageCount as number | undefined,
        textLength: meta?.textLength as number | undefined,
        totalChars: meta?.totalChars as number | undefined,
        truncated: meta?.truncated as boolean | undefined,
      };
      setActivePreviewTabId(newTab.id);
      return [...prev, newTab];
    });
  }, []);

  /**
   * 点击引用芯片 → 打开（或激活）对应文件 tab，并标记要滚动到目标行号。
   * - 已在 tab 里：直接 activate + 滚动
   * - 未在 tab 里：尝试从后端 /api/files/workspace/read 拉内容
   */
  const handleJumpToReference = useCallback(
    async (ref: FileReference) => {
      setPreviewPanelOpen(true);

      // 检查是否已有这个 path 的 tab
      const existing = previewTabsRef.current.find((t) => t.path === ref.path);
      if (existing) {
        setActivePreviewTabId(existing.id);
        // 等待 activeTab 切换 + DOM 渲染完成后再滚动
        setPendingScrollRef({ id: ref.id, nonce: Date.now() });
        return;
      }

      // 没找到 tab，尝试从工作区 API 拉取
      try {
        const token = localStorage.getItem('agenthub_token') || '';
        const readParams = new URLSearchParams({ path: ref.path });
        if (sessionId) readParams.set('session_id', sessionId);
        const resp = await fetch(
          `/api/files/workspace/read?${readParams.toString()}`,
          { headers: token ? { Authorization: `Bearer ${token}` } : {} },
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const content = data?.content ?? '';
        const language = data?.language ?? '';
        const state = data?.state ?? 'ok';
        handleOpenWorkspaceFile(ref.path, content, language, state, {
          contentType: data?.contentType,
          slideCount: data?.slideCount,
          imageCount: data?.imageCount,
          textLength: data?.textLength,
          totalChars: data?.totalChars,
          truncated: data?.truncated,
        });
        setPendingScrollRef({ id: ref.id, nonce: Date.now() });
      } catch (err) {
        // 拿不到内容也至少提示一下；不让 console 全是红
        console.warn('[references] jump failed to load file:', ref.path, err);
        setNotice(`无法打开文件: ${ref.path}`);
      }
    },
    [handleOpenWorkspaceFile, setNotice, sessionId],
  );

  // ── File upload (extracted to hook) ──────────────────────
  const { handleFileChange, handlePasteFiles, handleRemoveFile } = useFileUpload({
    authHeaders, setAttachedFiles, setNotice,
  });

  // 点击附件卡片上的预览按钮: 弹出全屏预览模态
  const handleSendPMEvent = useCallback((event: Record<string, unknown>) => {
    const activeSessionId = activeSessionIdRef.current;
    if (!activeSessionId) return;
    // Post-WebSocket migration: PM events no longer flow over WS.
    // This is a silent no-op; the MessageList still passes events here
    // but we deliberately drop them until a v1 API endpoint exists.
  }, []);

  // ── 对话引用 ──────────────────────────────────────────────────────
  const handleQuoteMessage = useCallback((msg: Message, selectedText?: string) => {
    setQuoteReferences((prev) => {
      const quoteRef: QuoteReference = {
        id: `quote-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        messageId: msg.messageId || msg.id || '',
        quotedText: selectedText || msg.content,
        originalSender: msg.sender,
        originalTimestamp: msg.timestamp,
        isFullMessage: !selectedText,
      };
      // 去重：同一消息 + 相同引文片段
      const exists = prev.some(
        (r) => r.messageId === quoteRef.messageId && r.quotedText === quoteRef.quotedText
      );
      if (exists) return prev;
      return [...prev, quoteRef];
    });
  }, []);

  const handleRemoveQuoteReference = useCallback((index: number) => {
    setQuoteReferences((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleClearAllQuoteReferences = useCallback(() => {
    setQuoteReferences([]);
  }, []);

  // ── Share dialog ────────────────────────────────────────────────
  const handleOpenShare = useCallback(() => setShareOpen(true), []);
  const handleCloseShare = useCallback(() => setShareOpen(false), []);
  const handleVisibilityChange = useCallback((vis: string) => {
    setSessionVisibility(vis);
    setSessions((prev) => prev.map((s) => (s.id === sessionId ? { ...s, visibility: vis } : s)));
  }, [sessionId]);

  // ── One-click deploy dialog ─────────────────────────────────────
  const handleOpenDeploy = useCallback(() => setDeployOpen(true), []);
  const handleCloseDeploy = useCallback(() => setDeployOpen(false), []);

  // 支持两种路径：
  //   1. 内联文件（小文件 <2MB）— 内容已在 file.content 中，走 FilePreviewModal 的 inlineContent 快速路径
  //   2. 大文件（已上传）        — 通过 fileId 走 /api/files/preview/{fileId} 获取内容
  const handlePreviewFile = useCallback((file: AttachedFile) => {
    if (file.uploadStatus === 'uploading') {
      setNotice('文件正在上传中, 请稍候再试');
      return;
    }
    if (file.uploadStatus === 'error') {
      setNotice('文件上传失败, 无法预览');
      return;
    }

    // 从文件扩展名推断预览类型
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    const mdExts = new Set(['md', 'markdown', 'mdx']);
    const codeExts = new Set([
      'py', 'js', 'ts', 'jsx', 'tsx', 'java', 'go', 'rs', 'c', 'cpp',
      'h', 'hpp', 'swift', 'kt', 'rb', 'php', 'sql', 'sh', 'bash',
      'vue', 'svelte', 'astro', 'html', 'htm', 'css', 'scss', 'less',
      'json', 'yaml', 'yml', 'xml', 'toml', 'ini', 'cfg', 'conf',
    ]);
    const inlineKind: FilePreviewTarget['inlineKind'] = mdExts.has(ext)
      ? 'markdown'
      : codeExts.has(ext)
        ? 'code'
        : 'text';

    // 构建 FilePreviewTarget — 优先使用内联内容（不需要 fileId）
    const target: FilePreviewTarget = {
      name: file.name,
      size: file.size,
      category: file.category,
      type: file.type,  // MIME type (e.g. "image/png")
      fileId: file.fileId,
      inlineContent: file.content,
      inlineKind: file.category === 'image' ? 'image' : inlineKind,
    };

    setPreviewFile(target);
  }, []);

  const handleClearAllFiles = useCallback(() => {
    setAttachedFiles((prev) => {
      if (prev.length === 0) return prev;
      return [];
    });
  }, []);

  // ── Memoized values ──────────────────────────────────────

  const sessionName = useMemo(() => {
    return sessions.find((s) => s.id === sessionId)?.name || 'New Session';
  }, [sessions, sessionId]);

  // Memoize current session to avoid repeated .find() in render
  const currentSession = useMemo(() => {
    return sessions.find((s) => s.id === sessionId);
  }, [sessions, sessionId]);

  const percent = useMemo(() => {
    return dag.total ? Math.round((dag.completed / dag.total) * 100) : 0;
  }, [dag.total, dag.completed]);

  const filteredSessions = useMemo(() => {
    const q = sessionQuery.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) => s.name.toLowerCase().includes(q));
  }, [sessions, sessionQuery]);

  // ── Render ───────────────────────────────────────────────

  if (!token) {
    return (
      <AuthForm
        authMode={authMode}
        authForm={authForm}
        notice={notice}
        onSubmit={handleAuthSubmit}
        onToggleMode={handleToggleAuthMode}
        onAuthFormChange={handleAuthFormChange}
      />
    );
  }

  // ── Main workspace layout: Sidebar | Main | AgentPanel | PreviewPanel(optional) ──
  const sidebarW = sidebarWidthLive ?? sidebarWidth;
  const previewW = previewWidthLive ?? previewWidth;
  const agentPanelW = agentPanelCollapsed ? 44 : 280;
  const gridCols = previewPanelOpen
    ? `var(--sidebar-w, 320px) 8px 1fr 8px ${agentPanelW}px 8px ${previewW}px`
    : `var(--sidebar-w, 320px) 8px 1fr 8px ${agentPanelW}px`;
  return (
    <ToastProvider>
    <div className="workspace-layout" style={{ '--sidebar-w': `${sidebarW}px`, gridTemplateColumns: gridCols } as React.CSSProperties}>
      {/* ═══════════════════════════════════════════════════════
          Column 1: Unified Workspace Sidebar (GlobalNav + SessionSidebar merged)
          ════════════════════════════════════════════════════ */}
      <WorkspaceSidebar
        user={user}
        connected={connected}
        agents={agents}
        onNavigate={handleGlobalNavigate}
        onLogout={handleLogout}
        filteredSessions={filteredSessions}
        sessionId={sessionId}
        sessionQuery={sessionQuery}
        editingId={editingId}
        editName={editName}
        notice={notice}
        isAutoNaming={isAutoNaming}
        sessionsLength={sessions.length}
        onCreateSession={handleCreateSession}
        onSelectSession={handleSelectSession}
        onDeleteSession={handleDeleteSession}
        onRenameSession={handleRenameSession}
        onTogglePin={handleTogglePin}
        onStartRename={handleStartRename}
        onRegenerateName={handleRegenerateName}
        onSessionQueryChange={handleSessionQueryChange}
        onEditNameChange={handleEditNameChange}
        onEditNameKeyDown={handleEditNameKeyDown}
        onEditNameBlur={handleEditNameBlur}
        onOpenShare={handleOpenShare}
        currentRole={currentSession?.myRole}
        currentVisibility={currentSession?.visibility || sessionVisibility}
        authHeaders={authHeaders()}
        width={sidebarW}
      />

      {/* ═══════════════════════════════════════════════════════
          Column 2: Divider column — resize handle
          Tight to sidebar edge, no gap, min 240px / max 480px
          ════════════════════════════════════════════════════ */}
      <div className="workspace-divider-col">
        <ResizableDivider
          orientation="horizontal"
          size={sidebarW}
          onPreview={setSidebarWidthLive}
          onCommit={(v) => {
            setSidebarWidthLive(null);
            setSidebarWidth(v);
          }}
          min={240}
          max={480}
          defaultValue={320}
          onReset={resetSidebarWidth}
          ariaLabel="左侧会话栏宽度"
          title="拖动调整会话栏宽度 · 右键输入数值 · 双击重置"
          bubbleSide="left"
        />
      </div>

      {/* ═══════════════════════════════════════════════════════
          Column 3: Main Content + Agent Panel
          ════════════════════════════════════════════════════ */}
      <div className="workspace-main">
        {/* Header */}
        <header className="workspace-header">
          <ChatHeader
            sessionName={sessionName}
            sessionId={sessionId}
            connected={connected}
            isStreaming={isStreaming}
            isAutoNaming={isAutoNaming}
            percent={percent}
            onTaskClick={handleTaskClick}
            onRenameSession={handleChatHeaderRename}
            onRegenerateName={() => handleRegenerateName()}
            onTogglePreview={handleTogglePreviewPanel}
            previewOpen={previewPanelOpen}
            onResetLayout={() => {
              resetSidebarWidth();
              resetPreviewWidth();
              try {
                window.localStorage.removeItem('agenthub.layout.previewTreeWidth');
                window.location.reload();
              } catch {
                /* ignore */
              }
            }}
            pmState={pmState}
            degradationStatus={degradationStatus}
            streamPhase={streamPhase}
            activeTools={activeTools}
            currentAgentName={currentAgentName}
            onInterruptStream={() => {
              cancelMission();
              addToast({ type: 'info', title: '已发送中断请求', duration: 3000 });
            }}
          />
        </header>

        {/* Content area: Messages */}
        <div className="workspace-content">
          {/* Messages */}
          <MessageList
            messages={messages}
            user={user}
            generated={generated}
            onCommit={handleCommit}
            messagesContainerRef={messagesContainerRef}
            bottomRef={bottomRef}
            onQuoteMessage={handleQuoteMessage}
            onSendPMEvent={handleSendPMEvent}
            agents={agents}
            sessionId={sessionId}
            isStreaming={isStreaming}
          />
        </div>

        {/* Sticky bottom chat input */}
        <ChatInput
          input={input}
          isStreaming={isStreaming}
          attachedFiles={attachedFiles}
          mentionOpen={mentionOpen}
          mentionTrigger={mentionTrigger}
          mentionSearch={mentionSearch}
          mentionActiveIndex={mentionActiveIndex}
          selectedRiskLevel={selectedRiskLevel}
          filteredAgents={filteredAgents}
          filteredWorkflows={filteredWorkflows}
          filteredSkills={filteredSkills}
          textareaRef={textareaRef}
          mentionPanelRef={mentionPanelRef}
          fileInputRef={fileInputRef}
          onInputChange={handleInputChange}
          onBlur={handleBlur}
          onKeyDown={handleKeyDown}
          onSend={handleSend}
          onFileChange={handleFileChange}
          onRemoveFile={handleRemoveFile}
          onClearAllFiles={handleClearAllFiles}
          onPasteFiles={handlePasteFiles}
          onPreviewFile={handlePreviewFile}
          fileReferences={fileReferences}
          onRemoveReference={handleRemoveReference}
          onClearAllReferences={handleClearAllReferences}
          onJumpToReference={handleJumpToReference}
          quoteReferences={quoteReferences}
          onRemoveQuoteReference={handleRemoveQuoteReference}
          onClearAllQuoteReferences={handleClearAllQuoteReferences}
          onInsertMention={handleInsertMention}
          onInsertAllMentions={handleInsertAllMentions}
          onInsertWorkflow={handleInsertWorkflow}
          onInsertSkill={handleInsertSkill}
          onMentionSearchChange={handleMentionSearchChange}
          onMentionActiveIndexChange={handleMentionActiveIndexChange}
          onRiskLevelChange={handleRiskLevelChange}
          execPermission={execPermission}
          onExecPermissionChange={setExecPermission}
          autoReply={autoReply}
          onAutoReplyChange={setAutoReply}
          userRole={currentSession?.myRole}
          memberCount={currentSession?.memberCount}
        />
      </div>

      {/* ═══════════════════════════════════════════════════════
          Agent Collaboration Panel (right side, grid cols 4-5)
          Always visible, collapsible to 44px
          ════════════════════════════════════════════════════ */}
      <div className="agent-panel-divider-col">
        <ResizableDivider
          orientation="horizontal"
          size={agentPanelW}
          onPreview={(v) => {
            if (v < 80) setAgentPanelCollapsed(true);
            else if (agentPanelCollapsed && v >= 80) setAgentPanelCollapsed(false);
          }}
          onCommit={(v) => {
            if (v < 80) setAgentPanelCollapsed(true);
            else setAgentPanelCollapsed(false);
          }}
          onReset={() => {
            setAgentPanelCollapsed(false);
          }}
          min={44}
          max={480}
          defaultValue={280}
          ariaLabel="智能体面板宽度"
          title="拖动调整智能体面板宽度 · 右键输入数值 · 双击重置"
          bubbleSide="left"
        />
      </div>
      <AgentCollaborationPanel
        agents={agents}
        sessionId={sessionId}
        collapsed={agentPanelCollapsed}
        onToggleCollapse={() => setAgentPanelCollapsed((v) => !v)}
        onAskAgent={(agentId) => {
          setInput(`@${agentId} `);
        }}
      />

      {/* ═══════════════════════════════════════════════════════
          File Preview Panel (right side, grid cols 6-7)
          Conditional: shown when previewPanelOpen is true
          ════════════════════════════════════════════════════ */}
      {previewPanelOpen && (
        <>
          <ResizableDivider
            orientation="horizontal"
            size={previewW}
            onPreview={setPreviewWidthLive}
            onCommit={(v) => {
              setPreviewWidthLive(null);
              setPreviewWidth(v);
            }}
            min={360}
            max={960}
            defaultValue={540}
            onReset={resetPreviewWidth}
            ariaLabel="右侧预览面板宽度"
            title="拖动调整预览面板宽度 · 右键输入数值 · 双击重置"
            reversed
            bubbleSide="right"
          />
          <aside
            className="border-l flex flex-col h-full shrink-0"
            style={{
              width: `${previewW}px`,
              background: '#191C22',
              borderColor: '#2C3038',
            }}
          >
            <FilePreviewPanel
              tabs={previewTabs}
              activeTabId={activePreviewTabId}
              onSelectTab={handleSelectPreviewTab}
              onCloseTab={handleClosePreviewTab}
              onAddReference={handleAddReference}
              onOpenWorkspaceFile={handleOpenWorkspaceFile}
              sessionId={sessionId}
              references={fileReferences}
              pendingScrollRef={pendingScrollRef}
              workspaceVersion={workspaceVersion}
            />
          </aside>
        </>
      )}
    </div>

    {/* ── Task DAG Modal ──────────────────────────────────── */}
    {taskOpen && (
      <DagModal dag={dag} onClose={handleTaskClose} />
    )}

    {/* ── URL Preview Sidebar (fixed overlay) ──────────────── */}
    <PreviewSidebar open={previewOpen} onClose={handlePreviewClose} previewUrl={previewUrl} />

    {/* ── Delete Session Confirmation Dialog ──────────────── */}
    <ConfirmDialog
      open={!!confirmDelete}
      title="删除对话记录"
      message={
        confirmDelete ? (
          <span>
            确认要删除对话 <b className="text-warm-800">「{confirmDelete.name}」</b> 吗？此操作不可撤销。
          </span>
        ) : null
      }
      details={
        <div>
          <div className="mb-1 font-medium text-warm-700">⚠️ 该操作将同时清理以下内容：</div>
          <ul className="ml-4 list-disc space-y-0.5">
            <li>PostgreSQL 中该会话的所有对话消息</li>
            <li>该会话关联的任务记录</li>
            <li>.claude/memory/ 下的会话总结文件</li>
            <li>项目记忆页面中以该会话名命名的所有记忆文件</li>
            <li>记忆提取状态（cursor）</li>
          </ul>
          <div className="mt-2 text-warm-500">删除后无法恢复，请确认是否继续。</div>
        </div>
      }
      confirmText={deleting ? '删除中...' : '确认删除'}
      cancelText="取消"
      variant="danger"
      onConfirm={performDeleteSession}
      onCancel={cancelDeleteSession}
    />

    {/* ── Session Sharing Dialog ──────────────────────────── */}
    <ShareDialog
      open={shareOpen}
      sessionId={sessionId}
      sessionName={sessionName}
      userRole={currentSession?.myRole || 'viewer'}
      visibility={currentSession?.visibility || sessionVisibility}
      authHeaders={authHeaders()}
      onClose={handleCloseShare}
      onVisibilityChange={handleVisibilityChange}
    />

    {/* ── One-click Deploy Modal ──────────────────────────── */}
    <OneClickDeployModal
      open={deployOpen}
      sessionId={sessionId}
      sessionName={sessionName}
      onClose={handleCloseDeploy}
    />

    {/* ── File Attachment Preview Modal ───────────────────── */}
    <FilePreviewModal
      file={previewFile}
      onClose={() => setPreviewFile(null)}
      authToken={token}
    />

    </ToastProvider>
  );
}
