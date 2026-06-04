import React, { useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react';
import AuthForm from '../components/chat/AuthForm';
import ChatHeader from '../components/chat/ChatHeader';
import ChatInput from '../components/chat/ChatInput';
import FilePreviewModal from '../components/chat/FilePreviewModal';
import DagModal from '../components/chat/DagModal';
import MessageList from '../components/chat/MessageList';
import SessionSidebar from '../components/chat/SessionSidebar';
import PreviewSidebar from '../components/shared/PreviewSidebar';
import ConfirmDialog from '../components/ui/ConfirmDialog';
import FilePreviewPanel from '../components/chat/FilePreviewPanel';
import ResizableDivider from '../components/common/ResizableDivider';
import { useResizableSize } from '../hooks/useResizableSize';
import { normalizeReferences } from '../lib/references';
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
import type { Agent, AttachedFile, AttachmentMeta, ChatSession, DagState, FileReference, GeneratedData, Message, PendingMessage, SkillMeta, StreamChunk, ToolCallEvent, ToolResultEvent, User, WorkflowSummary, WorkspacePreviewTab } from '../types';
import type { FilePreviewTarget } from '../components/chat/FilePreviewModal';

const AGENTS = ['Orchestrator', 'Architect', 'CodeGen', 'Review', 'Test', 'Deploy'] as const;
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

function detectFileCategory(name: string, _mimeType: string): string {
  const ext = (name.split('.').pop() || '').toLowerCase();
  const configs: Record<string, { extensions: string[] }> = {
    code: { extensions: ['py','js','ts','jsx','tsx','java','go','rs','c','cpp','h','hpp','swift','kt','rb','php','sql','sh','bash','vue','svelte','astro'] },
    document: { extensions: ['txt','md','pdf','docx','rtf','tex','rst','org','log'] },
    image: { extensions: ['png','jpg','jpeg','gif','svg','webp','bmp','ico'] },
    archive: { extensions: ['zip','rar','7z','tar','gz','bz2','xz'] },
    spreadsheet: { extensions: ['xlsx','xls','csv','tsv'] },
    config: { extensions: ['json','yaml','yml','xml','toml','ini','cfg','env','conf','cnf','editorconfig','gitignore','dockerfile','makefile','prisma','graphql','proto'] },
  };
  for (const [cat, cfg] of Object.entries(configs)) {
    if (cfg.extensions.includes(ext)) return cat;
  }
  return 'unknown';
}

function extractApiError(err: unknown): string {
  if (err && typeof err === 'object' && 'detail' in err) {
    const detail = (err as Record<string, unknown>).detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return (detail as Array<{ msg?: string }>).map((d) => d.msg || '').filter(Boolean).join('; ') || 'Validation error';
    }
  }
  return 'Upload failed';
}

export default function AgentHubIM(): JSX.Element {
  const [token, setToken] = useState<string>('');
  const [user, setUser] = useState<User | null>(null);
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login');
  const [authForm, setAuthForm] = useState<{ name: string; password: string }>({ name: 'admin', password: 'admin123' });
  // ── 当前 session 的 messages / isStreaming 从 SessionStore 派生 ────────────
  // 这样切到别的 session 时，旧 session 的 messages 和流状态会保留在 Map 里，
  // 切回来时直接命中缓存，流的 chunks 不会丢。
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionId, setSessionId] = useState<string>('');
  const messages = useSessionMessages(sessionId);
  const isStreaming = useSessionStreaming(sessionId);
  const [sessionQuery, setSessionQuery] = useState<string>('');
  const [input, setInput] = useState<string>('@CodeGen Generate a FastAPI health route file, save as health_router.py');
  const [dag, setDag] = useState<DagState>({ total: 0, completed: 0, nodes: [] });
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
  // 附件卡片的全屏预览弹窗 (点击眼睛按钮触发)
  const [previewFile, setPreviewFile] = useState<FilePreviewTarget | null>(null);
  const [isAutoNaming, setIsAutoNaming] = useState<boolean>(false);
  const [previewTabs, setPreviewTabs] = useState<WorkspacePreviewTab[]>([]);
  const [activePreviewTabId, setActivePreviewTabId] = useState<string | null>(null);
  const [fileReferences, setFileReferences] = useState<FileReference[]>([]);
  const [previewPanelOpen, setPreviewPanelOpen] = useState<boolean>(false);
  /**
   * 触发预览面板跳转到某条引用对应的行号。
   * 写一个递增 counter，让 FilePreviewPanel 用 effect 监听变化并执行滚动。
   */
  const [pendingScrollRef, setPendingScrollRef] = useState<{ id: string; nonce: number } | null>(null);

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
  const retryRef = useRef<PendingMessage[]>([]);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const lastMessageIdRef = useRef<string>('');
  const dedupIdsRef = useRef<Set<string>>(new Set());
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const messagesContainerRef = useRef<HTMLElement | null>(null);
  const currentSessionRef = useRef<string>(sessionId);
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
  const blurTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Effects ──────────────────────────────────────────────

  useEffect(() => {
    currentSessionRef.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    const saved = localStorage.getItem('agenthub_token');
    const savedUser = localStorage.getItem('agenthub_user');
    if (saved) setToken(saved);
    if (savedUser) setUser(JSON.parse(savedUser) as User);
  }, []);

  // Apply global settings (theme, lang, zoom) on page load
  useEffect(() => {
    const theme = localStorage.getItem('agenthub_theme') || 'warm';
    const lang = localStorage.getItem('agenthub_lang') || 'zh';
    const zoom = localStorage.getItem('agenthub_zoom') || '100';

    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.lang = lang === 'en' ? 'en' : 'zh-CN';
    document.body.style.zoom = `${zoom}%`;
  }, []);

  useEffect(() => {
    if (!token) return;
    fetch('/api/chat/sessions', { headers: authHeaders() })
      .then((r) => r.json())
      .then((data: ChatSession[]) => {
        setSessions(sortSessions(data));
        if (!data.find((s) => s.id === sessionId) && data.length) {
          setSessionId(data[0].id);
        }
      })
      .catch(() => {});
    fetch('/api/agent/registry', { headers: authHeaders() })
      .then((r) => r.json())
      .then((data: Agent[]) => setAgents(data.length ? data : FALLBACK_AGENTS))
      .catch(() => setAgents(FALLBACK_AGENTS));
    fetch('/api/chat/workflows', { headers: authHeaders() })
      .then((r) => r.json())
      .then((data: WorkflowSummary[]) => setWorkflows(data))
      .catch(() => {});
    fetch('/api/skills')
      .then((r) => r.json())
      .then((data: { skills: SkillMeta[] }) => setSkills(data.skills || []))
      .catch(() => {});
  }, [token]);

  async function reloadMessages(merge = false): Promise<void> {
    const sid = currentSessionRef.current;
    try {
      const res = await fetch(`/api/chat/sessions/${sid}/messages`, { headers: authHeaders() });
      if (!res.ok) return;
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
    } catch { /* ignore */ }
  }

  function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
    const localToken = typeof window !== 'undefined' ? localStorage.getItem('agenthub_token') : '';
    return localToken ? { ...extra, Authorization: `Bearer ${localToken}` } : extra;
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
    const container = messagesContainerRef.current;
    if (!container) return;

    const currentCount = messages.length;
    const isNewMessage = currentCount > prevMessageCountRef.current;
    const isSessionSwitch = sessionId !== prevSessionRef.current;
    prevMessageCountRef.current = currentCount;
    prevSessionRef.current = sessionId;

    if (isSessionSwitch) {
      requestAnimationFrame(() => {
        container.scrollTo({ top: container.scrollHeight, behavior: 'auto' });
      });
      return;
    }

    if (isNewMessage) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
      return;
    }

    const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    if (distanceToBottom < 120) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'auto' });
    }
  }, [messages, sessionId]);

  // ── Streaming ────────────────────────────────────────────

  /**
   * 把指定 session 的 buffer 中的 chunks 应用到对应 session 的 messages。
   * 每个 session 独立调度：切到别的 session 时旧 session 的 chunks 仍能被正确
   * 累积到对应 session 的 messages 里，切回来时直接看到完整流式结果。
   */
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

    const contentDelta = buf.chunks.join('');
    const finalFlag = buf.isFinal;
    // 重置 buffer 内容（保留 isFinal 以便 isFinal 分支消费）
    const nextBuf: StreamBuffer = {
      messageId: buf.messageId,
      sessionId: buf.sessionId,
      chunks: [],
      isFinal: finalFlag ? false : buf.isFinal,
    };
    setSessionBuffer(buf.sessionId, nextBuf);

    const bufMessageId = buf.messageId;

    updateSessionMessages(buf.sessionId, (prev) => {
      const idx = prev.findIndex((m) => m.messageId === bufMessageId);
      if (idx >= 0) {
        const updated = [...prev];
        updated[idx] = {
          ...updated[idx],
          content: updated[idx].content + contentDelta,
          isStreaming: !finalFlag,
        };
        return updated;
      }
      const newMsg: Message = {
        event: 'message',
        sessionId: buf.sessionId,
        sender: 'agent',
        content: contentDelta,
        type: 'text',
        timestamp: new Date().toISOString(),
        messageId: bufMessageId,
        isStreaming: !finalFlag,
      };
      return [...prev, newMsg];
    });

    if (finalFlag) {
      setSessionStreaming(buf.sessionId, false);
      setSessionBuffer(buf.sessionId, null);
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
    // eslint-disable-next-line no-console
    console.log('[agenthub] connectWs called', { targetSid, ref: currentSessionRef.current, finalSid: sid, alreadyHas: wsRef.current.has(sid), wsMapKeys: Array.from(wsRef.current.keys()) });
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

    const ws = new WebSocket(`ws://127.0.0.1:8000/ws/${sid}?token=${encodeURIComponent(token)}`);
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
      // 仅当用户当前还在这个 session 才重连
      if (currentSessionRef.current === sid) {
        reconnectAttemptsRef.current += 1;
        if (reconnectRef.current) clearTimeout(reconnectRef.current);
        reconnectRef.current = setTimeout(connectWs, _reconnectDelay());
      }
    };

    ws.onerror = () => {
      wsReadyRef.current.delete(sid);
      setConnected(false);
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

      if (evt === 'task_update') {
        setDag({ total: raw.total as number || 0, completed: raw.completed as number || 0, nodes: raw.nodes as DagState['nodes'] || [] });
      }

      if (evt === 'message_chunk') {
        const chunk = raw as unknown as StreamChunk;
        const cSessionId = chunk.sessionId || chunkSessionId;
        setSessionStreaming(cSessionId, !chunk.isFinal);

        const existingBuf = getSessionBuffer(cSessionId);
        if (!existingBuf || existingBuf.messageId !== chunk.messageId) {
          // First chunk of a new stream — clean up any thinking placeholder
          // from the agent_thinking event (it has a different messageId)
          updateSessionMessages(cSessionId, (prev) => {
            const cleaned = prev.filter(
              (m) => !(m.isStreaming && m.sender !== 'user' && (!m.content || m.content.startsWith('正在')))
            );
            return cleaned.length !== prev.length ? cleaned : prev;
          });

          setSessionBuffer(cSessionId, {
            messageId: chunk.messageId,
            sessionId: cSessionId,
            chunks: [],
            isFinal: false,
          });
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

        // 调度该 session 自己的 RAF flush
        if (!streamFlushRafRef.current.has(cSessionId)) {
          const raf = window.requestAnimationFrame(() => {
            streamFlushRafRef.current.delete(cSessionId);
            flushStreamBuffer(cSessionId);
          });
          streamFlushRafRef.current.set(cSessionId, raf);
        }
      }

      if (evt === 'stream_interrupted') {
        const iSessionId = chunkSessionId;
        setSessionStreaming(iSessionId, false);
        updateSessionMessages(iSessionId, (prev) => {
          const updated = [...prev];
          let changed = false;
          for (let i = updated.length - 1; i >= 0; i--) {
            if (updated[i].isStreaming) {
              // If it's a thinking placeholder (empty or progress text), remove it entirely
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
        setSessionStreaming(chunkSessionId, true);
        // Insert or update the thinking placeholder with phase details.
        // Subsequent agent_thinking events (e.g. "executing", "synthesizing")
        // update the same placeholder in-place so the user sees live progress.
        updateSessionMessages(chunkSessionId, (prev) => {
          const existingIdx = prev.findIndex(
            (m) => m.messageId === payload.messageId && m.isStreaming
          );
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
        updateSessionMessages(chunkSessionId, (prev) => {
          // Remove empty thinking placeholders — tool execution has started
          const cleaned = prev.filter(
            (m) => !(m.isStreaming && m.sender !== 'user' && (!m.content || m.content.startsWith('正在')))
          );
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

      if (evt === 'session_renamed') {
        const payload = raw as { sessionId: string; name: string };
        setSessions((prev) => prev.map((s) => (s.id === payload.sessionId ? { ...s, name: payload.name } : s)));
        setIsAutoNaming(false);
      }

      if (evt === 'message') {
        // Flush any pending stream buffer for THIS session before searching
        // for the placeholder — the RAF callback may not have fired yet.
        const cSessionId = chunkSessionId;
        const buf = getSessionBuffer(cSessionId);
        if (buf && streamFlushRafRef.current.has(cSessionId)) {
          const raf = streamFlushRafRef.current.get(cSessionId);
          if (raf != null) window.cancelAnimationFrame(raf);
          streamFlushRafRef.current.delete(cSessionId);
          flushStreamBuffer(cSessionId);
        }
        setSessionStreaming(cSessionId, false);
        const msg = raw as unknown as Message;
        const isSystemMsg = msg.type === 'system' || msg.sender === 'system';
        updateSessionMessages(cSessionId, (prev) => {
          // Clean up any empty thinking placeholders (from agent_thinking) before
          // adding/replacing the final message
          let cleaned = prev;
          const hasEmptyThinkers = prev.some(
            (m) => m.isStreaming && m.sender !== 'user' && (!m.content || m.content.startsWith('正在'))
          );
          if (hasEmptyThinkers) {
            cleaned = prev.filter(
              (m) => !(m.isStreaming && m.sender !== 'user' && (!m.content || m.content.startsWith('正在')))
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
    localStorage.removeItem('agenthub_token');
    localStorage.removeItem('agenthub_user');
    // 关闭所有 session 的 WebSocket
    Array.from(wsRef.current.keys()).forEach((sid) => closeWs(sid));
    // 关闭 store 里所有的 session
    Array.from(wsRef.current.keys()).forEach((sid) => clearSession(sid));
    if (reconnectRef.current) {
      clearTimeout(reconnectRef.current);
      reconnectRef.current = null;
    }
    streamFlushRafRef.current.forEach((raf) => window.cancelAnimationFrame(raf));
    streamFlushRafRef.current.clear();
    setToken('');
    setUser(null);
  }, []);

  const handleCreateSession = useCallback(async () => {
    const name = `Untitled Session ${sessions.length + 1}`;
    const res = await fetch('/api/chat/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ name }),
    });
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
    console.log('[agenthub] select session', { from: currentSessionRef.current, to: id });
    currentSessionRef.current = id;
    activeSessionIdRef.current = id;  // 立即同步给“当前活跃 session” ref，避免下一帧前就发消息
    setSessionId(id);
    setTaskOpen(false);
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
      const res = await fetch(`/api/chat/sessions/${id}`, { method: 'DELETE', headers: authHeaders() });
      const data = await res.json();
      if (!res.ok) {
        setNotice(data.detail || 'Delete failed');
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

    // eslint-disable-next-line no-console
    console.log('[agenthub] handleSend', {
      closureSessionId: sessionId,
      refSessionId: activeSessionId,
      text,
      wsMapKeys: Array.from(wsRef.current.keys()),
      targetWsState: wsRef.current.get(activeSessionId)?.readyState,
    });

    const clientId = `client-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    const localMsg: Message = {
      id: clientId,
      event: 'message',
      sessionId: activeSessionId,
      content: displayContent,
      sender: user?.name || 'user',
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
    };

    if (isStreaming) {
      setSessionStreaming(activeSessionId, false);
    }
    updateSessionMessages(activeSessionId, (prev) => [...prev, localMsg]);
    setSessions((prev) => sortSessions(prev.map((s) => (s.id === activeSessionId ? { ...s, lastMessageAt: localMsg.timestamp } : s))));
    const targetWs = wsRef.current.get(activeSessionId);
    if (targetWs && targetWs.readyState === WebSocket.OPEN) {
      targetWs.send(JSON.stringify(wsMsg));
    } else {
      // ws 还没连上：保险起见触发一次连接（如果本来就在连，就是 no-op）。
      connectWs(activeSessionId);
      retryRef.current.push(wsMsg);
      setPending((prev) => [...prev, wsMsg]);
      setNotice('Message queued for retry');
    }
    setInput('');
    setAttachedFiles([]);
    setFileReferences([]);
  }, [sessionId, user, isStreaming, fileReferences]);

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

  // Toggle preview panel
  const handleTogglePreviewPanel = useCallback(() => {
    setPreviewPanelOpen((prev) => !prev);
  }, []);

  // Open workspace file in preview panel (fetched by FilePreviewPanel)
  const handleOpenWorkspaceFile = useCallback((path: string, content: string, language: string, state: string) => {
    setPreviewPanelOpen(true);
    setPreviewTabs((prev) => {
      const existing = prev.find((t) => t.path === path && t.kind === 'file');
      if (existing) {
        setActivePreviewTabId(existing.id);
        return prev.map((t) => (t.id === existing.id ? { ...t, content, language, state: state as WorkspacePreviewTab['state'] } : t));
      }
      const newTab: WorkspacePreviewTab = {
        id: `ws-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        path,
        kind: 'file',
        content,
        language,
        state: state as WorkspacePreviewTab['state'],
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
        const resp = await fetch(
          `/api/files/workspace/read?path=${encodeURIComponent(ref.path)}`,
          { headers: token ? { Authorization: `Bearer ${token}` } : {} },
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const content = data?.content ?? '';
        const language = data?.language ?? '';
        const state = data?.state ?? 'ok';
        handleOpenWorkspaceFile(ref.path, content, language, state);
        setPendingScrollRef({ id: ref.id, nonce: Date.now() });
      } catch (err) {
        // 拿不到内容也至少提示一下；不让 console 全是红
        console.warn('[references] jump failed to load file:', ref.path, err);
        setNotice(`无法打开文件: ${ref.path}`);
      }
    },
    [handleOpenWorkspaceFile, setNotice],
  );

  // ── File upload ──────────────────────────────────────────

  async function uploadFileChunked(file: File): Promise<string> {
    const CHUNK_SIZE = 512 * 1024;
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);

    const initRes = await fetch('/api/files/upload/init', { method: 'POST', headers: authHeaders() });
    const { uploadId } = (await initRes.json()) as { uploadId: string; chunkSizeHint: number };

    for (let i = 0; i < totalChunks; i++) {
      const start = i * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, file.size);
      const chunk = file.slice(start, end);

      const formData = new FormData();
      formData.append('file', chunk, `${file.name}.chunk${i}`);
      formData.append('upload_id', uploadId);
      formData.append('chunk_index', String(i));
      formData.append('total_chunks', String(totalChunks));
      formData.append('file_name', file.name);

      const res = await fetch('/api/files/upload/chunk', {
        method: 'POST',
        headers: authHeaders(),
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(extractApiError(err) || `Chunk ${i} failed`);
      }

      setAttachedFiles((prev) => prev.map((f) =>
        f.name === file.name
          ? { ...f, uploadProgress: Math.round(((i + 1) / totalChunks) * 100), uploadStatus: 'uploading' as const }
          : f,
      ));
    }

    const completeRes = await fetch('/api/files/upload/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ upload_id: uploadId, file_name: file.name, total_chunks: totalChunks }),
    });

    if (!completeRes.ok) {
      throw new Error('Upload completion failed');
    }

    return uploadId;
  }

  // Shared file-processing helper — used by both file input and clipboard paste
  function processFiles(files: File[]): void {
    const MAX_INLINE = 2 * 1024 * 1024;
    const MAX_TOTAL = 50 * 1024 * 1024;

    files.forEach(async (file) => {
      const ext = (file.name.split('.').pop() || '').toLowerCase();
      const category = detectFileCategory(file.name, file.type);

      if (category === 'unknown') {
        setNotice(`不支持的文件类型: ${file.name}`);
        return;
      }

      if (file.size > MAX_TOTAL) {
        setNotice(`文件 ${file.name} 超过 50MB 限制`);
        return;
      }

      const base: AttachedFile = {
        name: file.name,
        size: file.size,
        type: file.type || 'application/octet-stream',
        category: category as AttachedFile['category'],
        uploadStatus: 'pending',
      };

      const isInlineText = (category === 'code' || category === 'config' || (category === 'document' && ext !== 'pdf' && ext !== 'docx' && ext !== 'rtf'));
      const isInlineImage = category === 'image' && file.size <= MAX_INLINE;
      const canInline = (isInlineText && file.size <= MAX_INLINE) || isInlineImage;

      if (canInline) {
        const reader = new FileReader();
        reader.onload = () => {
          setAttachedFiles((prev) => [...prev, {
            ...base,
            content: reader.result as string,
            uploadStatus: 'done' as const,
            uploadProgress: 100,
          }]);
        };
        reader.onerror = () => {
          setNotice(`读取文件失败: ${file.name}`);
        };
        if (isInlineImage) {
          reader.readAsDataURL(file);
        } else {
          reader.readAsText(file);
        }
      } else {
        setAttachedFiles((prev) => [...prev, { ...base, uploadProgress: 0 }]);

        try {
          const fileId = await uploadFileChunked(file);
          setAttachedFiles((prev) => prev.map((f) =>
            f.name === file.name
              ? { ...f, fileId, uploadStatus: 'done' as const, uploadProgress: 100 }
              : f,
          ));
        } catch (err: unknown) {
          const msg = err instanceof Error ? err.message : 'Upload failed';
          setAttachedFiles((prev) => prev.map((f) =>
            f.name === file.name
              ? { ...f, uploadStatus: 'error' as const, uploadError: msg }
              : f,
          ));
          setNotice(`上传失败: ${file.name} - ${msg}`);
        }
      }
    });
  }

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const fs = e.target.files;
    if (!fs || fs.length === 0) return;
    processFiles(Array.from(fs));
    e.target.value = '';
  }, []);

  const handlePasteFiles = useCallback((files: File[]) => {
    if (files.length === 0) return;
    processFiles(files);
    setNotice(`已从剪贴板添加 ${files.length} 张图片`);
  }, []);

  const handleRemoveFile = useCallback((index: number) => {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  // 点击附件卡片上的预览按钮: 弹出全屏预览模态
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

  return (
    <div className="flex h-screen bg-warm-50 text-warm-800 overflow-hidden">
      <SessionSidebar
        user={user}
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
        onLogout={handleLogout}
        width={sidebarWidthLive ?? sidebarWidth}
      />

      <ResizableDivider
        orientation="horizontal"
        size={sidebarWidthLive ?? sidebarWidth}
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
        // 气泡出现在被调整的左侧栏内部（分隔条左侧）
        bubbleSide="left"
      />

      <main className="flex flex-1 flex-col min-h-0">
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
        />

        <MessageList
          messages={messages}
          user={user}
          generated={generated}
          onCommit={handleCommit}
          messagesContainerRef={messagesContainerRef}
          bottomRef={bottomRef}
        />

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
          onPreviewFile={handlePreviewFile}
          onClearAllFiles={handleClearAllFiles}
          onPasteFiles={handlePasteFiles}
          onInsertMention={handleInsertMention}
          onInsertAllMentions={handleInsertAllMentions}
          onInsertWorkflow={handleInsertWorkflow}
          onInsertSkill={handleInsertSkill}
          onMentionSearchChange={handleMentionSearchChange}
          onMentionActiveIndexChange={handleMentionActiveIndexChange}
          onRiskLevelChange={handleRiskLevelChange}
          fileReferences={fileReferences}
          onRemoveReference={handleRemoveReference}
          onClearAllReferences={handleClearAllReferences}
          onJumpToReference={handleJumpToReference}
        />
      </main>

      {/* ── File Preview Panel (right side) ──────────────────── */}
      {previewPanelOpen && (
        <>
          <ResizableDivider
            orientation="horizontal"
            size={previewWidthLive ?? previewWidth}
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
            // 拖动方向反向：向右拖 = 聊天区推开分隔条变宽 = 预览面板变窄
            reversed
            // 气泡出现在被调整的预览面板内部（分隔条右侧）
            bubbleSide="right"
          />
          <aside
            className="border-l border-warm-150 flex flex-col h-full bg-white shrink-0"
            style={{ width: `${previewWidthLive ?? previewWidth}px` }}
          >
            <FilePreviewPanel
              tabs={previewTabs}
              activeTabId={activePreviewTabId}
              onSelectTab={handleSelectPreviewTab}
              onCloseTab={handleClosePreviewTab}
              onAddReference={handleAddReference}
              onOpenWorkspaceFile={handleOpenWorkspaceFile}
              references={fileReferences}
              pendingScrollRef={pendingScrollRef}
            />
          </aside>
        </>
      )}

      {taskOpen && (
        <DagModal dag={dag} onClose={handleTaskClose} />
      )}

      <PreviewSidebar open={previewOpen} onClose={handlePreviewClose} previewUrl={previewUrl} />

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

      {/* 附件文件全屏预览弹窗 - 点击附件卡片上的眼睛按钮触发 */}
      <FilePreviewModal
        file={previewFile}
        onClose={() => setPreviewFile(null)}
        authToken={token}
      />
    </div>
  );
}
