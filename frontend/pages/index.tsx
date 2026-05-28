import { useEffect, useMemo, useRef, useState, type JSX } from 'react';
import DiffBubble from '../components/chat/DiffBubble';
import MarkdownRenderer from '../components/chat/MarkdownRenderer';
import ThinkingPanel from '../components/chat/ThinkingPanel';
import GeneratedFilesPanel from '../components/git/GeneratedFilesPanel';
import FidelityScore from '../components/chat/FidelityScore';
import PreviewSidebar from '../components/shared/PreviewSidebar';
import type { Agent, AttachmentMeta, GeneratedData, Message, PendingMessage, StreamChunk, User } from '../types';

const AGENTS = ['Orchestrator', 'Architect', 'CodeGen', 'Review', 'Test', 'Deploy'] as const;
const FALLBACK_AGENTS: Agent[] = AGENTS.map((agentId) => ({
  agentId,
  domain: agentId.toLowerCase(),
  status: 'sleeping',
  adapterType: 'mock',
  riskLevel: agentId === 'Deploy' ? 'L3' : agentId === 'CodeGen' || agentId === 'Orchestrator' ? 'L2' : 'L1',
}));

interface ChatSession {
  id: string;
  name: string;
  type?: string;
  active?: number;
  createdAt?: string;
  isPinned?: number;
  lastMessageAt?: string;
}

function sortSessions(items: ChatSession[]): ChatSession[] {
  return [...items].sort((a, b) => {
    const pinDiff = (b.isPinned || 0) - (a.isPinned || 0);
    if (pinDiff !== 0) return pinDiff;
    const aTime = a.lastMessageAt || a.createdAt || '';
    const bTime = b.lastMessageAt || b.createdAt || '';
    return bTime.localeCompare(aTime);
  });
}

interface DagState {
  total: number;
  completed: number;
  nodes: Array<{ id?: string; name?: string; status?: string; agent?: string; description?: string; dependencies?: string[] }>;
}

interface WorkflowSummary {
  routeId: number;
  name: string;
  description: string;
  triggerKeywords: string[];
}

type FileCategory = 'code' | 'document' | 'image' | 'archive' | 'spreadsheet' | 'config' | 'unknown';

const FILE_CATEGORY_CONFIG: Record<FileCategory, { label: string; extensions: string[]; mimePattern: RegExp }> = {
  code: { label: 'Code', extensions: ['py','js','ts','jsx','tsx','java','go','rs','c','cpp','h','hpp','swift','kt','rb','php','sql','sh','bash','vue','svelte','astro'], mimePattern: /^(text\/|\b(?:javascript|typescript|json)\b)/ },
  document: { label: 'Document', extensions: ['txt','md','pdf','docx','rtf','tex','rst','org','log'], mimePattern: /^(text\/|application\/pdf|application\/vnd\.openxmlformats)/ },
  image: { label: 'Image', extensions: ['png','jpg','jpeg','gif','svg','webp','bmp','ico'], mimePattern: /^image\// },
  archive: { label: 'Archive', extensions: ['zip','rar','7z','tar','gz','bz2','xz'], mimePattern: /^(application\/zip|application\/x-rar|application\/x-7z|application\/gzip|application\/x-tar)/ },
  spreadsheet: { label: 'Sheet', extensions: ['xlsx','xls','csv','tsv'], mimePattern: /^(application\/vnd\.(ms-excel|openxmlformats-officedocument\.spreadsheetml)|text\/csv)/ },
  config: { label: 'Config', extensions: ['json','yaml','yml','xml','toml','ini','cfg','env','conf','cnf','editorconfig','gitignore','dockerfile','makefile','prisma','graphql','proto'], mimePattern: /^(application\/json|application\/xml|text\/(xml|yaml|toml))/ },
  unknown: { label: 'File', extensions: [], mimePattern: /^$/ },
};

const ALL_EXTENSIONS: Set<string> = new Set();
Object.values(FILE_CATEGORY_CONFIG).forEach((c) => c.extensions.forEach((e) => ALL_EXTENSIONS.add(e)));
const ACCEPT_STRING = Array.from(ALL_EXTENSIONS).map((e) => `.${e}`).join(',');

function detectFileCategory(name: string, mimeType: string): FileCategory {
  const ext = (name.split('.').pop() || '').toLowerCase();
  for (const [cat, cfg] of Object.entries(FILE_CATEGORY_CONFIG) as [FileCategory, typeof FILE_CATEGORY_CONFIG[FileCategory]][]) {
    if (cfg.extensions.includes(ext)) return cat;
    if (cfg.mimePattern.test(mimeType)) return cat;
  }
  return 'unknown';
}

function FileIcon({ category, size }: { category: FileCategory; size: number }) {
  const cls = `h-${size} w-${size} shrink-0 text-warm-400`;
  switch (category) {
    case 'code':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>;
    case 'document':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>;
    case 'image':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>;
    case 'archive':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 8v13H3V8"/><path d="M1 3h22v5H1z"/><path d="M10 12h4"/></svg>;
    case 'spreadsheet':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/></svg>;
    case 'config':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>;
    default:
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>;
  }
}

interface ContentSegment {
  type: 'think' | 'text';
  content: string;
  isComplete: boolean;
}

function normalizeStructuredStreamContent(content: string): string {
  // Strip lingering thinking tags from providers that ignore the prompt rules
  return content ? content.replace(/<\/?think(?:ing)?>/g, '') : content;
}

function parseThinkSegments(content: string): ContentSegment[] {
  const segments: ContentSegment[] = [];
  const re = /<think>([\s\S]*?)(<\/think>|$)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = re.exec(content)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: 'text', content: content.slice(lastIndex, match.index), isComplete: true });
    }
    segments.push({
      type: 'think',
      content: match[1],
      isComplete: match[2] === '</think>',
    });
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < content.length) {
    segments.push({ type: 'text', content: content.slice(lastIndex), isComplete: true });
  }

  if (segments.length === 0) {
    segments.push({ type: 'text', content, isComplete: true });
  }

  return segments;
}

const INLINE_THRESHOLD = 512 * 1024; // files ≤ 512 KB can be embedded inline

interface AttachedFile {
  name: string;
  content?: string;
  size: number;
  type: string;
  category: FileCategory;
  fileId?: string;
  uploadProgress?: number;
  uploadStatus: 'pending' | 'uploading' | 'done' | 'error';
  uploadError?: string;
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
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [editingId, setEditingId] = useState<string>('');
  const [editName, setEditName] = useState<string>('');
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<PendingMessage[]>([]);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const messagesContainerRef = useRef<HTMLElement | null>(null);
  const currentSessionRef = useRef<string>(sessionId);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const mentionStartRef = useRef<number>(-1);
  const mentionTriggerRef = useRef<'@' | '#'>('@');
  const mentionPanelRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const streamBufferRef = useRef<{ messageId: string; sessionId: string; chunks: string[]; isFinal: boolean } | null>(null);
  const streamFlushRafRef = useRef<number | null>(null);

  const prevMessageCountRef = useRef(0);
  const prevSessionRef = useRef<string>(sessionId);

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
          // Remove streaming placeholders — DB data is authoritative and
          // includes the final saved message for any in-progress stream.
          const clean = prev.filter((m) => !m.messageId);
          return [...clean, ...newMessages].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
        });
      } else {
        setMessages([...data].sort((a, b) => a.timestamp.localeCompare(b.timestamp)));
      }
    } catch { /* ignore */ }
  }

  useEffect(() => {
    if (!token || !sessionId) return;
    void reloadMessages(false);
    connectWs();
    return () => wsRef.current?.close();
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

    // Streaming update — only follow if user hasn't scrolled up
    const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    if (distanceToBottom < 120) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'auto' });
    }
  }, [messages, sessionId]);

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

  function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
    const localToken = typeof window !== 'undefined' ? localStorage.getItem('agenthub_token') : '';
    return localToken ? { ...extra, Authorization: `Bearer ${localToken}` } : extra;
  }

  async function submitAuth(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
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
  }

  function logout(): void {
    localStorage.removeItem('agenthub_token');
    localStorage.removeItem('agenthub_user');
    wsRef.current?.close();
    setToken('');
    setUser(null);
  }

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
        setIsStreaming(false);
        const msg = raw as unknown as Message;
        const isSystemMsg = msg.type === 'system' || msg.sender === 'system';
        setMessages((prev) => {
          // Replace the streaming placeholder (matched by messageId, not isStreaming,
          // because isFinal=true already cleared isStreaming before this event arrives)
          const streamingIdx = prev.findIndex((m) => m.messageId);
          if (streamingIdx >= 0 && !isSystemMsg) {
            const updated = [...prev];
            updated[streamingIdx] = { ...msg, messageId: undefined, isStreaming: false };
            // reloadMessages merge may have already added the same DB record
            // while the placeholder was still pending — remove any duplicate.
            if (msg.id) {
              return updated.filter((m, i) => i === streamingIdx || m.id !== msg.id);
            }
            return updated;
          }
          // Suppress system connect messages from chat log
          if (isSystemMsg && msg.content && msg.content.includes('已连接')) {
            return prev;
          }
          // Dedup by id: reloadMessages may have already fetched this message
          // from DB while the streaming placeholder was still pending replacement.
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

  function detectMention(value: string, cursor: number): void {
    const textBefore = value.slice(0, cursor);
    const lastAt = textBefore.lastIndexOf('@');
    const lastHash = textBefore.lastIndexOf('#');

    // Find nearest trigger; if both present, use the later one
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
        mentionStartRef.current = c.pos;
        mentionTriggerRef.current = c.trigger;
        return;
      }
    }
    setMentionOpen(false);
    setMentionActiveIndex(0);
    mentionStartRef.current = -1;
  }

  function handleInput(e: React.ChangeEvent<HTMLTextAreaElement>): void {
    const value = e.target.value;
    const cursor = e.target.selectionStart || 0;
    setInput(value);
    detectMention(value, cursor);
  }

  function insertMention(agentId: string): void {
    setMentionOpen(false);
    setMentionSearch('');
    const mention = `@${agentId} `;
    const start = mentionStartRef.current;
    mentionStartRef.current = -1;

    if (start >= 0) {
      const ta = textareaRef.current;
      const cursor = ta ? ta.selectionEnd : input.length;
      const before = input.slice(0, start);
      const after = input.slice(cursor);
      const newInput = before + mention + after;
      setInput(newInput);
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
  }

  function insertAllMentions(): void {
    setMentionOpen(false);
    setMentionSearch('');
    const mentions = agents.map((a) => `@${a.agentId} `).join('');
    const start = mentionStartRef.current;
    mentionStartRef.current = -1;

    if (start >= 0) {
      const ta = textareaRef.current;
      const cursor = ta ? ta.selectionEnd : input.length;
      const before = input.slice(0, start);
      const after = input.slice(cursor);
      setInput(before + mentions + after);
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
  }

  function insertAtSymbol(): void {
    const ta = textareaRef.current;
    if (!ta) { setMentionOpen(true); return; }
    const pos = ta.selectionStart ?? input.length;
    const before = input.slice(0, pos);
    const after = input.slice(pos);
    const v = before + '@' + after;
    setInput(v);
    detectMention(v, pos + 1);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (el) {
        el.focus();
        el.selectionStart = pos + 1;
        el.selectionEnd = pos + 1;
      }
    });
  }

  function handleBlur(): void {
    setTimeout(() => {
      const activeEl = document.activeElement;
      if (mentionPanelRef.current?.contains(activeEl)) return;
      setMentionOpen(false);
      setMentionActiveIndex(0);
      mentionStartRef.current = -1;
    }, 200);
  }

  function handleTextareaKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (mentionOpen) {
      const itemCount = mentionTriggerRef.current === '@' ? filteredAgents.length : filteredWorkflows.length;
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
          if (mentionTriggerRef.current === '@') {
            insertMention(filteredAgents[mentionActiveIndex].agentId);
          } else {
            insertWorkflow(filteredWorkflows[mentionActiveIndex]);
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
      send();
    }
  }

  const filteredSessions = useMemo(() => {
    const q = sessionQuery.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) => s.name.toLowerCase().includes(q));
  }, [sessions, sessionQuery]);

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

  function insertWorkflow(wf: WorkflowSummary): void {
    setMentionOpen(false);
    setMentionSearch('');
    const name = `#route:${wf.name}`;
    const start = mentionStartRef.current;
    mentionStartRef.current = -1;

    if (start >= 0) {
      const ta = textareaRef.current;
      const cursor = ta ? ta.selectionEnd : input.length;
      const before = input.slice(0, start);
      const after = input.slice(cursor);
      setInput(before + name + ' ' + after);
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
  }

  async function createSession(): Promise<void> {
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
  }

  function selectSession(id: string): void {
    setSessionId(id);
    setTaskOpen(false);
  }

  async function deleteSession(id: string): Promise<void> {
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
  }

  async function renameSession(id: string): Promise<void> {
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
  }

  async function togglePin(id: string, current: number): Promise<void> {
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
  }

  function startRename(s: ChatSession): void {
    setEditingId(s.id);
    setEditName(s.name);
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

  async function uploadFileChunked(file: File): Promise<string> {
    const CHUNK_SIZE = 512 * 1024; // 512KB
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);

    // Init upload session
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

      // Update progress
      setAttachedFiles((prev) => prev.map((f) =>
        f.name === file.name
          ? { ...f, uploadProgress: Math.round(((i + 1) / totalChunks) * 100), uploadStatus: 'uploading' as const }
          : f,
      ));
    }

    // Complete upload
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

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>): void {
    const fs = e.target.files;
    if (!fs || fs.length === 0) return;

    const MAX_INLINE = 2 * 1024 * 1024;  // 2 MB for inline embedding
    const MAX_TOTAL = 50 * 1024 * 1024;  // 50 MB for HTTP upload

    Array.from(fs).forEach(async (file) => {
      const ext = (file.name.split('.').pop() || '').toLowerCase();
      const category = detectFileCategory(file.name, file.type);

      // Validate extension
      if (category === 'unknown' || !ALL_EXTENSIONS.has(ext)) {
        setNotice(`不支持的文件类型: ${file.name}`);
        return;
      }

      // Validate size
      if (file.size > MAX_TOTAL) {
        setNotice(`文件 ${file.name} 超过 50MB 限制`);
        return;
      }

      const base: AttachedFile = {
        name: file.name,
        size: file.size,
        type: file.type || 'application/octet-stream',
        category,
        uploadStatus: 'pending',
      };

      // Determine strategy: inline vs HTTP upload
      const isInlineText = (category === 'code' || category === 'config' || (category === 'document' && ext !== 'pdf' && ext !== 'docx' && ext !== 'rtf'));
      const isInlineImage = category === 'image' && file.size <= MAX_INLINE;
      const canInline = (isInlineText && file.size <= MAX_INLINE) || isInlineImage;

      if (canInline) {
        // Inline path: read content immediately
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
        // HTTP upload path: add pending entry, then start uploading
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
  }

  function removeFile(index: number): void {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
  }

  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes}B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
  }

  function send(customText?: string): void {
    const text = (customText || input).trim();
    if (!text && attachedFiles.length === 0) return;

    const fileMetas: AttachmentMeta[] = attachedFiles.map((f) => ({
      name: f.name,
      size: f.size,
      type: f.type,
      category: f.category,
      fileId: f.fileId,
    }));

    // Full content sent to AI (includes file contents for analysis)
    let aiContent = text;
    if (attachedFiles.length > 0) {
      const fileBlocks = attachedFiles.map((f) => {
        const ext = f.name.split('.').pop()?.toLowerCase() || '';
        const isImg = /^(png|jpg|jpeg|gif|svg|webp|bmp)$/i.test(ext);
        if (f.fileId && !f.content) {
          return `[Attached File: ${f.name} (fileId: ${f.fileId})]`;
        }
        if (isImg) {
          return `[Attached Image: ${f.name}]`;
        }
        return `[Attached File: ${f.name}]\n\`\`\`${ext}\n${f.content || ''}\n\`\`\``;
      }).join('\n\n');
      aiContent = text ? `${text}\n\n---\n${fileBlocks}` : fileBlocks;
    }

    // Display content shown in chat bubble (clean, no raw file content)
    const displayContent = text || (attachedFiles.length > 0 ? `发送了 ${attachedFiles.length} 个文件` : '');

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
      attachments: attachedFiles,
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
  }

  function retryMessage(msg: PendingMessage): void {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
      setPending((prev) => prev.filter((item) => item.timestamp !== msg.timestamp));
    } else {
      retryRef.current.push(msg);
      setNotice('WebSocket not connected, waiting for reconnect');
    }
  }

  async function openPreview(): Promise<void> {
    const res = await fetch('/api/preview/local-task', { headers: authHeaders() });
    const data = await res.json();
    setPreviewUrl((data.url as string) || '');
    setPreviewOpen(true);
  }

  async function confirmCommit(): Promise<void> {
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
  }

  function renderMessage(msg: Message, index: number): JSX.Element {
    const isUser = msg.sender === user?.name || msg.sender === 'user';
    const isCode = msg.type === 'code' || msg.type === 'diff';
    const badge = msg.type || 'text';
    const showCursor = msg.isStreaming;

    if (isCode) {
      return (
        <div key={`${msg.timestamp}-${index}`} className="-mx-6 mb-4 px-6">
          <div className="mb-2 flex items-center gap-2 text-xs text-warm-500">
            <span className="font-semibold text-warm-700">{msg.sender || 'agent'}</span>
            <span className="tag tag-warm">{badge}</span>
            {showCursor && <span className="inline-block h-4 w-0.5 animate-pulse bg-primary-500" />}
          </div>
          <DiffBubble value={msg.content} />
          {msg.fidelityScore ? <FidelityScore score={msg.fidelityScore} /> : null}
        </div>
      );
    }
    return (
      <div key={`${msg.timestamp}-${index}`} className={`mb-4 flex ${isUser ? 'justify-end' : 'justify-start'}`}>
        <div className={`max-w-[85%] rounded-2xl px-4 py-3 shadow-sm ${isUser ? 'bg-primary-500 text-white' : 'bg-white text-warm-800 border border-warm-150'}`}>
          <div className="mb-1 flex items-center gap-2 text-xs opacity-80">
            <span className="font-semibold">{msg.sender || 'agent'}</span>
            <span className={`rounded px-2 py-0.5 ${isUser ? 'bg-white/20 text-white' : 'bg-warm-100 text-warm-600'}`}>{badge}</span>
            {showCursor && <span className="inline-block h-4 w-0.5 animate-pulse bg-primary-500" />}
          </div>
          {isUser ? (
            <div className="whitespace-pre-wrap leading-7">
              {msg.content}
              {msg.attachments && msg.attachments.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5 border-t border-white/20 pt-2">
                  {msg.attachments.map((f, i) => (
                    <span key={i} className="inline-flex items-center gap-1 rounded bg-white/20 px-2 py-0.5 text-xs">
                      <FileIcon category={f.category || 'unknown'} size={3} />
                      <span className="max-w-[140px] truncate">{f.name}</span>
                      <span className="opacity-70">{formatSize(f.size)}</span>
                    </span>
                  ))}
                </div>
              )}
              {showCursor && <span className="ml-0.5 inline-block h-5 w-0.5 animate-pulse bg-primary-500 align-text-bottom" />}
            </div>
          ) : (
            <div className="leading-7">
              {parseThinkSegments(normalizeStructuredStreamContent(msg.content)).map((seg, si) =>
                seg.type === 'think' ? (
                  <ThinkingPanel key={si} content={seg.content} isStreaming={!!showCursor} isComplete={seg.isComplete} />
                ) : (
                  <div key={si}>
                    {seg.content.includes('【正式回复】') ? null : <div className="mb-2 text-xs font-semibold text-warm-500">【正式回复】</div>}
                    <MarkdownRenderer content={seg.content.replace('【正式回复】\n', '')} />
                  </div>
                ),
              )}
              {showCursor && <span className="ml-0.5 inline-block h-5 w-0.5 animate-pulse bg-primary-500 align-text-bottom" />}
            </div>
          )}
          {!isUser && msg.fidelityScore ? <FidelityScore score={msg.fidelityScore} /> : null}
        </div>
      </div>
    );
  }

  if (!token) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-warm-50">
        <form onSubmit={submitAuth} className="card w-96 p-8">
          <h1 className="text-h1 text-warm-800">AgentHub {authMode === 'login' ? 'Login' : 'Register'}</h1>
          <p className="mt-2 text-caption text-warm-500">Default admin: admin / admin123</p>
          {notice && <div className="mt-4 rounded-lg bg-warning-50 p-3 text-sm text-warning-600">{notice}</div>}
          <label className="mt-6 block text-h4 text-warm-700">
            Username
            <input className="input-field mt-2" value={authForm.name} onChange={(e) => setAuthForm({ ...authForm, name: e.target.value })} />
          </label>
          <label className="mt-5 block text-h4 text-warm-700">
            Password
            <input type="password" className="input-field mt-2" value={authForm.password} onChange={(e) => setAuthForm({ ...authForm, password: e.target.value })} />
          </label>
          <button className="btn-primary mt-6 w-full">{authMode === 'login' ? 'Login' : 'Register'}</button>
          <button type="button" className="btn-ghost mt-3 w-full text-primary-500" onClick={() => setAuthMode(authMode === 'login' ? 'register' : 'login')}>
            {authMode === 'login' ? 'No account? Register' : 'Already have an account? Login'}
          </button>
        </form>
      </div>
    );
  }

  const percent = dag.total ? Math.round((dag.completed / dag.total) * 100) : 0;

  return (
    <div className="flex h-screen bg-warm-50 text-warm-800">
      <aside className="w-80 border-r border-warm-150 bg-white p-4 flex h-screen flex-col">
        <div className="mb-4">
          <div className="text-h2 text-warm-800">AgentHub</div>
          <div className="mt-1 text-caption text-warm-500">{user?.name} / {user?.role}</div>
        </div>
        <a className="btn-secondary block w-full text-center" href="/admin">管理面板</a>
        <a className="btn-secondary mt-2 block w-full text-center" href="/canvas">智能体画布</a>
        <button className="btn-ghost mt-2 w-full" onClick={logout}>退出登录</button>
        {notice && <div className="mt-3 rounded-lg bg-warning-50 p-2 text-xs text-warning-600">{notice}</div>}
        <div className="mb-3 mt-4 flex items-center justify-between border-b border-warm-150 pb-3">
          <button className="btn-ghost flex items-center gap-2" onClick={createSession}><span className="text-lg">+</span><span>New Session</span></button>
        </div>
        <div className="mb-3 flex items-center gap-2 rounded-xl border border-warm-150 bg-warm-50 px-3 py-2">
          <span className="text-warm-400">Search</span>
          <input className="w-full bg-transparent text-sm outline-none" placeholder="Search sessions..." value={sessionQuery} onChange={(e) => setSessionQuery(e.target.value)} />
        </div>
        <div className="mb-2 text-xs text-warm-500">Recent 30 days</div>
        <div className="flex-1 overflow-hidden">
          <div className="h-full space-y-1 overflow-auto pr-1">
          {filteredSessions.map((s) => (
            <div key={s.id} className={`group flex items-center gap-1 rounded-lg px-2 py-1 ${s.id === sessionId ? 'bg-warm-100' : 'hover:bg-warm-50'}`}>
              {editingId === s.id ? (
                <input
                  className="flex-1 rounded border border-primary-300 px-2 py-1 text-sm outline-none focus:ring-1 focus:ring-primary-500"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') renameSession(s.id); if (e.key === 'Escape') setEditingId(''); }}
                  onBlur={() => renameSession(s.id)}
                  autoFocus
                />
              ) : (
                <button className={`flex-1 rounded-lg px-2 py-2 text-left text-sm ${s.id === sessionId ? 'text-warm-800' : 'text-warm-600'}`} onClick={() => selectSession(s.id)}>
                  <div className="flex items-center gap-1.5 truncate">
                    {s.isPinned ? <span className="shrink-0 text-amber-500" title="Pinned">📌</span> : null}
                    <span className="truncate">{s.name || 'Untitled'}</span>
                  </div>
                </button>
              )}
              <button className="invisible rounded p-1 text-warm-400 transition hover:bg-white hover:text-amber-500 group-hover:visible" title="Pin session" onClick={() => togglePin(s.id, s.isPinned || 0)}>
                <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill={s.isPinned ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 2l3 7h5l-4 6 1 7-5-3-5 3 1-7-4-6h5z" />
                </svg>
              </button>
              <button className="invisible rounded p-1 text-warm-400 transition hover:bg-white hover:text-primary-500 group-hover:visible" title="Rename session" onClick={() => startRename(s)}>
                <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />
                </svg>
              </button>
              <button className="invisible rounded p-1 text-warm-400 transition hover:bg-white hover:text-danger-500 group-hover:visible" title="Delete session" onClick={() => deleteSession(s.id)}>
                <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M3 6h18" />
                  <path d="M8 6V4h8v2" />
                  <path d="M19 6l-1 14H6L5 6" />
                  <path d="M10 11v6" />
                  <path d="M14 11v6" />
                </svg>
              </button>
            </div>
          ))}
          {!sessions.length && <div className="rounded-lg bg-warm-50 px-3 py-2 text-sm text-warm-500">No sessions, click &quot;New Session&quot;</div>}
          </div>
        </div>
      </aside>

      <main className="flex flex-1 flex-col">
        <header className="border-b border-warm-150 bg-white px-6 py-4">
          <div className="flex items-center justify-between gap-6">
            <div>
              <div className="text-h3 text-warm-800">{sessions.find((s) => s.id === sessionId)?.name || 'New Session'}</div>
              <div className="text-caption text-warm-500 mt-0.5">
                WebSocket: {connected ? (isStreaming ? 'AI streaming...' : 'Connected') : 'Reconnecting'}
              </div>
            </div>
            <div className="min-w-[420px]">
              <div className="mb-1.5 flex justify-between text-caption text-warm-500">
                <button onClick={() => setTaskOpen(true)} className="text-primary-500 hover:text-primary-600">DAG Progress / View Tasks</button>
                <span>{percent}%</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-warm-100">
                <div className="h-full bg-primary-500 transition-all duration-300" style={{ width: `${percent}%` }} />
              </div>
            </div>
          </div>
        </header>

        <section ref={messagesContainerRef} className="flex-1 overflow-auto p-6">
          {messages.map(renderMessage)}
          {generated && <GeneratedFilesPanel generated={generated} onCommit={confirmCommit} />}
          <div ref={bottomRef} />
        </section>

        <footer className="relative border-t border-warm-150 bg-white px-6 py-4">
          {mentionOpen && mentionTriggerRef.current === '@' && (
            <div ref={mentionPanelRef} className="absolute bottom-24 left-6 z-20 w-[520px] rounded-xl border border-warm-150 bg-white p-3 shadow-modal">
              <div className="mb-2 flex items-center justify-between text-caption text-warm-500">
                <span>@ Select Agent</span>
                <button className="text-primary-500" onClick={insertAllMentions}>@All Agents</button>
              </div>
              <div className="mb-2">
                <input
                  type="text"
                  placeholder="搜索agent..."
                  value={mentionSearch}
                  onChange={(e) => { setMentionSearch(e.target.value); setMentionActiveIndex(0); }}
                  className="w-full rounded-lg border border-warm-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
                />
              </div>
              <div className="mb-2 flex gap-1">
                {['all', 'L1', 'L2', 'L3'].map((level) => (
                  <button
                    key={level}
                    onClick={() => { setSelectedRiskLevel(level); setMentionActiveIndex(0); }}
                    className={`rounded-md px-2 py-1 text-xs ${
                      selectedRiskLevel === level
                        ? 'bg-primary-500 text-white'
                        : 'bg-warm-100 text-warm-600 hover:bg-warm-200'
                    }`}
                  >
                    {level === 'all' ? '全部' : level}
                  </button>
                ))}
              </div>
              <div className="max-h-60 overflow-y-auto">
                <div className="grid grid-cols-2 gap-2">
                  {filteredAgents.length === 0 ? (
                    <div className="col-span-2 py-4 text-center text-sm text-warm-400">No matching agents</div>
                  ) : (
                    filteredAgents.map((agent, idx) => (
                      <button
                        key={agent.agentId}
                        className={`rounded-lg px-3 py-2 text-left border ${
                          idx === mentionActiveIndex
                            ? 'bg-primary-50 border-primary-300 ring-1 ring-primary-300'
                            : 'bg-warm-50 border-transparent hover:bg-primary-50'
                        }`}
                        onClick={() => insertMention(agent.agentId)}
                        onMouseEnter={() => setMentionActiveIndex(idx)}
                      >
                        <div className="font-medium text-warm-700">@{agent.agentId}</div>
                        <div className="text-caption text-warm-500">{agent.domain} / {agent.rankLevel || 'L1'}</div>
                      </button>
                    ))
                  )}
                </div>
              </div>
            </div>
          )}

          {mentionOpen && mentionTriggerRef.current === '#' && (
            <div ref={mentionPanelRef} className="absolute bottom-24 left-6 z-20 w-[520px] rounded-xl border border-warm-150 bg-white p-3 shadow-modal">
              <div className="mb-2 flex items-center justify-between text-caption text-warm-500">
                <span># Select Workflow</span>
                <span className="text-warm-400">{filteredWorkflows.length} workflows</span>
              </div>
              <div className="mb-2">
                <input
                  type="text"
                  placeholder="搜索工作流..."
                  value={mentionSearch}
                  onChange={(e) => { setMentionSearch(e.target.value); setMentionActiveIndex(0); }}
                  className="w-full rounded-lg border border-warm-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
                />
              </div>
              <div className="max-h-60 overflow-y-auto">
                <div className="space-y-1">
                  {filteredWorkflows.length === 0 ? (
                    <div className="py-4 text-center text-sm text-warm-400">No matching workflows</div>
                  ) : (
                    filteredWorkflows.map((wf, idx) => (
                      <button
                        key={wf.routeId}
                        className={`w-full rounded-lg px-3 py-2 text-left border ${
                          idx === mentionActiveIndex
                            ? 'bg-primary-50 border-primary-300 ring-1 ring-primary-300'
                            : 'bg-warm-50 border-transparent hover:bg-primary-50'
                        }`}
                        onClick={() => insertWorkflow(wf)}
                        onMouseEnter={() => setMentionActiveIndex(idx)}
                      >
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-warm-800">#{wf.name}</span>
                          <span className="text-xs text-warm-400">{wf.description.slice(0, 40)}{wf.description.length > 40 ? '...' : ''}</span>
                        </div>
                        {wf.triggerKeywords.length > 0 && (
                          <div className="mt-1 flex flex-wrap gap-1">
                            {wf.triggerKeywords.map((k) => (
                              <span key={k} className="rounded bg-warm-100 px-1.5 py-0.5 text-xs text-warm-500">{k}</span>
                            ))}
                          </div>
                        )}
                      </button>
                    ))
                  )}
                </div>
              </div>
            </div>
          )}
          <div className="flex gap-3">
            <div className="flex flex-1 flex-col gap-2">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={handleInput}
                onBlur={handleBlur}
                onKeyDown={handleTextareaKeyDown}
                rows={3}
                className="input-field w-full resize-none"
                placeholder={isStreaming ? 'AI is streaming, new message will interrupt current output...' : 'Type message, supports @Agent directives...'}
              />
              {attachedFiles.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {attachedFiles.map((f, i) => (
                    <span key={`${f.name}-${i}`} className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs border ${
                      f.uploadStatus === 'error' ? 'bg-danger-50 border-danger-200 text-danger-700' :
                      f.uploadStatus === 'uploading' ? 'bg-primary-50 border-primary-200 text-warm-700' :
                      'bg-warm-100 border-warm-150 text-warm-700'
                    }`}>
                      <FileIcon category={f.category} size={3.5} />
                      <span className="max-w-[140px] truncate">{f.name}</span>
                      <span className="text-warm-400">{formatSize(f.size)}</span>
                      {f.uploadStatus === 'uploading' && (
                        <span className="flex items-center gap-1 text-primary-600">
                          <span className="h-2 w-12 overflow-hidden rounded-full bg-primary-100">
                            <span className="block h-full rounded-full bg-primary-500 transition-all" style={{ width: `${f.uploadProgress || 0}%` }} />
                          </span>
                          <span className="text-[10px]">{f.uploadProgress || 0}%</span>
                        </span>
                      )}
                      {f.uploadStatus === 'error' && (
                        <span className="text-[10px] text-danger-500" title={f.uploadError}>失败</span>
                      )}
                      {f.uploadStatus !== 'uploading' && (
                        <button className="ml-0.5 text-warm-400 hover:text-danger-500" onClick={() => removeFile(i)} title="Remove">
                          <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                        </button>
                      )}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <div className="flex flex-col gap-2">
              <button className="btn-primary" onClick={() => send()}>Send</button>
              <button className="btn-secondary" onClick={openPreview}>Preview</button>
              <label className="btn-ghost flex cursor-pointer items-center justify-center p-2" title="Attach file">
                <svg className="h-5 w-5 text-warm-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
                <input ref={fileInputRef} type="file" multiple className="sr-only" onChange={handleFileChange} accept={ACCEPT_STRING} />
              </label>
            </div>
          </div>
        </footer>
      </main>

      {taskOpen && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-warm-900/20">
          <div className="w-[520px] rounded-xl bg-white p-6 shadow-modal">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-h3 text-warm-800">DAG Task Details</h3>
              <button className="btn-ghost p-1 text-warm-500" onClick={() => setTaskOpen(false)}>X</button>
            </div>
            <div className="space-y-3">
              {dag.nodes.map((n, i) => (
                <div key={n.id || i} className="flex items-center gap-3 rounded-lg bg-warm-50 px-4 py-3">
                  <span className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-medium ${
                    n.status === 'completed' ? 'bg-success-50 text-success-500' :
                    n.status === 'running' ? 'bg-primary-50 text-primary-500' :
                    'bg-warm-100 text-warm-500'
                  }`}>
                    {n.status === 'completed' ? 'OK' : n.status === 'running' ? 'R' : i + 1}
                  </span>
                  <span className="text-body flex-1 text-warm-700">{n.agent || n.name || `Task ${i + 1}`}</span>
                  <span className="tag tag-warm">{n.status || 'pending'}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      <PreviewSidebar open={previewOpen} onClose={() => setPreviewOpen(false)} previewUrl={previewUrl} />
    </div>
  );
}
