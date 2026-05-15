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
    return <div key={`${msg.timestamp}-${index}`} className={`mb-4 flex ${isUser ? 'justify-end' : 'justify-start'}`}><div className={`max-w-[78%] rounded-xl px-4 py-3 shadow-warm-sm ${isUser ? 'bg-primary-500 text-white' : 'bg-white text-warm-800'}`}><div className="mb-1 flex items-center gap-2 text-xs opacity-80"><span className="font-semibold">{msg.sender || 'agent'}</span><span className="tag tag-warm">{badge}</span></div>{msg.type === 'code' || msg.type === 'diff' ? <DiffBubble value={msg.content} /> : <div className="whitespace-pre-wrap leading-7 text-body">{msg.content}</div>}{!isUser && msg.fidelityScore ? <FidelityScore score={msg.fidelityScore} /> : null}</div></div>;
  }

  if (!token) {
    return <div className="flex min-h-screen items-center justify-center bg-warm-50"><form onSubmit={submitAuth} className="w-96 rounded-xl bg-white p-8 shadow-warm-lg"><h1 className="text-h2">AgentHub {authMode === 'login' ? '登录' : '注册'}</h1><p className="mt-2 text-caption">默认管理员：admin / admin123</p>{notice && <div className="mt-4 rounded-lg bg-warning-50 p-3 text-sm text-warning-500">{notice}</div>}<label className="mt-6 block text-h4">用户名<input className="input-field mt-2" value={authForm.name} onChange={(e) => setAuthForm({ ...authForm, name: e.target.value })} /></label><label className="mt-5 block text-h4">密码<input type="password" className="input-field mt-2" value={authForm.password} onChange={(e) => setAuthForm({ ...authForm, password: e.target.value })} /></label><button className="btn-primary mt-6 w-full">{authMode === 'login' ? '登录' : '注册'}</button><button type="button" className="btn-ghost mt-3 w-full text-primary-500" onClick={() => setAuthMode(authMode === 'login' ? 'register' : 'login')}>{authMode === 'login' ? '没有账号？注册' : '已有账号？登录'}</button></form></div>;
  }

  const percent = dag.total ? Math.round((dag.completed / dag.total) * 100) : 0;

  return <div className="flex h-screen bg-warm-50 text-warm-800"><aside className="w-72 border-r border-warm-150 bg-white p-6"><div className="mb-6"><div className="text-h2">AgentHub</div><div className="mt-1 text-caption">{user?.name} / {user?.role}</div></div><a className="btn-secondary block w-full text-center" href="/admin">管理控制台</a><button className="btn-ghost mt-2 w-full" onClick={logout}>退出登录</button>{notice && <div className="mt-4 rounded-lg bg-warning-50 p-3 text-sm text-warning-500">{notice}</div>}<div className="mt-6 rounded-xl bg-warm-50 p-4"><div className="mb-3 text-h4">可 @ Agent</div><div className="flex flex-wrap gap-2">{AGENTS.map((a) => <button key={a} className="tag tag-blue" onClick={() => setInput(`@${a} ${input}`)}>{a}</button>)}</div></div>{pending.length > 0 && <div className="mt-6 rounded-xl bg-danger-50 p-4"><div className="mb-2 text-h4 text-danger-500">失败重试队列</div>{pending.map((m) => <button key={m.timestamp} className="mb-2 block text-left text-xs text-danger-500 underline" onClick={() => retryMessage(m)}>{m.content.slice(0, 28)}...</button>)}</div>}</aside><main className="flex flex-1 flex-col"><header className="border-b border-warm-150 bg-white px-6 py-4"><div className="flex items-center justify-between gap-6"><div><div className="text-h3">IM 协作入口</div><div className="text-caption mt-0.5">WebSocket：{connected ? '已连接' : '重连中'}</div></div><div className="min-w-[420px]"><div className="mb-1.5 flex justify-between text-caption"><button onClick={() => setTaskOpen(true)} className="text-primary-500 hover:text-primary-600">DAG 进度 / 查看任务详情</button><span>{percent}%</span></div><div className="h-1.5 overflow-hidden rounded-full bg-warm-100"><div className="h-full bg-primary-500 transition-all duration-300" style={{ width: `${percent}%` }} /></div></div></div></header><section className="flex-1 overflow-auto p-6">{messages.map(renderMessage)}{generated && <GeneratedFilesPanel generated={generated} onCommit={confirmCommit} />}<div ref={bottomRef} /></section><footer className="border-t border-warm-150 bg-white px-6 py-4"><div className="flex gap-3"><input className="input-field flex-1" value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && (e.preventDefault(), send())} placeholder="输入消息，支持 @Agent 指令..." /><button className="btn-primary" onClick={() => send()}>发送</button><button className="btn-secondary" onClick={openPreview}>预览</button></div></footer><PreviewSidebar open={previewOpen} onClose={() => setPreviewOpen(false)} previewUrl={previewUrl} />{taskOpen && <div className="fixed inset-0 z-40 flex items-center justify-center bg-warm-900/20"><div className="w-[520px] rounded-xl bg-white p-6 shadow-warm-xl"><div className="mb-4 flex items-center justify-between"><h3 className="text-h3">DAG 任务详情</h3><button className="btn-ghost p-1" onClick={() => setTaskOpen(false)}>✕</button></div><div className="space-y-3">{dag.nodes.map((node, i) => <div key={i} className="flex items-center gap-3 rounded-lg bg-warm-50 px-4 py-3"><span className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-medium ${node.status === 'completed' ? 'bg-success-50 text-success-500' : node.status === 'running' ? 'bg-primary-50 text-primary-500' : 'bg-warm-100 text-warm-500'}`}>{node.status === 'completed' ? '✓' : node.status === 'running' ? '●' : i + 1}</span><span className="text-body flex-1">{node.name || `任务 ${i + 1}`}</span><span className="tag tag-warm">{node.status || 'pending'}</span></div>)}</div></div></div>}</main></div>;
}