import { useEffect, useRef, useState } from 'react';
import DiffBubble from '../components/DiffBubble';
import GeneratedFilesPanel from '../components/GeneratedFilesPanel';
import FidelityScore from '../components/FidelityScore';
import PreviewSidebar from '../components/PreviewSidebar';

const AGENTS = ['Orchestrator', 'Architect', 'CodeGen', 'Review', 'Test', 'Deploy'];
const API = '';

export default function AgentHubIM() {
  const [token, setToken] = useState('');
  const [user, setUser] = useState(null);
  const [authMode, setAuthMode] = useState('login');
  const [authForm, setAuthForm] = useState({ name: 'admin', password: 'admin123' });
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('@CodeGen 生成一个 FastAPI health 路由文件，保存为 health_router.py');
  const [dag, setDag] = useState({ total: 0, completed: 0, nodes: [] });
  const [taskOpen, setTaskOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewUrl, setPreviewUrl] = useState('');
  const [connected, setConnected] = useState(false);
  const [notice, setNotice] = useState('');
  const [pending, setPending] = useState([]);
  const [generated, setGenerated] = useState(null);
  const wsRef = useRef(null);
  const retryRef = useRef([]);
  const reconnectRef = useRef(null);
  const bottomRef = useRef(null);

  useEffect(() => {
    const saved = localStorage.getItem('agenthub_token');
    const savedUser = localStorage.getItem('agenthub_user');
    if (saved) setToken(saved);
    if (savedUser) setUser(JSON.parse(savedUser));
  }, []);

  useEffect(() => {
    if (!token) return;
    fetch('/api/chat/sessions/session-1/messages', { headers: authHeaders() }).then((r) => r.json()).then(setMessages).catch(() => {});
    connectWs();
    return () => wsRef.current?.close();
  }, [token]);

  useEffect(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), [messages]);

  function authHeaders() {
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  async function submitAuth(event) {
    event.preventDefault();
    const res = await fetch(`/api/auth/${authMode}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(authForm) });
    const data = await res.json();
    if (!res.ok) {
      setNotice(data.detail || '认证失败');
      return;
    }
    localStorage.setItem('agenthub_token', data.accessToken);
    localStorage.setItem('agenthub_user', JSON.stringify(data.user));
    setToken(data.accessToken);
    setUser(data.user);
    setNotice('登录成功');
  }

  function logout() {
    localStorage.removeItem('agenthub_token');
    localStorage.removeItem('agenthub_user');
    wsRef.current?.close();
    setToken('');
    setUser(null);
  }

  function connectWs() {
    clearTimeout(reconnectRef.current);
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
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.event === 'task_update') setDag({ total: data.total, completed: data.completed, nodes: data.nodes || [] });
      if (data.event === 'message') {
        setMessages((prev) => [...prev, data]);
        if (data.symbolic?.generated) setGenerated(data.symbolic.generated);
      }
    };
  }

  function send(customText) {
    const text = (customText || input).trim();
    if (!text) return;
    const msg = { sessionId: 'session-1', content: text, sender: user?.name || 'user', timestamp: new Date().toISOString(), type: 'text' };
    setMessages((prev) => [...prev, msg]);
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    } else {
      retryRef.current.push(msg);
      setPending((prev) => [...prev, msg]);
      setNotice('消息已进入失败重试队列，连接恢复后自动发送');
    }
    setInput('');
  }

  function retryMessage(msg) {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
      setPending((prev) => prev.filter((item) => item.timestamp !== msg.timestamp));
    } else {
      retryRef.current.push(msg);
      setNotice('WebSocket 未连接，继续等待自动重连');
    }
  }

  async function openPreview() {
    const res = await fetch('/api/preview/local-task', { headers: authHeaders() });
    const data = await res.json();
    setPreviewUrl(data.url);
    setPreviewOpen(true);
  }

  async function confirmCommit() {
    if (!generated?.files?.length) return;
    const res = await fetch('/api/git/commit', { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ sessionId: 'session-1', message: '确认提交 CodeGen 生成文件', paths: generated.files }) });
    const data = await res.json();
    setNotice(res.ok ? `已提交：${data.commit_hash || data.message}` : data.detail || '提交失败');
  }

  function renderMessage(msg, index) {
    const isUser = msg.sender === user?.name || msg.sender === 'user';
    const badge = msg.type || 'text';
    return <div key={`${msg.timestamp}-${index}`} className={`mb-4 flex ${isUser ? 'justify-end' : 'justify-start'}`}><div className={`max-w-[78%] rounded-2xl px-4 py-3 shadow-sm ${isUser ? 'bg-blue-600 text-white' : 'bg-white text-slate-800'}`}><div className="mb-1 flex items-center gap-2 text-xs opacity-80"><span className="font-semibold">{msg.sender || 'agent'}</span><span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-700">{badge}</span></div>{msg.type === 'code' || msg.type === 'diff' ? <DiffBubble value={msg.content} /> : <div className="whitespace-pre-wrap leading-7">{msg.content}</div>}{!isUser && msg.fidelityScore ? <FidelityScore score={msg.fidelityScore} /> : null}</div></div>;
  }

  if (!token) {
    return <div className="flex min-h-screen items-center justify-center bg-slate-100"><form onSubmit={submitAuth} className="w-96 rounded-2xl bg-white p-8 shadow"><h1 className="text-2xl font-bold">AgentHub {authMode === 'login' ? '登录' : '注册'}</h1><p className="mt-2 text-sm text-slate-500">默认管理员：admin / admin123</p>{notice && <div className="mt-4 rounded-xl bg-amber-50 p-3 text-sm text-amber-700">{notice}</div>}<label className="mt-5 block text-sm font-medium">用户名<input className="mt-2 w-full rounded-lg border px-3 py-2" value={authForm.name} onChange={(e) => setAuthForm({ ...authForm, name: e.target.value })} /></label><label className="mt-4 block text-sm font-medium">密码<input type="password" className="mt-2 w-full rounded-lg border px-3 py-2" value={authForm.password} onChange={(e) => setAuthForm({ ...authForm, password: e.target.value })} /></label><button className="mt-6 w-full rounded-xl bg-blue-600 px-4 py-2 text-white">{authMode === 'login' ? '登录' : '注册'}</button><button type="button" className="mt-3 w-full text-sm text-blue-600" onClick={() => setAuthMode(authMode === 'login' ? 'register' : 'login')}>{authMode === 'login' ? '没有账号？注册' : '已有账号？登录'}</button></form></div>;
  }

  const percent = dag.total ? Math.round((dag.completed / dag.total) * 100) : 0;

  return <div className="flex h-screen bg-slate-100 text-slate-900"><aside className="w-72 border-r bg-white p-5"><div className="mb-6"><div className="text-2xl font-bold">AgentHub</div><div className="mt-1 text-sm text-slate-500">{user?.name} / {user?.role}</div></div><a className="block w-full rounded-xl border px-4 py-2 text-center font-medium hover:bg-slate-50" href="/admin">管理控制台</a><button className="mt-3 w-full rounded-xl border px-4 py-2" onClick={logout}>退出登录</button>{notice && <div className="mt-3 rounded-xl bg-amber-50 p-3 text-sm text-amber-700">{notice}</div>}<div className="mt-6 rounded-2xl bg-slate-50 p-4"><div className="mb-3 font-semibold">可 @ Agent</div><div className="flex flex-wrap gap-2">{AGENTS.map((a) => <button key={a} className="rounded-full bg-blue-50 px-3 py-1 text-sm text-blue-700" onClick={() => setInput(`@${a} ${input}`)}>{a}</button>)}</div></div>{pending.length > 0 && <div className="mt-6 rounded-2xl bg-red-50 p-4"><div className="mb-2 font-semibold text-red-700">失败重试队列</div>{pending.map((m) => <button key={m.timestamp} className="mb-2 block text-left text-xs text-red-700 underline" onClick={() => retryMessage(m)}>{m.content.slice(0, 28)}...</button>)}</div>}</aside><main className="flex flex-1 flex-col"><header className="border-b bg-white px-6 py-4"><div className="flex items-center justify-between gap-6"><div><div className="text-lg font-semibold">IM 协作入口</div><div className="text-sm text-slate-500">WebSocket：{connected ? '已连接' : '重连中'}</div></div><div className="min-w-[420px]"><div className="mb-1 flex justify-between text-xs text-slate-500"><button onClick={() => setTaskOpen(true)} className="underline">DAG 进度 / 查看任务详情</button><span>{percent}%</span></div><div className="h-2 overflow-hidden rounded-full bg-slate-200"><div className="h-full bg-blue-600" style={{ width: `${percent}%` }} /></div></div></div></header><section className="flex-1 overflow-auto p-6">{messages.map(renderMessage)}{generated && <GeneratedFilesPanel generated={generated} onCommit={confirmCommit} />}<div ref={bottomRef} /></section><footer className="border-t bg-white p-4"><div className="flex gap-3"><textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }} rows={3} className="flex-1 rounded-2xl border px-4 py-3 outline-none focus:border-blue-500" /><div className="flex flex-col gap-2"><button className="rounded-xl bg-blue-600 px-5 py-2 text-white" onClick={() => send()}>发送</button><button className="rounded-xl border px-5 py-2" onClick={openPreview}>预览</button></div></div></footer></main>{taskOpen && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"><div className="max-h-[80vh] w-[720px] overflow-auto rounded-2xl bg-white p-6 shadow-2xl"><div className="mb-4 flex justify-between"><h2 className="text-xl font-semibold">任务详情</h2><button className="rounded-lg border px-3 py-1" onClick={() => setTaskOpen(false)}>关闭</button></div>{dag.nodes.map((n) => <div key={n.id} className="mb-3 rounded-xl border p-3"><div className="font-semibold">{n.id}. {n.agent} / {n.status}</div><div className="mt-1 text-sm text-slate-500">{n.description}</div><div className="mt-1 text-xs text-slate-400">依赖：{n.dependencies?.join(', ') || '无'}</div></div>)}</div></div>}<PreviewSidebar open={previewOpen} onClose={() => setPreviewOpen(false)} previewUrl={previewUrl} /></div>;
}
