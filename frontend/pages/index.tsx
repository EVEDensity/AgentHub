import { useEffect, useRef, useState, type JSX } from 'react';
import DiffBubble from '../components/DiffBubble';
import GeneratedFilesPanel from '../components/GeneratedFilesPanel';
import FidelityScore from '../components/FidelityScore';
import PreviewSidebar from '../components/PreviewSidebar';
import type { GeneratedData, Message, PendingMessage, User } from '../types';

const AGENTS = ['Orchestrator', 'Architect', 'CodeGen', 'Review', 'Test', 'Deploy'] as const;

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
  const [input, setInput] = useState<string>('@CodeGen 生成一个 FastAPI health 路由文件，保存为 health_router.py');
  const [dag, setDag] = useState<DagState>({ total: 0, completed: 0, nodes: [] });
  const [taskOpen, setTaskOpen] = useState<boolean>(false);
  const [previewOpen, setPreviewOpen] = useState<boolean>(false);
  const [previewUrl, setPreviewUrl] = useState<string>('');
  const [connected, setConnected] = useState<boolean>(false);
  const [notice, setNotice] = useState<string>('');
  const [pending, setPending] = useState<PendingMessage[]>([]);
  const [generated, setGenerated] = useState<GeneratedData | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<PendingMessage[]>([]);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const saved = localStorage.getItem('agenthub_token');
    const savedUser = localStorage.getItem('agenthub_user');
    if (saved) setToken(saved);
    if (savedUser) setUser(JSON.parse(savedUser) as User);
  }, []);

  useEffect(() => {
    if (!token) return;
    fetch('/api/chat/sessions/session-1/messages', { headers: authHeaders() })
      .then((r) => r.json())
      .then((data: Message[]) => setMessages(data))
      .catch(() => {});
    connectWs();
    return () => wsRef.current?.close();
  }, [token]);

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
      setNotice(data.detail || '认证失败');
      return;
    }
    localStorage.setItem('agenthub_token', data.accessToken);
    localStorage.setItem('agenthub_user', JSON.stringify(data.user));
    setToken(data.accessToken as string);
    setUser(data.user as User);
    setNotice('登录成功');
  }

  function logout(): void {
    localStorage.removeItem('agenthub_token');
    localStorage.removeItem('agenthub_user');
    wsRef.current?.close();
    setToken('');
    setUser(null);
  }

  function connectWs(): void {
    if (reconnectRef.current) clearTimeout(reconnectRef.current);
    const ws = new WebSocket(`ws://127.0.0.1:8000/ws/session-1?token=${encodeURIComponent(token)}`);
    wsRef.current = ws;
    ws.onopen = () => {
      setConnected(true);
      setNotice('WebSocket 已连接');
      const queued = [...retryRef.current];
      retryRef.current = [];
      setPending([]);
      queued.forEach((msg) => ws.send(JSON.stringify(msg)));
    };
    ws.onclose = () => {
      setConnected(false);
      reconnectRef.current = setTimeout(connectWs, 1500);
    };
    ws.onerror = () => setConnected(false);
    ws.onmessage = (event: MessageEvent<string>) => {
      const data = JSON.parse(event.data) as Message & { total?: number; completed?: number; nodes?: DagState['nodes']; event?: string };
      if (data.event === 'task_update') setDag({ total: data.total || 0, completed: data.completed || 0, nodes: data.nodes || [] });
      if (data.event === 'message') {
        setMessages((prev) => [...prev, data]);
        if (data.symbolic?.generated) setGenerated(data.symbolic.generated as GeneratedData);
      }
    };
  }

  function send(customText?: string): void {
    const text = (customText || input).trim();
    if (!text) return;
    const msg: PendingMessage = {
      sessionId: 'session-1',
      content: text,
      sender: user?.name || 'user',
      timestamp: new Date().toISOString(),
      type: 'text',
    };
    setMessages((prev) => [...prev, { ...msg, event: 'message' }]);
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    } else {
      retryRef.current.push(msg);
      setPending((prev) => [...prev, msg]);
      setNotice('消息已进入失败重试队列，连接恢复后自动发送');
    }
    setInput('');
  }

  function retryMessage(msg: PendingMessage): void {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
      setPending((prev) => prev.filter((item) => item.timestamp !== msg.timestamp));
    } else {
      retryRef.current.push(msg);
      setNotice('WebSocket 未连接，继续等待自动重连');
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
        sessionId: 'session-1',
        message: '确认提交 CodeGen 生成文件',
        paths: generated.files,
      }),
    });
    const data = await res.json();
    setNotice(res.ok ? `已提交：${data.commit_hash || data.message}` : data.detail || '提交失败');
  }

  function renderMessage(msg: Message, index: number): JSX.Element {
    const isUser = msg.sender === user?.name || msg.sender === 'user';
    const isCode = msg.type === 'code' || msg.type === 'diff';
    const badge = msg.type || 'text';
    if (isCode) {
      return (
        <div key={`${msg.timestamp}-${index}`} className="-mx-6 mb-4 px-6">
          <div className="mb-2 flex items-center gap-2 text-xs text-warm-500">
            <span className="font-semibold text-warm-700">{msg.sender || 'agent'}</span>
            <span className="tag tag-warm">{badge}</span>
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
          </div>
          <div className="whitespace-pre-wrap leading-7">{msg.content}</div>
          {!isUser && msg.fidelityScore ? <FidelityScore score={msg.fidelityScore} /> : null}
        </div>
      </div>
    );
  }

  if (!token) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-warm-50">
        <form onSubmit={submitAuth} className="card w-96 p-8">
          <h1 className="text-h1 text-warm-800">AgentHub {authMode === 'login' ? '登录' : '注册'}</h1>
          <p className="mt-2 text-caption text-warm-500">默认管理员：admin / admin123</p>
          {notice && <div className="mt-4 rounded-lg bg-warning-50 p-3 text-sm text-warning-600">{notice}</div>}
          <label className="mt-6 block text-h4 text-warm-700">
            用户名
            <input className="input-field mt-2" value={authForm.name} onChange={(e) => setAuthForm({ ...authForm, name: e.target.value })} />
          </label>
          <label className="mt-5 block text-h4 text-warm-700">
            密码
            <input type="password" className="input-field mt-2" value={authForm.password} onChange={(e) => setAuthForm({ ...authForm, password: e.target.value })} />
          </label>
          <button className="btn-primary mt-6 w-full">{authMode === 'login' ? '登录' : '注册'}</button>
          <button type="button" className="btn-ghost mt-3 w-full text-primary-500" onClick={() => setAuthMode(authMode === 'login' ? 'register' : 'login')}>
            {authMode === 'login' ? '没有账号？注册' : '已有账号？登录'}
          </button>
        </form>
      </div>
    );
  }

  const percent = dag.total ? Math.round((dag.completed / dag.total) * 100) : 0;

  return (
    <div className="flex h-screen bg-warm-50 text-warm-800">
      <aside className="w-72 border-r border-warm-150 bg-white p-6">
        <div className="mb-6">
          <div className="text-h2 text-warm-800">AgentHub</div>
          <div className="mt-1 text-caption text-warm-500">{user?.name} / {user?.role}</div>
        </div>
        <a className="btn-secondary block w-full text-center" href="/admin">管理控制台</a>
        <button className="btn-ghost mt-2 w-full" onClick={logout}>退出登录</button>
        {notice && <div className="mt-4 rounded-lg bg-warning-50 p-3 text-sm text-warning-600">{notice}</div>}
        <div className="mt-6 rounded-xl bg-warm-50 p-4">
          <div className="mb-3 text-h4 text-warm-700">可 @ Agent</div>
          <div className="flex flex-wrap gap-2">
            {AGENTS.map((a) => (
              <button key={a} className="tag tag-blue" onClick={() => setInput(`@${a} ${input}`)}>{a}</button>
            ))}
          </div>
        </div>
        {pending.length > 0 && (
          <div className="mt-6 rounded-xl bg-danger-50 p-4">
            <div className="mb-2 text-h4 text-danger-500">失败重试队列</div>
            {pending.map((m) => (
              <button key={m.timestamp} className="mb-2 block text-left text-xs text-danger-500 underline" onClick={() => retryMessage(m)}>{m.content.slice(0, 28)}...</button>
            ))}
          </div>
        )}
      </aside>

      <main className="flex flex-1 flex-col">
        <header className="border-b border-warm-150 bg-white px-6 py-4">
          <div className="flex items-center justify-between gap-6">
            <div>
              <div className="text-h3 text-warm-800">IM 协作入口</div>
              <div className="text-caption text-warm-500 mt-0.5">WebSocket：{connected ? '已连接' : '重连中'}</div>
            </div>
            <div className="min-w-[420px]">
              <div className="mb-1.5 flex justify-between text-caption text-warm-500">
                <button onClick={() => setTaskOpen(true)} className="text-primary-500 hover:text-primary-600">DAG 进度 / 查看任务详情</button>
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

        <footer className="border-t border-warm-150 bg-white px-6 py-4">
          <div className="flex gap-3">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
              rows={3}
              className="input-field flex-1 resize-none"
              placeholder="输入消息，支持 @Agent 指令..."
            />
            <div className="flex flex-col gap-2">
              <button className="btn-primary" onClick={() => send()}>发送</button>
              <button className="btn-secondary" onClick={openPreview}>预览</button>
            </div>
          </div>
        </footer>
      </main>

      {taskOpen && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-warm-900/20">
          <div className="w-[520px] rounded-xl bg-white p-6 shadow-modal">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-h3 text-warm-800">DAG 任务详情</h3>
              <button className="btn-ghost p-1 text-warm-500" onClick={() => setTaskOpen(false)}>✕</button>
            </div>
            <div className="space-y-3">
              {dag.nodes.map((n, i) => (
                <div key={n.id || i} className="flex items-center gap-3 rounded-lg bg-warm-50 px-4 py-3">
                  <span className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-medium ${
                    n.status === 'completed' ? 'bg-success-50 text-success-500' :
                    n.status === 'running' ? 'bg-primary-50 text-primary-500' :
                    'bg-warm-100 text-warm-500'
                  }`}>
                    {n.status === 'completed' ? '✓' : n.status === 'running' ? '●' : i + 1}
                  </span>
                  <span className="text-body flex-1 text-warm-700">{n.agent || n.name || `任务 ${i + 1}`}</span>
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