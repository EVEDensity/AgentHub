import React, { useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react';
import AuthForm from '../components/chat/AuthForm';
import ChatHeader from '../components/chat/ChatHeader';
import ChatInput from '../components/chat/ChatInput';
import DagModal from '../components/chat/DagModal';
import MessageList from '../components/chat/MessageList';
import SessionSidebar from '../components/chat/SessionSidebar';
import PreviewSidebar from '../components/shared/PreviewSidebar';
import type { Agent, AttachedFile, AttachmentMeta, ChatSession, DagState, GeneratedData, Message, PendingMessage, SkillMeta, StreamChunk, ToolCallEvent, ToolResultEvent, User, WorkflowSummary } from '../types';

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
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionId, setSessionId] = useState<string>('session-1');
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
  const [selectedRiskLevel, setSelectedRiskLevel] = useState<string>('all');
  const [mentionOpen, setMentionOpen] = useState<boolean>(false);
  const [mentionActiveIndex, setMentionActiveIndex] = useState<number>(0);
  const [mentionTrigger, setMentionTrigger] = useState<'@' | '#' | '/'>( '@');
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [skills, setSkills] = useState<SkillMeta[]>([]);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [editingId, setEditingId] = useState<string>('');
  const [editName, setEditName] = useState<string>('');
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const [isAutoNaming, setIsAutoNaming] = useState<boolean>(false);

  const wsRef = useRef<WebSocket | null>(null);
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
  const streamBufferRef = useRef<{ messageId: string; sessionId: string; chunks: string[]; isFinal: boolean } | null>(null);
  const streamFlushRafRef = useRef<number | null>(null);
  const inputRef = useRef(input);
  inputRef.current = input;
  const attachedFilesRef = useRef(attachedFiles);
  attachedFilesRef.current = attachedFiles;
  const prevMessageCountRef = useRef(0);
  const prevSessionRef = useRef<string>(sessionId);
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
    try {
      const sid = currentSessionRef.current;
      const res = await fetch(`/api/chat/sessions/${sid}/messages`, { headers: authHeaders() });
      if (!res.ok) return;
      const data: Message[] = (await res.json()) as Message[];
      if (merge) {
        setMessages((prev) => {
          const existingIds = new Set(prev.filter((m) => m.id).map((m) => m.id));
          const newMessages = data.filter((m) => !m.id || !existingIds.has(m.id));
          if (newMessages.length === 0) return prev;
          // Only remove actively-streaming temp messages; keep finalized ones
          // that haven't been replaced by the DB message event yet.
          const clean = prev.filter((m) => !m.isStreaming);
          return [...clean, ...newMessages].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
        });
      } else {
        // Only replace all messages if data is non-empty; an empty API
        // response usually means a race condition (DB not yet written).
        // Replacing with [] would wipe the chat history from view.
        if (data.length > 0) {
          setMessages([...data].sort((a, b) => a.timestamp.localeCompare(b.timestamp)));
        }
      }
    } catch { /* ignore */ }
  }

  function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
    const localToken = typeof window !== 'undefined' ? localStorage.getItem('agenthub_token') : '';
    return localToken ? { ...extra, Authorization: `Bearer ${localToken}` } : extra;
  }

  useEffect(() => {
    if (!token || !sessionId) return;
    void reloadMessages(false);
    connectWs();
    return () => {
      wsRef.current?.close();
      if (reconnectRef.current) {
        clearTimeout(reconnectRef.current);
        reconnectRef.current = null;
      }
      if (streamFlushRafRef.current != null) {
        window.cancelAnimationFrame(streamFlushRafRef.current);
        streamFlushRafRef.current = null;
      }
      if (blurTimerRef.current) {
        clearTimeout(blurTimerRef.current);
        blurTimerRef.current = null;
      }
      streamBufferRef.current = null;
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

  function flushStreamBuffer(): void {
    const buf = streamBufferRef.current;
    if (!buf || currentSessionRef.current !== buf.sessionId) return;

    // Handle final signal even when no content chunks are pending.
    // The backend sends an empty message_chunk with isFinal=true to
    // signal end-of-stream.  Without this branch the early return
    // below would drop the final flag, leaving isStreaming stuck.
    if (buf.chunks.length === 0) {
      if (buf.isFinal) {
        setIsStreaming(false);
        streamBufferRef.current = null;
      }
      return;
    }

    const contentDelta = buf.chunks.join('');
    const finalFlag = buf.isFinal;
    buf.chunks = [];
    // Only clear isFinal when this flush didn't consume it —
    // preserves the final signal if chunks arrive in the same
    // JS tick as the empty isFinal marker.
    if (!finalFlag) {
      buf.isFinal = false;
    }

    setMessages((prev) => {
      const idx = prev.findIndex((m) => m.messageId === buf.messageId);
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
        messageId: buf.messageId,
        isStreaming: !finalFlag,
      };
      return [...prev, newMsg];
    });

    if (finalFlag) {
      setIsStreaming(false);
      streamBufferRef.current = null;
    }
  }

  // ── WebSocket ────────────────────────────────────────────

  function _reconnectDelay(): number {
    // Exponential backoff: 1s → 2s → 4s → 8s → ... → 30s max
    const attempt = reconnectAttemptsRef.current;
    const delay = Math.min(1000 * Math.pow(2, attempt), 30000);
    return delay + Math.random() * 500; // jitter to avoid thundering herd
  }

  function connectWs(): void {
    const sid = currentSessionRef.current;
    if (reconnectRef.current) {
      clearTimeout(reconnectRef.current);
      reconnectRef.current = null;
    }
    // Close any existing socket
    if (wsRef.current) {
      wsRef.current.onclose = null; // prevent reconnect trigger
      wsRef.current.close();
      wsRef.current = null;
    }

    const ws = new WebSocket(`ws://127.0.0.1:8000/ws/${sid}?token=${encodeURIComponent(token)}`);
    wsRef.current = ws;

    ws.onopen = () => {
      if (currentSessionRef.current !== sid) { ws.close(); return; }
      setConnected(true);
      reconnectAttemptsRef.current = 0; // reset backoff on successful connect
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
      setConnected(false);
      // Flush any buffered stream content BEFORE clearing — otherwise
      // the last batch of chunks from the final RAF frame is lost and
      // the user sees a truncated response ("对话一半").
      if (streamBufferRef.current && streamBufferRef.current.chunks.length > 0) {
        flushStreamBuffer();
      }
      if (streamFlushRafRef.current != null) {
        window.cancelAnimationFrame(streamFlushRafRef.current);
        streamFlushRafRef.current = null;
      }
      streamBufferRef.current = null;
      setIsStreaming(false);
      if (currentSessionRef.current === sid) {
        reconnectAttemptsRef.current += 1;
        reconnectRef.current = setTimeout(connectWs, _reconnectDelay());
      }
    };

    ws.onerror = () => setConnected(false);

    ws.onmessage = (event: MessageEvent<string>) => {
      if (currentSessionRef.current !== sid) return;
      const raw: Record<string, unknown> = JSON.parse(event.data);
      const evt = raw.event as string | undefined;

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
        setIsStreaming(!chunk.isFinal);

        if (!streamBufferRef.current || streamBufferRef.current.messageId !== chunk.messageId || streamBufferRef.current.sessionId !== chunk.sessionId) {
          // First chunk of a new stream — clean up any thinking placeholder
          // from the agent_thinking event (it has a different messageId)
          setMessages((prev) => {
            const cleaned = prev.filter(
              (m) => !(m.isStreaming && m.sender !== 'user' && (!m.content || m.content.startsWith('正在')))
            );
            return cleaned.length !== prev.length ? cleaned : prev;
          });

          streamBufferRef.current = {
            messageId: chunk.messageId,
            sessionId: chunk.sessionId,
            chunks: [],
            isFinal: false,
          };
        }

        if (chunk.content) {
          streamBufferRef.current.chunks.push(chunk.content);
        }
        if (chunk.isFinal) {
          streamBufferRef.current.isFinal = true;
        }

        if (streamFlushRafRef.current == null) {
          streamFlushRafRef.current = window.requestAnimationFrame(() => {
            streamFlushRafRef.current = null;
            flushStreamBuffer();
          });
        }
      }

      if (evt === 'stream_interrupted') {
        setIsStreaming(false);
        setMessages((prev) => {
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

      // ── Fidelity closed-loop events (§3.3) ─────────────────────
      if (evt === 'fidelity_warning') {
        const payload = raw as { agentId?: string; fidelityScore?: number; grade?: string; message?: string };
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.sender === (payload.agentId || '') && last.fidelityScore == null) {
            const updated = [...prev];
            updated[updated.length - 1] = {
              ...last,
              fidelityScore: payload.fidelityScore,
            };
            return updated;
          }
          return [
            ...prev,
            {
              event: 'message',
              sessionId: sid,
              sender: 'system',
              content: payload.message || `保真度警告 (${(payload.fidelityScore || 0).toFixed(2)})`,
              type: 'system' as const,
              timestamp: new Date().toISOString(),
              fidelityScore: payload.fidelityScore,
            },
          ];
        });
      }

      if (evt === 'fidelity_block') {
        const payload = raw as { agentId?: string; fidelityScore?: number; message?: string; requiresHumanConfirm?: boolean };
        setIsStreaming(false);
        setMessages((prev) => [
          ...prev,
          {
            event: 'message',
            sessionId: sid,
            sender: 'system',
            content: `⚠️ ${payload.message || '保真度过低，流程已阻断'}` + (payload.requiresHumanConfirm ? '\n\n需要人工确认后继续。' : ''),
            type: 'system' as const,
            timestamp: new Date().toISOString(),
            fidelityScore: payload.fidelityScore,
          },
        ]);
      }

      if (evt === 'fidelity_resolved') {
        const payload = raw as { agentId?: string; fidelityScore?: number; message?: string };
        setMessages((prev) => [
          ...prev,
          {
            event: 'message',
            sessionId: sid,
            sender: 'system',
            content: `✅ ${payload.message || '保真度已恢复'}`,
            type: 'system' as const,
            timestamp: new Date().toISOString(),
            fidelityScore: payload.fidelityScore,
          },
        ]);
      }

      // ── Agent thinking (shows streaming indicator during tool phase) ──
      if (evt === 'agent_thinking') {
        const payload = raw as {
          messageId: string;
          agentId: string;
          phase?: string;
          details?: string;
        };
        setIsStreaming(true);
        // Insert or update the thinking placeholder with phase details.
        // Subsequent agent_thinking events (e.g. "executing", "synthesizing")
        // update the same placeholder in-place so the user sees live progress.
        setMessages((prev) => {
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
              sessionId: sid,
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
        setMessages((prev) => {
          // Remove empty thinking placeholders — tool execution has started
          const cleaned = prev.filter(
            (m) => !(m.isStreaming && m.sender !== 'user' && (!m.content || m.content.startsWith('正在')))
          );
          return [
            ...cleaned,
            {
              event: 'message',
              sessionId: sid,
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
        setMessages((prev) => {
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
      }

      if (evt === 'session_renamed') {
        const payload = raw as { sessionId: string; name: string };
        setSessions((prev) => prev.map((s) => (s.id === payload.sessionId ? { ...s, name: payload.name } : s)));
        setIsAutoNaming(false);
      }

      if (evt === 'message') {
        // Flush any pending stream buffer before searching for the
        // placeholder — the RAF callback may not have fired yet, and
        // without a placeholder the message below would be appended
        // as a duplicate instead of replacing the streaming entry.
        if (streamBufferRef.current && streamFlushRafRef.current != null) {
          window.cancelAnimationFrame(streamFlushRafRef.current);
          streamFlushRafRef.current = null;
          flushStreamBuffer();
        }
        setIsStreaming(false);
        const msg = raw as unknown as Message;
        const isSystemMsg = msg.type === 'system' || msg.sender === 'system';
        setMessages((prev) => {
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
          setSessions((prev) => sortSessions(prev.map((s) => (s.id === (msg.sessionId || sid) ? { ...s, lastMessageAt: msg.timestamp || new Date().toISOString() } : s))));
        }
        if (msg.symbolic?.generated) setGenerated(msg.symbolic.generated as GeneratedData);
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
    wsRef.current?.close();
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
    setMessages([]);
  }, [sessions.length]);

  const handleSelectSession = useCallback((id: string) => {
    setSessionId(id);
    setTaskOpen(false);
  }, []);

  const handleDeleteSession = useCallback(async (id: string) => {
    const ok = window.confirm('Delete this session?');
    if (!ok) return;
    const res = await fetch(`/api/chat/sessions/${id}`, { method: 'DELETE', headers: authHeaders() });
    const data = await res.json();
    if (!res.ok) {
      setNotice(data.detail || 'Delete failed');
      return;
    }
    setSessions((prev) => prev.filter((s) => s.id !== id));
    if (sessionId === id) {
      const next = sessions.find((s) => s.id !== id);
      setSessionId(next?.id || 'session-1');
      setMessages([]);
    }
  }, [sessionId, sessions]);

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

    const displayContent = text || (currentFiles.length > 0 ? `发送了 ${currentFiles.length} 个文件` : '');

    const clientId = `client-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    const localMsg: Message = {
      id: clientId,
      event: 'message',
      sessionId,
      content: displayContent,
      sender: user?.name || 'user',
      timestamp: new Date().toISOString(),
      type: 'text',
      attachments: fileMetas.length > 0 ? fileMetas : undefined,
    };

    const wsMsg: PendingMessage = {
      sessionId,
      content: aiContent,
      sender: user?.name || 'user',
      timestamp: new Date().toISOString(),
      type: 'text',
      attachments: currentFiles,
    };

    if (isStreaming) {
      setIsStreaming(false);
    }
    setMessages((prev) => [...prev, localMsg]);
    setSessions((prev) => sortSessions(prev.map((s) => (s.id === sessionId ? { ...s, lastMessageAt: localMsg.timestamp } : s))));
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(wsMsg));
    } else {
      retryRef.current.push(wsMsg);
      setPending((prev) => [...prev, wsMsg]);
      setNotice('Message queued for retry');
    }
    setInput('');
    setAttachedFiles([]);
  }, [sessionId, user, isStreaming]);

  const handleRetryMessage = useCallback((msg: PendingMessage) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
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
          onPreview={handlePreview}
          onFileChange={handleFileChange}
          onRemoveFile={handleRemoveFile}
          onPasteFiles={handlePasteFiles}
          onInsertMention={handleInsertMention}
          onInsertAllMentions={handleInsertAllMentions}
          onInsertWorkflow={handleInsertWorkflow}
          onInsertSkill={handleInsertSkill}
          onMentionSearchChange={handleMentionSearchChange}
          onMentionActiveIndexChange={handleMentionActiveIndexChange}
          onRiskLevelChange={handleRiskLevelChange}
        />
      </main>

      {taskOpen && (
        <DagModal dag={dag} onClose={handleTaskClose} />
      )}

      <PreviewSidebar open={previewOpen} onClose={handlePreviewClose} previewUrl={previewUrl} />
    </div>
  );
}
