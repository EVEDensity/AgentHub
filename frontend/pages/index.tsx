import { useEffect, useRef, useState, type JSX } from 'react';
import DiffBubble from '../components/chat/DiffBubble';
import MarkdownRenderer from '../components/chat/MarkdownRenderer';
import GeneratedFilesPanel from '../components/git/GeneratedFilesPanel';
import FidelityScore from '../components/chat/FidelityScore';
import PreviewSidebar from '../components/shared/PreviewSidebar';
import type { Agent, GeneratedData, Message, PendingMessage, StreamChunk, User } from '../types';

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
}

interface DagState {
  total: number;
  completed: number;
  nodes: Array<{ id?: string; name?: string; status?: string; agent?: string; description?: string; dependencies?: string[] }>;
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
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [editingId, setEditingId] = useState<string>('');
  const [editName, setEditName] = useState<string>('');
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<PendingMessage[]>([]);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const currentSessionRef = useRef<string>(sessionId);

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
        setSessions(data);
        if (!data.find((s) => s.id === sessionId) && data.length) {
          setSessionId(data[0].id);
        }
      })
      .catch(() => {});
    fetch('/api/agent/registry', { headers: authHeaders() })
      .then((r) => r.json())
      .then((data: Agent[]) => setAgents(data.length ? data : FALLBACK_AGENTS))
      .catch(() => setAgents(FALLBACK_AGENTS));
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
          return [...prev, ...newMessages].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
        });
      } else {
        setMessages(data);
      }
    } catch { /* ignore */ }
  }

  useEffect(() => {
    if (!token || !sessionId) return;
    void reloadMessages(false);
    connectWs();
    return () => wsRef.current?.close();
  }, [token, sessionId]);

  useEffect(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), [messages]);

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
        setMessages((prev) => {
          const idx = prev.findIndex((m) => m.messageId === chunk.messageId);
          if (idx >= 0) {
            const updated = [...prev];
            updated[idx] = {
              ...updated[idx],
              content: updated[idx].content + chunk.content,
              isStreaming: !chunk.isFinal,
            };
            return updated;
          }
          const newMsg: Message = {
            event: 'message',
            sessionId: chunk.sessionId,
            sender: 'agent',
            content: chunk.content,
            type: 'text',
            timestamp: new Date().toISOString(),
            messageId: chunk.messageId,
            isStreaming: !chunk.isFinal,
          };
          return [...prev, newMsg];
        });
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
        setMessages((prev) => {
          const lastIdx = prev.length - 1;
          if (lastIdx >= 0 && prev[lastIdx].isStreaming && prev[lastIdx].messageId) {
            const updated = [...prev];
            updated[lastIdx] = { ...msg, messageId: undefined, isStreaming: false };
            return updated;
          }
          return [...prev, { ...msg, messageId: undefined, isStreaming: false }];
        });
        if (msg.symbolic?.generated) setGenerated(msg.symbolic.generated as GeneratedData);
      }
    };
  }

  function insertMention(agentId: string): void {
    const mention = `@${agentId}`;
    setInput((prev) => (prev.includes(mention) ? prev : `${mention} ${prev}`));
    setMentionOpen(false);
  }

  function insertAllMentions(): void {
    const mentions = agents.map((agent) => `@${agent.agentId}`).join(' ');
    setInput((prev) => `${mentions} ${prev}`);
    setMentionOpen(false);
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
    setSessions((prev) => [created, ...prev]);
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
      updated.sort((a, b) => (b.isPinned || 0) - (a.isPinned || 0) || (b.createdAt || '').localeCompare(a.createdAt || ''));
      return updated;
    });
  }

  function startRename(s: ChatSession): void {
    setEditingId(s.id);
    setEditName(s.name);
  }

  function send(customText?: string): void {
    const text = (customText || input).trim();
    if (!text) return;
    const msg: PendingMessage = {
      sessionId,
      content: text,
      sender: user?.name || 'user',
      timestamp: new Date().toISOString(),
      type: 'text',
    };
    if (isStreaming) {
      setIsStreaming(false);
    }
    setMessages((prev) => [...prev, { ...msg, event: 'message' }]);
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    } else {
      retryRef.current.push(msg);
      setPending((prev) => [...prev, msg]);
      setNotice('Message queued for retry');
    }
    setInput('');
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
              {showCursor && <span className="ml-0.5 inline-block h-5 w-0.5 animate-pulse bg-primary-500 align-text-bottom" />}
            </div>
          ) : (
            <div className="leading-7">
              <MarkdownRenderer content={msg.content} />
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
          {sessions.filter((s) => !sessionQuery.trim() || s.name.toLowerCase().includes(sessionQuery.toLowerCase())).map((s) => (
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

        <section className="flex-1 overflow-auto p-6">
          {messages.map(renderMessage)}
          {generated && <GeneratedFilesPanel generated={generated} onCommit={confirmCommit} />}
          <div ref={bottomRef} />
        </section>

        <footer className="relative border-t border-warm-150 bg-white px-6 py-4">
          {mentionOpen && (
            <div className="absolute bottom-24 left-6 z-20 w-[520px] rounded-xl border border-warm-150 bg-white p-3 shadow-modal">
              <div className="mb-2 flex items-center justify-between text-caption text-warm-500">
                <span>@ Select Agent</span>
                <button className="text-primary-500" onClick={insertAllMentions}>@All Agents</button>
              </div>
              <div className="mb-2">
                <input
                  type="text"
                  placeholder="搜索agent..."
                  value={mentionSearch}
                  onChange={(e) => setMentionSearch(e.target.value)}
                  className="w-full rounded-lg border border-warm-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
                />
              </div>
              <div className="mb-2 flex gap-1">
                {['all', 'L1', 'L2', 'L3'].map((level) => (
                  <button
                    key={level}
                    onClick={() => setSelectedRiskLevel(level)}
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
                  {agents
                    .filter((agent) => {
                      const matchesSearch = mentionSearch === '' ||
                        agent.agentId.toLowerCase().includes(mentionSearch.toLowerCase()) ||
                        agent.domain.toLowerCase().includes(mentionSearch.toLowerCase());
                      const matchesLevel = selectedRiskLevel === 'all' || agent.rankLevel === selectedRiskLevel;
                      return matchesSearch && matchesLevel;
                    })
                    .map((agent) => (
                      <button
                        key={agent.agentId}
                        className="rounded-lg bg-warm-50 px-3 py-2 text-left hover:bg-primary-50"
                        onClick={() => insertMention(agent.agentId)}
                      >
                        <div className="font-medium text-warm-700">@{agent.agentId}</div>
                        <div className="text-caption text-warm-500">{agent.domain} / {agent.rankLevel || 'L1'}</div>
                      </button>
                    ))}
                </div>
              </div>
            </div>
          )}
          <div className="flex gap-3">
            <button className="btn-secondary self-start" onClick={() => setMentionOpen((v) => !v)}>@</button>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
              rows={3}
              className="input-field flex-1 resize-none"
              placeholder={isStreaming ? 'AI is streaming, new message will interrupt current output...' : 'Type message, supports @Agent directives...'}
            />
            <div className="flex flex-col gap-2">
              <button className="btn-primary" onClick={() => send()}>Send</button>
              <button className="btn-secondary" onClick={openPreview}>Preview</button>
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
