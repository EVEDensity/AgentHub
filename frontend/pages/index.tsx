import React, { useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react';
import AuthForm from '../components/chat/AuthForm';
import ChatHeader from '../components/chat/ChatHeader';
import ChatInput from '../components/chat/ChatInput';
import DagModal from '../components/chat/DagModal';
import MessageList from '../components/chat/MessageList';
import SessionSidebar from '../components/chat/SessionSidebar';
import PreviewSidebar from '../components/shared/PreviewSidebar';
import type { Agent, AttachedFile, AttachmentMeta, ChatSession, DagState, GeneratedData, Message, PendingMessage, StreamChunk, User, WorkflowSummary } from '../types';

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
  const [mentionTrigger, setMentionTrigger] = useState<'@' | '#'>('@');
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [editingId, setEditingId] = useState<string>('');
  const [editName, setEditName] = useState<string>('');
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);

  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<PendingMessage[]>([]);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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
          const clean = prev.filter((m) => !m.messageId);
          return [...clean, ...newMessages].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
        });
      } else {
        setMessages([...data].sort((a, b) => a.timestamp.localeCompare(b.timestamp)));
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
    if (!buf || buf.chunks.length === 0 || currentSessionRef.current !== buf.sessionId) return;
    const contentDelta = buf.chunks.join('');
    const finalFlag = buf.isFinal;
    buf.chunks = [];
    buf.isFinal = false;

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

  function connectWs(): void {
    const sid = currentSessionRef.current;
    if (reconnectRef.current) {
      clearTimeout(reconnectRef.current);
      reconnectRef.current = null;
    }
    const ws = new WebSocket(`ws://127.0.0.1:8000/ws/${sid}?token=${encodeURIComponent(token)}`);
    wsRef.current = ws;
    ws.onopen = () => {
      if (currentSessionRef.current !== sid) { ws.close(); return; }
      setConnected(true);
      setNotice('WebSocket connected');
      void reloadMessages(true);
      const queued = [...retryRef.current];
      retryRef.current = [];
      setPending([]);
      queued.forEach((msg) => ws.send(JSON.stringify(msg)));
    };
    ws.onclose = () => {
      setConnected(false);
      if (streamFlushRafRef.current != null) {
        window.cancelAnimationFrame(streamFlushRafRef.current);
        streamFlushRafRef.current = null;
      }
      streamBufferRef.current = null;
      setIsStreaming(false);
      if (currentSessionRef.current === sid) {
        reconnectRef.current = setTimeout(connectWs, 1500);
      }
    };
    ws.onerror = () => setConnected(false);
    ws.onmessage = (event: MessageEvent<string>) => {
      if (currentSessionRef.current !== sid) return;
      const raw: Record<string, unknown> = JSON.parse(event.data);
      const evt = raw.event as string | undefined;

      if (evt === 'task_update') {
        setDag({ total: raw.total as number || 0, completed: raw.completed as number || 0, nodes: raw.nodes as DagState['nodes'] || [] });
      }

      if (evt === 'message_chunk') {
        const chunk = raw as unknown as StreamChunk;
        setIsStreaming(!chunk.isFinal);

        if (!streamBufferRef.current || streamBufferRef.current.messageId !== chunk.messageId || streamBufferRef.current.sessionId !== chunk.sessionId) {
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
          const lastIdx = updated.length - 1;
          if (lastIdx >= 0 && updated[lastIdx].isStreaming) {
            updated[lastIdx] = {
              ...updated[lastIdx],
              isStreaming: false,
              content: updated[lastIdx].content + '\n\n[Interrupted, processing new message...]',
            };
          }
          return updated;
        });
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
          const streamingIdx = prev.findIndex((m) => m.messageId);
          if (streamingIdx >= 0 && !isSystemMsg) {
            const updated = [...prev];
            updated[streamingIdx] = { ...msg, messageId: undefined, isStreaming: false };
            if (msg.id) {
              return updated.filter((m, i) => i === streamingIdx || m.id !== msg.id);
            }
            return updated;
          }
          if (isSystemMsg && msg.content && msg.content.includes('已连接')) {
            return prev;
          }
          if (msg.id && prev.some((m) => m.id === msg.id)) {
            return prev;
          }
          return [...prev, { ...msg, messageId: undefined, isStreaming: false }];
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

    const candidates: Array<{ pos: number; trigger: '@' | '#' }> = [];
    if (lastAt >= 0) candidates.push({ pos: lastAt, trigger: '@' });
    if (lastHash >= 0) candidates.push({ pos: lastHash, trigger: '#' });
    candidates.sort((a, b) => b.pos - a.pos);

    for (const c of candidates) {
      const charBefore = c.pos === 0 ? ' ' : value[c.pos - 1];
      const textAfter = textBefore.slice(c.pos + 1);
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

  const handleRenameSession = useCallback(async (id: string) => {
    const name = editName.trim();
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

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mentionOpen) {
      const itemCount = mentionTrigger === '@' ? filteredAgents.length : filteredWorkflows.length;
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
          } else {
            handleInsertWorkflow(filteredWorkflows[mentionActiveIndex]);
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
  }, [mentionOpen, mentionTrigger, mentionActiveIndex, filteredAgents, filteredWorkflows]);

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
    const text = (customText || currentInput).trim();
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

    const localMsg: Message = {
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

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const fs = e.target.files;
    if (!fs || fs.length === 0) return;

    const MAX_INLINE = 2 * 1024 * 1024;
    const MAX_TOTAL = 50 * 1024 * 1024;

    Array.from(fs).forEach(async (file) => {
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
    e.target.value = '';
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
    <div className="flex h-screen bg-warm-50 text-warm-800">
      <SessionSidebar
        user={user}
        filteredSessions={filteredSessions}
        sessionId={sessionId}
        sessionQuery={sessionQuery}
        editingId={editingId}
        editName={editName}
        notice={notice}
        sessionsLength={sessions.length}
        onCreateSession={handleCreateSession}
        onSelectSession={handleSelectSession}
        onDeleteSession={handleDeleteSession}
        onRenameSession={handleRenameSession}
        onTogglePin={handleTogglePin}
        onStartRename={handleStartRename}
        onSessionQueryChange={handleSessionQueryChange}
        onEditNameChange={handleEditNameChange}
        onEditNameKeyDown={handleEditNameKeyDown}
        onEditNameBlur={handleEditNameBlur}
        onLogout={handleLogout}
      />

      <main className="flex flex-1 flex-col">
        <ChatHeader
          sessionName={sessionName}
          connected={connected}
          isStreaming={isStreaming}
          percent={percent}
          onTaskClick={handleTaskClick}
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
          onInsertMention={handleInsertMention}
          onInsertAllMentions={handleInsertAllMentions}
          onInsertWorkflow={handleInsertWorkflow}
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
