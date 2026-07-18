'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react';
import dynamic from 'next/dynamic';
import AuthForm from '../components/chat/AuthForm';
import ChatHeader from '../components/chat/ChatHeader';
import ChatInput from '../components/chat/ChatInput';

import UserRoster from '../components/collaboration/UserRoster';
import TypingIndicator from '../components/collaboration/TypingIndicator';
import { getPresenceStore } from '../lib/presenceStore';
import { getCollaborationStore } from '../lib/collaborationStore';
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
import { useFileUpload } from '../hooks/useFileUpload';
import { normalizeReferences } from '../lib/references';
import { buildChatWebSocketUrl } from '../lib/websocketUrl';
import {
  clearDagSession,
  setDagState,
  syncDagFromMessages,
  updateDagState,
  useDagState,
} from '../lib/dagStore';
import {
  useSessionMessages,
  useSessionStreaming,
  updateSessionMessages,
  setSessionStreaming,
  getSessionBuffer,
  setSessionBuffer,
  replaceSessionMessages,
  clearSession,
  pinSession,
  unpinSession,
  type StreamBuffer,
} from '../lib/sessionStore';
import type { Agent, AttachedFile, AttachmentMeta, ChatSession, FileReference, GeneratedData, Message, PendingMessage, QuoteReference, SkillMeta, StreamChunk, ToolCallEvent, ToolResultEvent, User, WorkflowSummary, WorkspacePreviewTab } from '../types';
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

const AGENTS = ['Orchestrator', 'Architect', 'CodeGen', 'Review', 'Test', 'Deploy', 'Implement'] as const;
const FALLBACK_AGENTS: Agent[] = AGENTS.map((agentId) => ({
  agentId,
  domain: agentId.toLowerCase(),
  status: 'sleeping',
  adapterType: 'mock',
  riskLevel: agentId === 'Deploy' ? 'L3' : agentId === 'CodeGen' || agentId === 'Orchestrator' ? 'L2' : 'L1',
}));

function sortSessions(items: ChatSession[]): ChatSession[] {
  return [...items].sort((a, b) => {
    const pinDiff = (b.isPinned || 0) - (a.isPinned || 0);
    if (pinDiff !== 0) return pinDiff;
    const aTime = a.lastMessageAt || a.createdAt || '';
    const bTime = b.lastMessageAt || b.createdAt || '';
    return bTime.localeCompare(aTime);
  });
}

export default function AgentHubIM(): JSX.Element {
  const [token, setToken] = useState<string>('');
  const [user, setUser] = useState<User | null>(null);
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login');
  const [authForm, setAuthForm] = useState<{ name: string; password: string }>({ name: '', password: '' });
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
  const [pending, setPending] = useState<PendingMessage[]>([]);
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

  // ── per-session WebSocket 连接 ─────────────────────────────────────
  // 切 session 时不关闭旧 session 的 WebSocket，让后台 session 的流照常累积。
  // closeWs(sid) 只在删除会话 / 登出 / 组件卸载时调用。
  const wsRef = useRef<Map<string, WebSocket>>(new Map());
  const wsReadyRef = useRef<Map<string, boolean>>(new Map());
  // ★ streamBufferRef 不再使用——buffer 走 SessionStore。
  // ★ streamFlushRafRef 改为 per-session Map，让每个 session 独立 RAF 调度 flush。
  const streamFlushRafRef = useRef<Map<string, number>>(new Map());
  // ★ progressiveFlushTimersRef: per-session setTimeout IDs for progressive
  //    chunk release (one chunk every ~8ms for a natural typing effect).
  const progressiveFlushTimersRef = useRef<Map<string, number>>(new Map());
  // ★ streamInterruptedAtRef 记录每个 session 上次 stream_interrupted 的时间戳。
  //   用于在 800ms 窗口内阻断迟到的 agent_thinking 事件重新激活流式状态
  //   (避免标题栏"AI streaming..."状态卡死)。
  const streamInterruptedAtRef = useRef<Map<string, number>>(new Map());
  const retryRef = useRef<PendingMessage[]>([]);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const lastMessageIdRef = useRef<string>('');
  const dedupIdsRef = useRef<Set<string>>(new Set());
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const messagesContainerRef = useRef<HTMLElement | null>(null);
  const currentSessionRef = useRef<string>(sessionId);
  const tokenRef = useRef<string>(token);
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
  const prevMessageCountRef = useRef(0);
  const prevSessionRef = useRef<string>(sessionId);
  // 关键修复：始终以 ref 形式保留最新的 sessionId，
  // 让 handleSend / handleRetryMessage 等回调即使在 useCallback 闭包过期时
  // 也能拿到“此时此刻”真实的 sessionId，而不是上一次渲染的快照。
  const activeSessionIdRef = useRef<string>(sessionId);
  activeSessionIdRef.current = sessionId;
  const sessionsRef = useRef(sessions);
  sessionsRef.current = sessions;
  const blurTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Effects ──────────────────────────────────────────────

  useEffect(() => {
    currentSessionRef.current = sessionId;
    tokenRef.current = token;
  }, [sessionId, token]);

  useEffect(() => {
    const saved = localStorage.getItem('agenthub_token');
    const savedUser = localStorage.getItem('agenthub_user');
    if (saved) setToken(saved);
    if (savedUser) setUser(JSON.parse(savedUser) as User);
  }, []);

  useEffect(() => {
    document.documentElement.lang = 'zh-CN';
  }, []);

  /** Centralised handler for expired / invalid auth tokens.
   *  Clears stored credentials, tears down all active connections,
   *  and returns the UI to the login screen with an explanatory notice. */
  function handleTokenExpired(): void {
    // Prevent duplicate logout cascades
    if (!localStorage.getItem('agenthub_token')) return;

    const sessionIds = Array.from(wsRef.current.keys());
    localStorage.removeItem('agenthub_token');
    localStorage.removeItem('agenthub_user');
    // Close every open WebSocket — the token is dead
    sessionIds.forEach((sid) => closeWs(sid));
    sessionIds.forEach((sid) => clearSession(sid));
    sessionIds.forEach((sid) => clearDagSession(sid));
    if (reconnectRef.current) {
      clearTimeout(reconnectRef.current);
      reconnectRef.current = null;
    }
    streamFlushRafRef.current.forEach((raf) => window.cancelAnimationFrame(raf));
    streamFlushRafRef.current.clear();
    progressiveFlushTimersRef.current.forEach((tid) => window.clearTimeout(tid));
    progressiveFlushTimersRef.current.clear();
    setToken('');
    setUser(null);
    setNotice('登录已过期，请重新登录');
  }

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
    // ── Fetch agents ───────────────────────────────────────────────
    fetch('/api/agent/registry', { headers: authHeaders() })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: Agent[]) => setAgents(data.length ? data : FALLBACK_AGENTS))
      .catch(() => setAgents(FALLBACK_AGENTS));
    // ── Fetch workflows ─────────────────────────────────────────────
    fetch('/api/chat/workflows', { headers: authHeaders() })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: WorkflowSummary[]) => setWorkflows(data))
      .catch(() => {});
    // ── Fetch skills ────────────────────────────────────────────────
    fetch('/api/skills')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: { skills: SkillMeta[] }) => setSkills(data.skills || []))
      .catch(() => {});
  }, [token]);

  async function reloadMessages(merge = false): Promise<void> {
    const sid = currentSessionRef.current;
    // Guard: never issue requests with an empty session id — the
    // resulting double-slash URL (…/sessions//messages) would 404.
    if (!sid) return;
    try {
      const res = await fetch(`/api/chat/sessions/${encodeURIComponent(sid)}/messages`, { headers: authHeaders() });
      if (!res.ok) {
        if (res.status === 401) handleTokenExpired();
        return;
      }
      const data: Message[] = (await res.json()) as Message[];
      if (merge) {
        updateSessionMessages(sid, (prev) => {
          const existingIds = new Set(prev.filter((m) => m.id).map((m) => m.id));
          const newMessages = data.filter((m) => !m.id || !existingIds.has(m.id));
          if (newMessages.length === 0) return prev;
          // Only remove actively-streaming temp messages; keep finalized ones
          // that haven't been replaced by the DB message event yet.
          const clean = prev.filter((m) => !m.isStreaming);
          return [...clean, ...newMessages].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
        });
      } else {
        // Always replace messages on a full (non-merge) reload.
        // 写到 SessionStore（per-session），切走再切回来时这条记录还在 Map 里。
        replaceSessionMessages(
          sid,
          [...data].sort((a, b) => a.timestamp.localeCompare(b.timestamp)),
        );
      }
      syncDagFromMessages(sid, data);
    } catch { /* ignore */ }
  }

  function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
    const localToken = typeof window !== 'undefined' ? localStorage.getItem('agenthub_token') : '';
    return localToken ? { ...extra, Authorization: `Bearer ${localToken}` } : extra;
  }

  /** Fetch wrapper that attaches auth headers and auto-logouts on 401.
   *  Any component-level API call SHOULD use this instead of raw fetch
   *  so stale/expired tokens are caught uniformly. */
  async function fetchAuth(url: string, init: RequestInit = {}): Promise<Response> {
    const res = await fetch(url, {
      ...init,
      headers: { ...authHeaders(), ...(init.headers as Record<string, string> || {}) },
    });
    if (res.status === 401) {
      handleTokenExpired();
      throw new Error('TOKEN_EXPIRED');
    }
    return res;
  }

  useEffect(() => {
    if (!token || !sessionId) return;
    // Reset session-scoped refs to prevent stale data leaking across sessions
    lastMessageIdRef.current = '';
    dedupIdsRef.current = new Set();
    retryRef.current = [];
    setPending([]);
    // ★ 关键修复：不要清 stream buffer，也不要重置 isStreaming。
    // 旧 session 正在流式接收的 chunks 应当继续累积到 SessionStore 里对应 session 的
    // buffer 中，等用户切回时能立即看到流式结果。buffer 走 store，不走 ref。
    setFileReferences([]);
    // Don't reset previewTabs on session switch — user may want to
    // keep viewing previously generated files across sessions.
    // ★ 关键修复：reloadMessages 改成写到 SessionStore 的 per-session Map。
    // 如果新 session 在 Store 里已经有缓存（用户切走又切回），命中缓存就不发 API。
    const cached = getSessionBuffer(sessionId);
    if (!cached) {
      void reloadMessages(false);
    }
    // 无论是否命中缓存，都要 connectWs —— 切回来时需要新 WebSocket 继续接收 chunks。
    connectWs(sessionId);
    return () => {
      // 注意：切 session 不再关闭旧 WebSocket，旧 session 仍在后台接收 chunks。
      // 旧 ws 仅在删除会话 / 登出 / 组件卸载时由 closeWs() 关闭。
      if (reconnectRef.current) {
        clearTimeout(reconnectRef.current);
        reconnectRef.current = null;
      }
    };
  }, [token, sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    syncDagFromMessages(sessionId, messages);
  }, [sessionId, messages]);

  // ── Scroll-to-bottom helper refs ──────────────────────────
  const scrollRafRef = useRef<number>(0);
  const lastScrollTimeRef = useRef<number>(0);

  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;

    const currentCount = messages.length;
    const isNewMessage = currentCount > prevMessageCountRef.current;
    const isSessionSwitch = sessionId !== prevSessionRef.current;
    prevMessageCountRef.current = currentCount;
    prevSessionRef.current = sessionId;

    // Session switch: immediate scroll to bottom
    if (isSessionSwitch) {
      if (scrollRafRef.current) cancelAnimationFrame(scrollRafRef.current);
      scrollRafRef.current = requestAnimationFrame(() => {
        container.scrollTo({ top: container.scrollHeight, behavior: 'auto' });
      });
      return;
    }

    // New message during streaming: throttle to ~30fps to avoid scroll jank
    // The progressive flush releases chunks at ~8ms intervals (125Hz) —
    // scrolling on every chunk causes layout thrashing.
    if (isNewMessage) {
      const now = performance.now();
      // Throttle: max one scroll per ~32ms (~30fps)
      if (now - lastScrollTimeRef.current < 32) return;
      lastScrollTimeRef.current = now;

      if (scrollRafRef.current) cancelAnimationFrame(scrollRafRef.current);
      scrollRafRef.current = requestAnimationFrame(() => {
        // Use auto (not smooth) during streaming — smooth animation
        // competes with DOM updates from progressive chunk release
        container.scrollTo({ top: container.scrollHeight, behavior: 'auto' });
      });
      return;
    }

    // User is near the bottom: keep them anchored
    const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    if (distanceToBottom < 120) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'auto' });
    }
  }, [messages, sessionId]);

  // ── Streaming ────────────────────────────────────────────

  /**
   * Progressive chunk release: release ONE chunk at a time, then
   * schedule the next release via setTimeout.  This ensures text
   * appears progressively even when chunks arrive faster than the
   * animation frame rate (e.g., when the LLM adapter returns the
   * entire response in one big chunk, or when chunks arrive over
   * a very fast network connection within a single RAF window).
   *
   * Each chunk is ~1-5 characters (individual SSE tokens), so
   * releasing them at ~8ms intervals gives ~125-625 chars/sec —
   * a natural "streaming typewriter" feel.
   */
  const PROGRESSIVE_FLUSH_INTERVAL_MS = 8;

  function flushStreamBuffer(sessionId: string): void {
    const buf = getSessionBuffer(sessionId);
    if (!buf) return;

    // Handle final signal even when no content chunks are pending.
    // The backend sends an empty message_chunk with isFinal=true to
    // signal end-of-stream.  Without this branch the early return
    // below would drop the final flag, leaving isStreaming stuck.
    if (buf.chunks.length === 0) {
      if (buf.isFinal) {
        setSessionStreaming(buf.sessionId, false);
        setSessionBuffer(buf.sessionId, null);
      }
      return;
    }

    // ── Release only the FIRST chunk per flush ──
    // Remaining chunks stay in the buffer and will be flushed by
    // subsequent timer/RAF calls, creating a visible typing effect.
    const chunk = buf.chunks[0];
    const remaining = buf.chunks.slice(1);
    const isLastChunk = remaining.length === 0 && buf.isFinal;

    const nextBuf: StreamBuffer = {
      messageId: buf.messageId,
      sessionId: buf.sessionId,
      chunks: remaining,
      isFinal: isLastChunk ? false : buf.isFinal,
    };
    setSessionBuffer(buf.sessionId, nextBuf);

    const bufMessageId = buf.messageId;

    updateSessionMessages(buf.sessionId, (prev) => {
      const idx = prev.findIndex((m) => m.messageId === bufMessageId);
      if (idx >= 0) {
        const updated = [...prev];
        updated[idx] = {
          ...updated[idx],
          content: updated[idx].content + chunk,
          isStreaming: !isLastChunk,
        };
        return updated;
      }
      const newMsg: Message = {
        event: 'message',
        sessionId: buf.sessionId,
        sender: 'agent',
        content: chunk,
        type: 'text',
        timestamp: new Date().toISOString(),
        messageId: bufMessageId,
        isStreaming: !isLastChunk,
      };
      return [...prev, newMsg];
    });

    if (isLastChunk) {
      setSessionStreaming(buf.sessionId, false);
      setSessionBuffer(buf.sessionId, null);
      return;
    }

    // ── Schedule next chunk release ──
    // If there are more chunks in the buffer, release the next one
    // after a short delay to maintain the progressive typing effect.
    if (remaining.length > 0) {
      const timerId = window.setTimeout(() => {
        progressiveFlushTimersRef.current.delete(sessionId);
        flushStreamBuffer(sessionId);
      }, PROGRESSIVE_FLUSH_INTERVAL_MS);
      progressiveFlushTimersRef.current.set(sessionId, timerId);
    }
  }

  // ── WebSocket ────────────────────────────────────────────

  function _reconnectDelay(): number {
    // Exponential backoff: 1s → 2s → 4s → 8s → ... → 30s max
    const attempt = reconnectAttemptsRef.current;
    const delay = Math.min(1000 * Math.pow(2, attempt), 30000);
    return delay + Math.random() * 500; // jitter to avoid thundering herd
  }

  function closeWs(sid: string): void {
    const existing = wsRef.current.get(sid);
    if (existing) {
      existing.onclose = null;
      existing.close();
      wsRef.current.delete(sid);
    }
    wsReadyRef.current.delete(sid);
    unpinSession(sid);
  }

  function connectWs(targetSid?: string): void {
    // 关键修复：必须用参数传入的 sessionId，而不是 currentSessionRef.current。
    // useEffect 顺序执行的不确定性会让 ref 在 connectWs 触发时还未更新，
    // 导致新建/切换会话时连到了旧 sid —— 表现就是 "只能发到固定会话"。
    const sid = targetSid || currentSessionRef.current;
    currentSessionRef.current = sid;

    // Guard: empty session id produces malformed ws://…/ws/ URLs that 404.
    // Also refuse to connect when the stored token is missing (already
    // logged out) — this prevents spurious reconnection attempts after
    // handleTokenExpired() fires.  Use tokenRef to avoid stale-closure
    // issues when connectWs is called from setTimeout.
    if (!sid || !tokenRef.current) return;

    // eslint-disable-next-line no-console
    if (process.env.NODE_ENV === "development") { console.log("[agenthub] connectWs", { targetSid, finalSid: sid, hasSocket: wsRef.current.has(sid) }); }
    // ★ 关键修复：如果该 session 已有 ws 但已 CLOSED/CLOSING，删掉重建。
    // 仅当 ws 是 OPEN/CONNECTING 时才跳过。
    const existing = wsRef.current.get(sid);
    if (existing) {
      if (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING) {
        return;
      }
      // 死连接：清理掉，准备重建
      try { existing.close(); } catch { /* ignore */ }
      wsRef.current.delete(sid);
      wsReadyRef.current.delete(sid);
    }
    pinSession(sid);

    if (reconnectRef.current) {
      clearTimeout(reconnectRef.current);
      reconnectRef.current = null;
    }

    const ws = new WebSocket(buildChatWebSocketUrl(sid, token));
    wsRef.current.set(sid, ws);

    ws.onopen = () => {
      if (wsRef.current.get(sid) !== ws) return; // 已被新连接替代
      wsReadyRef.current.set(sid, true);
      setConnected(true);
      reconnectAttemptsRef.current = 0;
      setNotice('WebSocket connected');

      // Replay queued messages that were pending during disconnect
      const queued = [...retryRef.current];
      retryRef.current = [];
      setPending([]);
      queued.forEach((msg) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
      });

      // Request missed messages since last known ID
      if (lastMessageIdRef.current) {
        ws.send(JSON.stringify({
          event: 'sync_request',
          lastMessageId: lastMessageIdRef.current,
        }));
      } else {
        void reloadMessages(true);
      }
    };

    ws.onclose = () => {
      wsReadyRef.current.delete(sid);
      if (wsRef.current.get(sid) === ws) {
        wsRef.current.delete(sid);
      }
      setConnected(false);
      // Flush any buffered stream content for THIS session before clearing
      const raf = streamFlushRafRef.current.get(sid);
      if (raf != null) {
        window.cancelAnimationFrame(raf);
        streamFlushRafRef.current.delete(sid);
      }
      flushStreamBuffer(sid);
      setSessionBuffer(sid, null);
      setSessionStreaming(sid, false);
      // 清理该 session 的中断标记
      streamInterruptedAtRef.current.delete(sid);
      // ★ 方案4: 重连通知
      const attempt = reconnectAttemptsRef.current;
      if (currentSessionRef.current === sid && attempt === 0) {
        addToast({ type: 'warning', title: 'WebSocket 连接断开', message: '正在尝试重新连接...', duration: 5000 });
      } else if (currentSessionRef.current === sid && attempt >= 3) {
        addToast({ type: 'error', title: '连接不稳定', message: `已尝试重连 ${attempt + 1} 次，请检查网络`, duration: 0 });
      }
      // 仅当用户当前还在这个 session 且 token 仍有效才重连
      // tokenRef 检查防止已过期登出后仍继续无效重连
      if (currentSessionRef.current === sid && tokenRef.current) {
        // Cap reconnection attempts to avoid infinite loops on
        // permanent failures (e.g. expired token, deleted session).
        if (reconnectAttemptsRef.current >= 10) {
          setNotice('连接已断开，请刷新页面或重新登录');
          addToast({ type: 'error', title: '连接彻底断开', message: '请刷新页面或重新登录', duration: 0 });
          return;
        }
        reconnectAttemptsRef.current += 1;
        if (reconnectRef.current) clearTimeout(reconnectRef.current);
        reconnectRef.current = setTimeout(connectWs, _reconnectDelay());
      }
    };

    ws.onerror = () => {
      wsReadyRef.current.delete(sid);
      setConnected(false);
      // Ensure cleanup even if browser doesn't fire onclose after error
      if (ws.readyState !== WebSocket.CLOSED && ws.readyState !== WebSocket.CLOSING) {
        try { ws.close(); } catch { /* best-effort */ }
      }
    };

    ws.onmessage = (event: MessageEvent<string>) => {
      // ★ 关键修复：移除 `currentSessionRef.current !== sid` 守卫。
      // 后台 session 的 chunks 也要处理（写入该 session 的 buffer），切回来时直接可见。
      const raw: Record<string, unknown> = JSON.parse(event.data);
      const evt = raw.event as string | undefined;
      const chunkSessionId = (raw.sessionId || sid) as string;

      // ── Heartbeat: respond to pings ──────────────────────────
      if (evt === 'ping') {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ event: 'pong', ts: Date.now() }));
        }
        return;
      }

      // ── Replayed message dedup ───────────────────────────────
      const msgId = (raw.id || raw.messageId || '') as string;
      if (raw._replay && msgId && dedupIdsRef.current.has(msgId)) {
        return; // already processed
      }
      if (msgId) {
        dedupIdsRef.current.add(msgId);
        // Keep dedup set from growing unbounded (cap at ~500)
        if (dedupIdsRef.current.size > 500) {
          const arr = [...dedupIdsRef.current];
          dedupIdsRef.current = new Set(arr.slice(arr.length - 250));
        }
      }

      // Track last known message ID for reconnection sync
      if (evt === 'message' || evt === 'message_chunk') {
        if (msgId) lastMessageIdRef.current = msgId;
      }

      // ── Workspace file change events ─────────────────────────────
      if (evt === 'workspace_change') {
        setWorkspaceVersion(v => v + 1);
        // Preview tabs: if the changed file is open in a tab, refresh its content
        const changePath = raw.path as string;
        if (changePath) {
          setPreviewTabs(prev =>
            prev.map(t => (t.path === changePath ? { ...t, _version: (t as any)._version + 1 || 1 } : t))
          );
        }
      }

      if (evt === 'file_conflict') {
        const conflictPath = raw.path as string;
        const backupPath = raw.backupPath as string;
        if (conflictPath) {
          setNotice(
            `⚠️ 文件冲突: ${conflictPath} 被其他用户修改过` +
            (backupPath ? ` (原文件已备份为 ${backupPath})` : '')
          );
        }
        // Still refresh the file tree
        setWorkspaceVersion(v => v + 1);
      }

      if (evt === 'file_lock_change') {
        // Increment version so lock indicators update in the file tree
        setWorkspaceVersion(v => v + 1);
      }

      if (evt === 'task_update') {
        // Per-node incremental update — merge into session-scoped DAG state
        const tu = raw as {
          nodeId: string;
          status: string;
          detail?: { error?: string; retries?: number };
          progress?: { completed?: number; total?: number; failed?: number; running?: number; percent?: number };
          durationMs?: number;
          retries?: number;
          sessionId: string;
        };
        if (tu.nodeId && tu.status) {
          updateDagState(chunkSessionId, {
            nodeId: tu.nodeId,
            status: tu.status,
            detail: tu.detail,
            progress: tu.progress,
            durationMs: tu.durationMs,
            retries: tu.retries,
          });
        }
      }

      if (evt === 'message_chunk') {
        const chunk = raw as unknown as StreamChunk;
        const cSessionId = chunk.sessionId || chunkSessionId;
        // ★ 中断守卫：stream_interrupted 后 800ms 内的迟到的 chunk 直接丢弃，
        //   避免旧 buffer 的残余内容被写入新会话的渲染区。
        const interruptedAt = streamInterruptedAtRef.current.get(cSessionId);
        if (interruptedAt && Date.now() - interruptedAt < 800) {
          return;
        }
        setSessionStreaming(cSessionId, !chunk.isFinal);
        // ★ 方案2: 第一个文本chunk到达 → 进入"生成回复"阶段
        setStreamPhase('generating');

        const existingBuf = getSessionBuffer(cSessionId);
        if (!existingBuf || existingBuf.messageId !== chunk.messageId) {
          // ★ 方案3: 保留最后一个 thinking 占位，更新为 "工具完成，生成回复中"
          // 而非全部删除。这样用户始终看到阶段上下文。
          updateSessionMessages(cSessionId, (prev) => {
            const lastThinkingIdx = (() => {
              for (let i = prev.length - 1; i >= 0; i--) {
                if (prev[i].isStreaming && prev[i].sender !== 'user' && !prev[i].diffFilePath && prev[i].type === 'text') {
                  return i;
                }
              }
              return -1;
            })();
            if (lastThinkingIdx >= 0) {
              const updated = [...prev];
              if (!updated[lastThinkingIdx].content || updated[lastThinkingIdx].content.startsWith('🔧')) {
                updated[lastThinkingIdx] = {
                  ...updated[lastThinkingIdx],
                  content: '工具执行完成，正在综合结果生成回复...',
                };
              }
              // Delete OLDER thinking placeholders but keep the latest one
              return updated.filter((m, i) => {
                if (i === lastThinkingIdx) return true;
                if (m.isStreaming && m.sender !== 'user' && !m.diffFilePath && m.type === 'text') {
                  return false;
                }
                return true;
              });
            }
            // Fallback: clean up all (no thinking buffer found)
            const cleaned = prev.filter(
              (m) => !(m.isStreaming && m.sender !== 'user' && !m.diffFilePath && m.type === 'text')
            );
            return cleaned.length !== prev.length ? cleaned : prev;
          });

          setSessionBuffer(cSessionId, {
            messageId: chunk.messageId,
            sessionId: cSessionId,
            chunks: [],
            isFinal: false,
          });
          // ★ 全新流开始了，清除该 session 的打断标记
          streamInterruptedAtRef.current.delete(cSessionId);
        }

        // 把 chunk 追加到对应 session 的 buffer
        const buf = getSessionBuffer(cSessionId);
        if (buf) {
          const nextChunks = chunk.content ? [...buf.chunks, chunk.content] : buf.chunks;
          setSessionBuffer(cSessionId, {
            messageId: buf.messageId,
            sessionId: buf.sessionId,
            chunks: nextChunks,
            isFinal: buf.isFinal || !!chunk.isFinal,
          });
        }

        // 调度该 session 自己的 RAF flush — only if no progressive
        // timer is already running (which would release chunks one at a time).
        if (
          !streamFlushRafRef.current.has(cSessionId) &&
          !progressiveFlushTimersRef.current.has(cSessionId)
        ) {
          const raf = window.requestAnimationFrame(() => {
            streamFlushRafRef.current.delete(cSessionId);
            flushStreamBuffer(cSessionId);
          });
          streamFlushRafRef.current.set(cSessionId, raf);
        }
      }

      if (evt === 'stream_interrupted') {
        const iSessionId = chunkSessionId;
        // ★ 方案2+3: 重置 UX 状态
        setStreamPhase('idle');
        setActiveTools([]);
        setCurrentAgentName('');
        // ★ 强制定位：清除该 session 的所有 streaming 状态、buffer、待调度 RAF
        //   以及流式 filter 状态。即便后续 stream_chunk 因为竞态到达，buffer
        //   已经被清空，flush 时不会把老内容写入新消息。
        setSessionStreaming(iSessionId, false);
        setSessionBuffer(iSessionId, null);
        const pendingRaf = streamFlushRafRef.current.get(iSessionId);
        if (pendingRaf) {
          window.cancelAnimationFrame(pendingRaf);
          streamFlushRafRef.current.delete(iSessionId);
        }
        // Clear any pending progressive flush timer
        const pendingTimer = progressiveFlushTimersRef.current.get(iSessionId);
        if (pendingTimer) {
          window.clearTimeout(pendingTimer);
          progressiveFlushTimersRef.current.delete(iSessionId);
        }
        // 记录打断时刻；800ms 内的 agent_thinking / stream_chunk 会被忽略
        streamInterruptedAtRef.current.set(iSessionId, Date.now());
        updateSessionMessages(iSessionId, (prev) => {
          const updated = [...prev];
          let changed = false;
          for (let i = updated.length - 1; i >= 0; i--) {
            if (updated[i].isStreaming) {
              // thinking placeholder（空或"正在..."进度文案）整体删除
              if (!updated[i].content || updated[i].content.startsWith('正在')) {
                updated.splice(i, 1);
              } else {
                updated[i] = {
                  ...updated[i],
                  isStreaming: false,
                  content: updated[i].content + '\n\n[Interrupted, processing new message...]',
                };
              }
              changed = true;
            }
          }
          return changed ? updated : prev;
        });
      }

      // ── Agent thinking (shows streaming indicator during tool phase) ──
      if (evt === 'agent_thinking') {
        const payload = raw as {
          messageId: string;
          agentId: string;
          phase?: string;
          details?: string;
        };
        // ★ 中断守卫：如果该 session 在 800ms 内被 stream_interrupted，
        //   迟到的 agent_thinking 事件不应该重新激活流式状态，否则标题
        //   栏"AI streaming..."会卡死。 直接 return，等下一次新会话。
        const interruptedAt = streamInterruptedAtRef.current.get(chunkSessionId);
        if (interruptedAt && Date.now() - interruptedAt < 800) {
          return;
        }
        // 过了窗口期就清掉标记，避免污染下一次正常流
        if (interruptedAt) {
          streamInterruptedAtRef.current.delete(chunkSessionId);
        }
        setSessionStreaming(chunkSessionId, true);
        // ★ 方案2: 更新阶段进度
        const phase = payload.phase || '';
        if (phase === 'analyzing' || phase === 'planning') {
          setStreamPhase('thinking');
        } else if (phase === 'executing') {
          setStreamPhase('executing');
        } else if (phase === 'synthesizing') {
          setStreamPhase('generating');
        }
        if (payload.agentId) setCurrentAgentName(payload.agentId);

        // Insert or update the thinking placeholder with phase details.
        // Subsequent agent_thinking events (e.g. "executing", "synthesizing")
        // update the same placeholder in-place so the user sees live progress.
        updateSessionMessages(chunkSessionId, (prev) => {
          // ★ 方案1: 如果有乐观占位，替换它而非新增
          const optimisticIdx = prev.findIndex(
            (m) => (m as any)._optimistic && m.isStreaming
          );
          const existingIdx = prev.findIndex(
            (m) => m.messageId === payload.messageId && m.isStreaming
          );
          if (optimisticIdx >= 0) {
            // Replace optimistic placeholder with real agent_thinking
            const updated = [...prev];
            updated[optimisticIdx] = {
              ...updated[optimisticIdx],
              messageId: payload.messageId,
              sender: payload.agentId || updated[optimisticIdx].sender,
              content: payload.details || '模型正在思考中...',
              _optimistic: undefined,
            };
            return updated;
          }
          if (existingIdx >= 0) {
            // Update existing placeholder with new phase info
            const updated = [...prev];
            updated[existingIdx] = {
              ...updated[existingIdx],
              content: payload.details || updated[existingIdx].content,
            };
            return updated;
          }
          return [
            ...prev,
            {
              event: 'message',
              sessionId: chunkSessionId,
              sender: payload.agentId || 'agent',
              content: payload.details || '',
              type: 'text' as const,
              timestamp: new Date().toISOString(),
              messageId: payload.messageId,
              isStreaming: true,
            },
          ];
        });
      }

      // ── Tool call events ──────────────────────────────────────────
      if (evt === 'tool_call') {
        const payload = raw as unknown as ToolCallEvent;
        // ★ 方案2+3: 更新阶段和活跃工具列表
        setStreamPhase('executing');
        if (payload.toolCalls && payload.toolCalls.length > 0) {
          setActiveTools(payload.toolCalls.map((c: any) => c.name || ''));
        }
        updateSessionMessages(chunkSessionId, (prev) => {
          // ★ 方案3: 保留最后一个 thinking 占位，更新为 "工具执行中"
          // 而不是全部删除。这样用户始终看到最近的上下文。
          const lastThinkingIdx = (() => {
            for (let i = prev.length - 1; i >= 0; i--) {
              if (prev[i].isStreaming && prev[i].sender !== 'user' && !prev[i].diffFilePath && prev[i].type === 'text') {
                return i;
              }
            }
            return -1;
          })();
          let cleaned = prev;
          if (lastThinkingIdx >= 0) {
            cleaned = prev.map((m, i) => {
              if (i === lastThinkingIdx) {
                return { ...m, content: '🔧 正在执行工具...' };
              }
              if (m.isStreaming && m.sender !== 'user' && !m.diffFilePath && m.type === 'text') {
                return null; // Remove older thinking placeholders
              }
              return m;
            }).filter(Boolean) as Message[];
          }
          return [
            ...cleaned,
            {
              event: 'message',
              sessionId: chunkSessionId,
              sender: 'system',
              content: '',
              type: 'tool_call' as const,
              timestamp: payload.timestamp,
              messageId: payload.messageId,
              toolCallData: { calls: payload.toolCalls },
            },
          ];
        });
      }

      if (evt === 'tool_result') {
        const payload = raw as unknown as ToolResultEvent;
        // ★ 方案3: 从活跃工具列表中移除完成的工具
        if (payload.results) {
          setActiveTools(prev => prev.filter(
            name => !payload.results.some((r: any) => r.tool_name === name)
          ));
          // ★ 方案4: 工具失败时弹出 toast 通知
          for (const result of payload.results) {
            if (!result.success) {
              addToast({
                type: 'error',
                title: `工具执行失败: ${result.tool_name}`,
                message: result.error || '未知错误',
                duration: 8000,
              });
            }
          }
        }
        updateSessionMessages(chunkSessionId, (prev) => {
          // Find the matching tool_call message and update it
          const updated = [...prev];
          for (let i = updated.length - 1; i >= 0; i--) {
            if (updated[i].type === 'tool_call' && updated[i].messageId === payload.messageId) {
              updated[i] = {
                ...updated[i],
                type: 'tool_result' as const,
                toolResultData: { results: payload.results },
              };
              break;
            }
          }
          return updated;
        });

        // ── Auto-detect file/diff content from tool results ─────
        if (payload.results) {
          for (const result of payload.results) {
            if (!result.success || !result.result) continue;
            const r = result.result as Record<string, unknown>;
            const filePath = r.path as string | undefined;
            if (!filePath) continue;

            // File creation/write → open file preview
            const content = r.content as string | undefined;
            if (content && typeof content === 'string') {
              const ext = filePath.split('.').pop()?.toLowerCase() || '';
              // Only auto-open for code/config/doc files, skip binary
              const isPreviewable = /^(py|js|ts|jsx|tsx|java|go|rs|c|cpp|h|hpp|swift|kt|rb|php|sql|sh|bash|vue|svelte|astro|html|css|scss|less|json|yaml|yml|xml|toml|ini|md|txt|cfg|conf|env|dockerfile|makefile|graphql|proto)$/i.test(ext);
              if (isPreviewable && content.length < 500000) {
                handleOpenFilePreview(filePath, content, undefined, r.status as string | undefined);
              }
            }

            // Diff result → open diff preview
            const diff = r.diff as string | undefined;
            if (diff && typeof diff === 'string' && diff.length > 0) {
              handleOpenDiffPreview(filePath, diff);
            }
          }
        }
      }

      // ── PM state & degradation events ────────────────────────────

      if (evt === 'pm_state_change') {
        const payload = raw as unknown as import('../types').PMStateChangeEvent;
        setPmState(payload.state);
      }

      if (evt === 'degradation_change') {
        const payload = raw as unknown as import('../types').DegradationEvent;
        setDegradationStatus(payload.status);
      }

      // ── Multi-user collaboration events ───────────────────────────

      if (evt === 'user_roster') {
        // Initial roster of online users when connecting
        const roster = raw as unknown as import('../types').UserRosterEvent;
        getPresenceStore().setRoster(chunkSessionId, roster.users);
      }

      if (evt === 'user_joined') {
        const joined = raw as unknown as import('../types').UserJoinedEvent;
        getPresenceStore().addUser(chunkSessionId, {
          userId: joined.userId,
          name: joined.userName,
          role: joined.role,
          status: 'online',
        });
      }

      if (evt === 'user_left') {
        const left = raw as unknown as import('../types').UserLeftEvent;
        getPresenceStore().removeUser(chunkSessionId, left.userId);
        getCollaborationStore().setTyping(chunkSessionId, left.userId, left.userName, false);
      }

      if (evt === 'presence_update') {
        const pu = raw as unknown as import('../types').PresenceUpdateEvent;
        getPresenceStore().bulkUpdateStatus(chunkSessionId, pu.users);
      }

      if (evt === 'typing_indicator') {
        const ti = raw as unknown as import('../types').TypingIndicatorEvent;
        getCollaborationStore().setTyping(chunkSessionId, ti.userId, ti.userName, ti.isTyping);
      }

      // ── PM interaction state sync ─────────────────────────────────

      if (evt === 'interaction_already_resolved') {
        const iar = raw as unknown as import('../types').InteractionAlreadyResolvedEvent;
        updateSessionMessages(chunkSessionId, (prev) => {
          const updated = [...prev];
          for (let i = updated.length - 1; i >= 0; i--) {
            const m = updated[i];
            if ((m.messageId || m.id) === iar.messageId) {
              // Update all PM interaction data types with resolvedBy
              const resolver = { resolvedBy: iar.resolvedBy, resolvedByName: iar.userName };
              if (m.questionData) {
                updated[i] = { ...m, questionData: { ...m.questionData, ...resolver } };
              } else if (m.riskWarningData) {
                updated[i] = { ...m, riskWarningData: { ...m.riskWarningData, ...resolver } };
              } else if (m.todoData) {
                updated[i] = { ...m, todoData: { ...m.todoData, ...resolver } };
              } else if (m.taskPreviewData) {
                updated[i] = { ...m, taskPreviewData: { ...m.taskPreviewData, ...resolver } };
              }
              break;
            }
          }
          return updated;
        });
      }

      if (evt === 'permission_mode_changed') {
        const pmc = raw as unknown as import('../types').PermissionModeChangedEvent;
        if (pmc.mode === 1 || pmc.mode === 2 || pmc.mode === 3) {
          setExecPermission(pmc.mode as ExecPermission);
        }
      }

      // ── PM/PMO agent interaction events ──────────────────────────

      if (evt === 'agent_question') {
        const payload = raw as unknown as import('../types').AgentQuestionEvent;
        updateSessionMessages(chunkSessionId, (prev) => [
          ...prev,
          {
            event: 'message',
            sessionId: chunkSessionId,
            sender: payload.agentId || 'PM',
            content: payload.question,
            type: 'agent_question' as const,
            timestamp: payload.timestamp,
            messageId: payload.messageId,
            questionData: payload,
          },
        ]);
      }

      if (evt === 'progress_update') {
        const payload = raw as unknown as import('../types').ProgressUpdateEvent;
        // Replace existing progress message with same messageId, or append new
        updateSessionMessages(chunkSessionId, (prev) => {
          const existingIdx = prev.findIndex(
            (m) => m.messageId === payload.messageId && m.type === 'progress_update'
          );
          if (existingIdx >= 0) {
            const updated = [...prev];
            updated[existingIdx] = {
              ...updated[existingIdx],
              content: payload.currentStep,
              progressData: payload,
            };
            return updated;
          }
          return [
            ...prev,
            {
              event: 'message',
              sessionId: chunkSessionId,
              sender: payload.agentId || 'PM',
              content: payload.currentStep,
              type: 'progress_update' as const,
              timestamp: payload.timestamp,
              messageId: payload.messageId,
              progressData: payload,
            },
          ];
        });
      }

      if (evt === 'risk_warning') {
        const payload = raw as unknown as import('../types').RiskWarningEvent;
        updateSessionMessages(chunkSessionId, (prev) => [
          ...prev,
          {
            event: 'message',
            sessionId: chunkSessionId,
            sender: payload.agentId || 'PM',
            content: payload.title + '\n' + payload.description,
            type: 'risk_warning' as const,
            timestamp: payload.timestamp,
            messageId: payload.messageId,
            riskWarningData: payload,
          },
        ]);
      }

      if (evt === 'agent_todo') {
        const payload = raw as unknown as import('../types').AgentTodoEvent;
        updateSessionMessages(chunkSessionId, (prev) => [
          ...prev,
          {
            event: 'message',
            sessionId: chunkSessionId,
            sender: payload.agentId || 'PM',
            content: payload.title + '\n' + payload.description,
            type: 'agent_todo' as const,
            timestamp: payload.timestamp,
            messageId: payload.messageId,
            todoData: payload,
          },
        ]);
      }

      if (evt === 'task_preview') {
        const payload = raw as unknown as import('../types').TaskPreviewEvent;
        // Initialize DAG state for real-time node status tracking
        setDagState(chunkSessionId, {
          total: payload.tasks.length,
          completed: 0,
          nodes: payload.tasks.map((t) => ({
            id: t.id,
            agent: t.agent,
            description: t.description,
            dependencies: t.dependencies,
            status: 'PENDING',
            estimated_effort: t.estimatedSeconds != null ? `${t.estimatedSeconds}s` : undefined,
          })),
        });
        updateSessionMessages(chunkSessionId, (prev) => [
          ...prev,
          {
            event: 'message',
            sessionId: chunkSessionId,
            sender: 'system',
            content: '任务预览',
            type: 'task_preview' as const,
            timestamp: payload.timestamp,
            messageId: payload.messageId,
            taskPreviewData: payload,
          },
        ]);
      }

      // ── Solution proposal event ──────────────────────────────────────
      if (evt === 'solution_proposal') {
        const payload = raw as unknown as import('../types').SolutionProposalEvent;
        updateSessionMessages(chunkSessionId, (prev) => [
          ...prev,
          {
            event: 'message',
            sessionId: chunkSessionId,
            sender: 'Orchestrator',
            content: `方案分析 — ${payload.solutions.length} 个方案`,
            type: 'solution_proposal' as const,
            timestamp: payload.timestamp,
            messageId: payload.messageId,
            solutionProposalData: payload,
          },
        ]);
      }

      // ── Deploy card event ──────────────────────────────────────────
      if (evt === 'deploy_card') {
        const payload = raw as unknown as import('../types').DeployCardEvent;
        updateSessionMessages(chunkSessionId, (prev) => [
          ...prev,
          {
            event: 'message',
            sessionId: chunkSessionId,
            sender: payload.agentId || 'Deploy',
            content: payload.description || '部署完成',
            type: 'deploy_card' as const,
            timestamp: payload.timestamp,
            messageId: payload.messageId,
            deployCardData: payload,
          },
        ]);
      }

      // ── CloudCode: terminal output (streaming) ────────────────────
      if (evt === 'terminal_output') {
        const payload = raw as unknown as import('../types').TerminalOutputEvent;
        updateSessionMessages(chunkSessionId, (prev) => {
          const existingIdx = prev.findIndex(
            (m) => m.messageId === payload.messageId && m.type === 'terminal'
          );
          if (existingIdx >= 0) {
            const updated = [...prev];
            updated[existingIdx] = {
              ...updated[existingIdx],
              content: updated[existingIdx].content + payload.content,
              isStreaming: true,
            };
            return updated;
          }
          return [
            ...prev,
            {
              event: 'message',
              sessionId: chunkSessionId,
              sender: payload.sender || 'system',
              content: payload.content,
              type: 'terminal' as const,
              timestamp: payload.timestamp,
              messageId: payload.messageId,
              isStreaming: true,
            },
          ];
        });
      }

      // ── CloudCode: diff update ────────────────────────────────────
      if (evt === 'diff_update') {
        const payload = raw as unknown as import('../types').DiffUpdateEvent;
        updateSessionMessages(chunkSessionId, (prev) => [
          ...prev,
          {
            event: 'message',
            sessionId: chunkSessionId,
            sender: 'system',
            content: payload.diff,
            type: 'diff' as const,
            timestamp: payload.timestamp,
            messageId: payload.messageId,
            diffFilePath: payload.path,
            diffDecisionState: 'pending' as const,
          },
        ]);
      }

      // ── Diff decision (user clicked Accept/Reject on a diff bubble) ──
      if (evt === 'diff_decision') {
        const payload = raw as unknown as import('../types').DiffDecisionEvent;
        updateSessionMessages(chunkSessionId, (prev) => {
          const updated = [...prev];
          for (let i = updated.length - 1; i >= 0; i--) {
            if (updated[i].messageId === payload.messageId && updated[i].type === 'diff') {
              updated[i] = {
                ...updated[i],
                diffDecisionState: payload.decision === 'accept' ? 'accepted' : 'rejected',
              };
              // Forward decision to server
              const targetWs = wsRef.current.get(chunkSessionId);
              if (targetWs && targetWs.readyState === WebSocket.OPEN) {
                targetWs.send(JSON.stringify({
                  event: 'diff_decision',
                  sessionId: payload.sessionId,
                  messageId: payload.messageId,
                  decision: payload.decision,
                  path: payload.path,
                }));
              }
              break;
            }
          }
          return updated;
        });
      }

      if (evt === 'session_renamed') {
        const payload = raw as { sessionId: string; name: string };
        setSessions((prev) => prev.map((s) => (s.id === payload.sessionId ? { ...s, name: payload.name } : s)));
        setIsAutoNaming(false);
      }

      if (evt === 'message') {
        // ★ 方案2: 消息完成，标记阶段为 done，2秒后恢复 idle
        setStreamPhase('done');
        setActiveTools([]);
        setTimeout(() => setStreamPhase('idle'), 2000);
        // Flush any pending stream buffer for THIS session before searching
        // for the placeholder — the RAF callback may not have fired yet.
        const cSessionId = chunkSessionId;
        const buf = getSessionBuffer(cSessionId);
        if (buf) {
          const pendingRaf = streamFlushRafRef.current.get(cSessionId);
          if (pendingRaf != null) {
            window.cancelAnimationFrame(pendingRaf);
            streamFlushRafRef.current.delete(cSessionId);
          }
          const pendingTimer = progressiveFlushTimersRef.current.get(cSessionId);
          if (pendingTimer != null) {
            window.clearTimeout(pendingTimer);
            progressiveFlushTimersRef.current.delete(cSessionId);
          }
          // Flush ALL remaining chunks at once before the final message
          if (buf.chunks.length > 0) {
            const allContent = buf.chunks.join('');
            const bufMsgId = buf.messageId;
            updateSessionMessages(cSessionId, (prev) => {
              const idx = prev.findIndex((m) => m.messageId === bufMsgId);
              if (idx >= 0) {
                const updated = [...prev];
                updated[idx] = {
                  ...updated[idx],
                  content: updated[idx].content + allContent,
                  isStreaming: false,
                };
                return updated;
              }
              return prev;
            });
          }
          setSessionBuffer(cSessionId, null);
        }
        setSessionStreaming(cSessionId, false);
        const msg = raw as unknown as Message;
        const isSystemMsg = msg.type === 'system' || msg.sender === 'system';
        updateSessionMessages(cSessionId, (prev) => {
          // Clean up ALL streaming thinking placeholders (from agent_thinking)
          // before adding/replacing the final message.  The prior narrow filter
          // (empty or "正在"-prefixed) missed the "synthesizing" phase content
          // like "工具执行完成，正在综合结果生成回复..."
          let cleaned = prev;
          const hasStaleStreamers = prev.some(
            (m) => m.isStreaming && m.sender !== 'user' && !m.diffFilePath && m.type === 'text'
          );
          if (hasStaleStreamers) {
            cleaned = prev.filter(
              (m) => !(m.isStreaming && m.sender !== 'user' && !m.diffFilePath && m.type === 'text')
            );
          }

          const targetMessageId = (raw.messageId || msg.messageId || '') as string;
          const streamingIdx = targetMessageId
            ? cleaned.findIndex((m) => m.messageId === targetMessageId)
            : -1;
          if (streamingIdx >= 0 && !isSystemMsg) {
            const updated = [...cleaned];
            updated[streamingIdx] = { ...msg, messageId: undefined, isStreaming: false };
            if (msg.id) {
              return updated.filter((m, i) => i === streamingIdx || m.id !== msg.id);
            }
            return updated;
          }
          if (isSystemMsg && msg.content && msg.content.includes('已连接')) {
            return cleaned;
          }
          if (msg.id && cleaned.some((m) => m.id === msg.id)) {
            return cleaned;
          }
          return [...cleaned, { ...msg, messageId: undefined, isStreaming: false }];
        });
        if (!isSystemMsg) {
          setSessions((prev) => sortSessions(prev.map((s) => (s.id === (msg.sessionId || cSessionId) ? { ...s, lastMessageAt: msg.timestamp || new Date().toISOString() } : s))));
        }
        if (msg.symbolic?.generated) {
          setGenerated(msg.symbolic.generated as GeneratedData);
          // Auto-open generated files in preview panel
          const gen = msg.symbolic.generated as GeneratedData;
          if (gen.fileDetails && gen.fileDetails.length > 0) {
            for (const fd of gen.fileDetails) {
              if (fd.path && fd.content && fd.content.length < 500000) {
                handleOpenFilePreview(fd.path, fd.content);
              }
            }
          }
          if (gen.diff) {
            handleOpenDiffPreview('changes.diff', gen.diff);
          }
        }
      }
    };
  }

  // ── Mention detection ────────────────────────────────────

  function detectMention(value: string, cursor: number): void {
    // ── Observer restriction in multi-user sessions ──────────────────
    // Only allow plain text — block @mentions, #workflows, /skills
    // Use refs to avoid stale-closure issues (this function is not a
    // useCallback, but consistent with the handler guards below).
    const sid = activeSessionIdRef.current;
    const currentSession = sessionsRef.current.find((s) => s.id === sid);
    const myRole = currentSession?.myRole || 'viewer';
    const memberCount = currentSession?.memberCount ?? 0;
    const isObserverInMultiUser = myRole === 'viewer' && memberCount > 1;
    if (isObserverInMultiUser) {
      setMentionOpen(false);
      setMentionActiveIndex(0);
      mentionStartRef.current = -1;
      return;
    }

    const textBefore = value.slice(0, cursor);
    const lastAt = textBefore.lastIndexOf('@');
    const lastHash = textBefore.lastIndexOf('#');
    const lastSlash = textBefore.lastIndexOf('/');

    const candidates: Array<{ pos: number; trigger: '@' | '#' | '/' }> = [];
    if (lastAt >= 0) candidates.push({ pos: lastAt, trigger: '@' });
    if (lastHash >= 0) candidates.push({ pos: lastHash, trigger: '#' });
    if (lastSlash >= 0) candidates.push({ pos: lastSlash, trigger: '/' });
    candidates.sort((a, b) => b.pos - a.pos);

    for (const c of candidates) {
      const charBefore = c.pos === 0 ? ' ' : value[c.pos - 1];
      const textAfter = textBefore.slice(c.pos + 1);
      // For / trigger, also skip if preceded by a protocol scheme (e.g. "https://")
      if (c.trigger === '/' && textBefore.slice(Math.max(0, c.pos - 7), c.pos).match(/(?:https?|ftp|file):$/)) {
        continue;
      }
      if (!textAfter.includes(' ') && !textAfter.includes('\n') &&
          (c.pos === 0 || charBefore === ' ' || charBefore === '\n')) {
        setMentionSearch(textAfter);
        setMentionOpen(true);
        setMentionActiveIndex(0);
        setMentionTrigger(c.trigger);
        mentionStartRef.current = c.pos;
        return;
      }
    }
    setMentionOpen(false);
    setMentionActiveIndex(0);
    mentionStartRef.current = -1;
  }

  // ── Callbacks ────────────────────────────────────────────

  const handleAuthFormChange = useCallback((update: Partial<{ name: string; password: string }>) => {
    setAuthForm((prev) => ({ ...prev, ...update }));
  }, []);

  const handleToggleAuthMode = useCallback(() => {
    setAuthMode((prev) => (prev === 'login' ? 'register' : 'login'));
  }, []);

  const handleAuthSubmit = useCallback((e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const submit = async () => {
      const res = await fetch(`/api/auth/${authMode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(authForm),
      });
      const data = await res.json();
      if (!res.ok) {
        setNotice(data.detail || 'Auth failed');
        return;
      }
      localStorage.setItem('agenthub_token', data.accessToken);
      localStorage.setItem('agenthub_user', JSON.stringify(data.user));
      setToken(data.accessToken as string);
      setUser(data.user as User);
      setNotice('Login success');
    };
    void submit();
  }, [authMode, authForm]);

  const handleLogout = useCallback(() => {
    const sessionIds = Array.from(wsRef.current.keys());
    localStorage.removeItem('agenthub_token');
    localStorage.removeItem('agenthub_user');
    // 关闭所有 session 的 WebSocket
    sessionIds.forEach((sid) => closeWs(sid));
    // 关闭 store 里所有的 session
    sessionIds.forEach((sid) => clearSession(sid));
    sessionIds.forEach((sid) => clearDagSession(sid));
    if (reconnectRef.current) {
      clearTimeout(reconnectRef.current);
      reconnectRef.current = null;
    }
    streamFlushRafRef.current.forEach((raf) => window.cancelAnimationFrame(raf));
    streamFlushRafRef.current.clear();
    progressiveFlushTimersRef.current.forEach((tid) => window.clearTimeout(tid));
    progressiveFlushTimersRef.current.clear();
    setToken('');
    setUser(null);
  }, []);

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
      // 关闭这个 session 的 WebSocket、清理 Store、清理重连定时器
      closeWs(id);
      if (reconnectRef.current) {
        clearTimeout(reconnectRef.current);
        reconnectRef.current = null;
      }
      const raf = streamFlushRafRef.current.get(id);
      if (raf != null) {
        window.cancelAnimationFrame(raf);
        streamFlushRafRef.current.delete(id);
      }
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
  }, [confirmDelete, deleting, sessionId, sessions]);

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

  const filteredAgents = useMemo(() => agents.filter((agent) => {
    const matchesSearch = mentionSearch === '' ||
      agent.agentId.toLowerCase().includes(mentionSearch.toLowerCase()) ||
      agent.domain.toLowerCase().includes(mentionSearch.toLowerCase());
    const matchesLevel = selectedRiskLevel === 'all' || agent.rankLevel === selectedRiskLevel;
    return matchesSearch && matchesLevel;
  }), [agents, mentionSearch, selectedRiskLevel]);

  const filteredWorkflows = useMemo(() => workflows.filter((w) => {
    if (mentionSearch === '') return true;
    const q = mentionSearch.toLowerCase();
    return (
      w.name.toLowerCase().includes(q) ||
      w.description.toLowerCase().includes(q) ||
      w.triggerKeywords.some((k) => k.toLowerCase().includes(q))
    );
  }), [workflows, mentionSearch]);

  const filteredSkills = useMemo(() => skills.filter((s) => {
    if (mentionSearch === '') return true;
    const q = mentionSearch.toLowerCase();
    return (
      s.name.toLowerCase().includes(q) ||
      (s.display_name || '').toLowerCase().includes(q) ||
      (s.description || '').toLowerCase().includes(q)
    );
  }), [skills, mentionSearch]);

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

    const fileMetas: AttachmentMeta[] = currentFiles.map((f) => ({
      name: f.name,
      size: f.size,
      type: f.type,
      category: f.category,
      fileId: f.fileId,
    }));

    let aiContent = text;
    if (currentFiles.length > 0) {
      const fileBlocks = currentFiles.map((f) => {
        const ext = f.name.split('.').pop()?.toLowerCase() || '';
        if (f.fileId && !f.content) {
          return `[Attached File: ${f.name} (fileId: ${f.fileId})]`;
        }
        return `[Attached File: ${f.name}]\n\`\`\`${ext}\n${f.content || ''}\n\`\`\``;
      }).join('\n\n');
      aiContent = text ? `${text}\n\n---\n${fileBlocks}` : fileBlocks;
    }

    // Include file references (quoted text from preview panel) in the message
    if (fileReferences.length > 0) {
      // 规范化：截断超长 quote、补全 lineEnd
      const normalized = normalizeReferences(fileReferences);
      const refBlocks = normalized.map((ref) => {
        const parts: string[] = [`[Referenced File: ${ref.path}]`];
        if (ref.lineStart) {
          const lineRange = ref.lineEnd && ref.lineEnd !== ref.lineStart
            ? `Lines ${ref.lineStart}-${ref.lineEnd}`
            : `Line ${ref.lineStart}`;
          parts.push(`(${lineRange})`);
        }
        if (ref.quote) {
          parts.push(`\n\`\`\`\n${ref.quote}\n\`\`\``);
        }
        return parts.join('');
      });
      const refContent = refBlocks.join('\n\n');
      aiContent = aiContent ? `${aiContent}\n\n---\n${refContent}` : refContent;
    }

    const displayContent = text || (currentFiles.length > 0 ? `发送了 ${currentFiles.length} 个文件` : '');

    if (process.env.NODE_ENV === 'development') {
      console.log('[agenthub] handleSend', { sessionId: activeSessionId, text });
    }

    const clientId = `client-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    const localMsg: Message = {
      id: clientId,
      event: 'message',
      sessionId: activeSessionId,
      content: displayContent,
      sender: user?.name || 'user',
      userId: user?.id || '',
      timestamp: new Date().toISOString(),
      type: 'text',
      attachments: fileMetas.length > 0 ? fileMetas : undefined,
    };

    const wsMsg: PendingMessage = {
      sessionId: activeSessionId,
      content: aiContent,
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
    const targetWs = wsRef.current.get(activeSessionId);
    if (targetWs && targetWs.readyState === WebSocket.OPEN) {
      targetWs.send(JSON.stringify(wsMsg));
      setSendState('sent');
    } else {
      // ws 还没连上：保险起见触发一次连接（如果本来就在连，就是 no-op）。
      connectWs(activeSessionId);
      retryRef.current.push(wsMsg);
      setPending((prev) => [...prev, wsMsg]);
      setNotice('Message queued for retry');
      addToast({ type: 'warning', title: '消息已排队', message: 'WebSocket 未连接，正在尝试重连后发送...', duration: 5000 });
      setSendState('error');
    }
    setInput('');
    setAttachedFiles([]);
    setFileReferences([]);
    setQuoteReferences([]);
  }, [sessionId, user, isStreaming, fileReferences, quoteReferences]);

  const handleRetryMessage = useCallback((msg: PendingMessage) => {
    const targetWs = wsRef.current.get(msg.sessionId);
    if (targetWs && targetWs.readyState === WebSocket.OPEN) {
      targetWs.send(JSON.stringify(msg));
      setPending((prev) => prev.filter((item) => item.timestamp !== msg.timestamp));
    } else {
      retryRef.current.push(msg);
      setNotice('WebSocket not connected, waiting for reconnect');
    }
  }, []);

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
  // ── PM/PMO 事件发送 ────────────────────────────────────────────────
  // Automatically injects sender / userId so bubble components don't
  // need to know the current user.
  const handleSendPMEvent = useCallback((event: Record<string, unknown>) => {
    const activeSessionId = activeSessionIdRef.current;
    if (!activeSessionId) return;
    const targetWs = wsRef.current.get(activeSessionId);
    if (targetWs && targetWs.readyState === WebSocket.OPEN) {
      targetWs.send(JSON.stringify({
        ...event,
        sender: user?.name || 'user',
        userId: user?.id || '',
      }));
    }
  }, [user]);

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
              const ws = wsRef.current.get(activeSessionIdRef.current);
              if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ event: 'interrupt_stream' }));
                addToast({ type: 'info', title: '已发送中断请求', duration: 3000 });
              }
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
