import { useEffect, useState, type JSX } from 'react';
import type { Agent, AgentRoute, AgentRouteNode, AuditLog, User } from '../types';

export default function AdminPage(): JSX.Element {
  const [user, setUser] = useState<User | null>(null);
  const [notice, setNotice] = useState('');
  const [agents, setAgents] = useState<Agent[]>([]);
  const [routes, setRoutes] = useState<AgentRoute[]>([]);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [newAgent, setNewAgent] = useState({ agentId: '', domain: 'domain', adapterType: 'mock', riskLevel: 'L1' });
  const [form, setForm] = useState<{ name: string; description: string; triggerKeywords: string; nodes: AgentRouteNode[] }>({
    name: '', description: '', triggerKeywords: '', nodes: [
      { id: 'orchestrator', domain: 'orchestrator', agent: 'Orchestrator', description: '元调度', dependencies: [], status: 'PENDING', layer: 'meta' },
      { id: 'codegen', domain: 'codegen', agent: 'CodeGen', description: '代码生成', dependencies: ['orchestrator'], status: 'PENDING', layer: 'domain' },
    ],
  });

  useEffect(() => {
    const u = localStorage.getItem('agenthub_user');
    if (u) setUser(JSON.parse(u) as User);
    void refresh();
  }, []);

  function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
    const token = localStorage.getItem('agenthub_token');
    return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
  }

  async function refresh(): Promise<void> {
    const [a, r, l] = await Promise.all([
      fetch('/api/agent/registry', { headers: authHeaders() }),
      fetch('/api/admin/agent-routes', { headers: authHeaders() }),
      fetch('/api/admin/audit-log', { headers: authHeaders() }),
    ]);
    if (a.ok) setAgents((await a.json()) as Agent[]);
    if (r.ok) setRoutes((await r.json()) as AgentRoute[]);
    if (l.ok) setLogs((await l.json()) as AuditLog[]);
  }

  async function createAgent(e: React.FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    const res = await fetch('/api/agent/registry', { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(newAgent) });
    const data = await res.json();
    setNotice(res.ok ? `已创建 Agent：${newAgent.agentId}` : data.detail || '创建失败');
    if (res.ok) { setNewAgent({ agentId: '', domain: 'domain', adapterType: 'mock', riskLevel: 'L1' }); await refresh(); }
  }

  function addNode(layer: 'meta' | 'domain' | 'micro'): void {
    const picked = agents[0];
    if (!picked) return;
    setForm((p) => ({ ...p, nodes: [...p.nodes, { id: `${picked.agentId.toLowerCase()}-${Date.now()}`, domain: picked.domain, agent: picked.agentId, description: `执行 ${picked.agentId}`, dependencies: [], status: 'PENDING', layer }] }));
  }

  function moveNode(i: number, d: -1 | 1): void {
    setForm((p) => {
      const n = [...p.nodes];
      const t = i + d;
      if (t < 0 || t >= n.length) return p;
      [n[i], n[t]] = [n[t], n[i]];
      return { ...p, nodes: n };
    });
  }

  function patchNode(i: number, patch: Partial<AgentRouteNode>): void {
    setForm((p) => ({ ...p, nodes: p.nodes.map((n, idx) => idx === i ? { ...n, ...patch } : n) }));
  }

  async function createRoute(e: React.FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    const payload = { name: form.name, description: form.description, triggerKeywords: form.triggerKeywords.split(',').map((x) => x.trim()).filter(Boolean), nodes: form.nodes, isDefault: false };
    const res = await fetch('/api/admin/agent-routes', { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(payload) });
    const data = await res.json();
    setNotice(res.ok ? `路线创建成功：${form.name}` : data.detail || '路线创建失败');
    if (res.ok) await refresh();
  }

  async function setDefaultRoute(id: number): Promise<void> {
    const res = await fetch(`/api/admin/agent-routes/${id}/default`, { method: 'POST', headers: authHeaders() });
    setNotice(res.ok ? '默认路线已更新' : '设置失败');
    if (res.ok) await refresh();
  }

  async function toggleRoute(route: AgentRoute): Promise<void> {
    const res = await fetch(`/api/admin/agent-routes/${route.id}/active`, { method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ active: !route.active }) });
    setNotice(res.ok ? `路线已${route.active ? '禁用' : '启用'}` : '操作失败');
    if (res.ok) await refresh();
  }

  return (
    <div className="min-h-screen bg-warm-50 text-warm-800">
      <header className="border-b border-warm-150 bg-white px-8 py-4"><div className="mx-auto flex max-w-7xl items-center justify-between"><div className="flex items-center gap-3"><h1 className="text-h2">Agent 路线配置</h1><span className="tag tag-warm">{user?.name}/{user?.role}</span></div><a className="btn-secondary" href="/">返回 IM</a></div></header>
      <main className="mx-auto max-w-7xl space-y-6 px-8 py-8">
        {notice && <div className="rounded-lg bg-warning-50 p-3 text-sm text-warning-600">{notice}</div>}

        <section className="grid gap-6 lg:grid-cols-2">
          <div className="card p-6">
            <h2 className="text-h3">Agent 注册（分层可自建）</h2>
            <form onSubmit={createAgent} className="mt-4 grid gap-3 md:grid-cols-2">
              <input className="input-field" placeholder="Agent ID" value={newAgent.agentId} onChange={(e) => setNewAgent((p) => ({ ...p, agentId: e.target.value }))} />
              <input className="input-field" placeholder="Domain" value={newAgent.domain} onChange={(e) => setNewAgent((p) => ({ ...p, domain: e.target.value }))} />
              <input className="input-field" placeholder="Adapter" value={newAgent.adapterType} onChange={(e) => setNewAgent((p) => ({ ...p, adapterType: e.target.value }))} />
              <select className="input-field" value={newAgent.riskLevel} onChange={(e) => setNewAgent((p) => ({ ...p, riskLevel: e.target.value }))}><option value="L1">L1</option><option value="L2">L2</option><option value="L3">L3</option></select>
              <button className="btn-primary md:col-span-2">创建 Agent</button>
            </form>
            <div className="mt-4 grid gap-2 md:grid-cols-2">{agents.map((a) => <div key={a.agentId} className="rounded bg-warm-50 px-3 py-2 text-sm">{a.agentId} · {a.domain}</div>)}</div>
          </div>

          <div className="card p-6">
            <h2 className="text-h3">新建路线（低代码）</h2>
            <form onSubmit={createRoute} className="mt-4 space-y-3">
              <input className="input-field" placeholder="路线名称" value={form.name} onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))} />
              <input className="input-field" placeholder="关键词（逗号）" value={form.triggerKeywords} onChange={(e) => setForm((p) => ({ ...p, triggerKeywords: e.target.value }))} />
              <textarea className="input-field" rows={2} placeholder="描述" value={form.description} onChange={(e) => setForm((p) => ({ ...p, description: e.target.value }))} />
              <div className="flex gap-2"><button type="button" className="btn-secondary" onClick={() => addNode('meta')}>+Layer1</button><button type="button" className="btn-secondary" onClick={() => addNode('domain')}>+Layer2</button><button type="button" className="btn-secondary" onClick={() => addNode('micro')}>+Layer3</button></div>
              <div className="space-y-2">{form.nodes.map((n, i) => <div key={n.id + i} className="rounded border border-warm-150 p-2"><div className="mb-2 flex items-center gap-2"><select className="input-field h-8 py-1 text-xs" value={n.agent} onChange={(e) => patchNode(i, { agent: e.target.value })}>{agents.map((a) => <option key={a.agentId} value={a.agentId}>{a.agentId}</option>)}</select><select className="input-field h-8 py-1 text-xs" value={n.layer || 'domain'} onChange={(e) => patchNode(i, { layer: e.target.value as 'meta' | 'domain' | 'micro' })}><option value="meta">Layer1</option><option value="domain">Layer2</option><option value="micro">Layer3</option></select><button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => moveNode(i, -1)}>↑</button><button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => moveNode(i, 1)}>↓</button></div><input className="input-field h-8 py-1 text-xs" placeholder="依赖节点ID，逗号分隔" value={n.dependencies.join(',')} onChange={(e) => patchNode(i, { dependencies: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })} /></div>)}</div>
              <button className="btn-primary">新建列表</button>
            </form>
          </div>
        </section>

        <section className="card p-6">
          <h2 className="text-h3">路线列表（默认/启用/预览 DAG）</h2>
          <div className="mt-4 space-y-3">{routes.map((r) => <div key={r.id} className="rounded-lg border border-warm-150 bg-white p-4"><div className="flex items-center justify-between"><div><div className="text-h4">{r.name} {r.isDefault ? <span className="tag tag-green ml-2">默认</span> : null} {!r.active ? <span className="tag tag-red ml-2">禁用</span> : null}</div><div className="text-caption text-warm-500">{r.description || '无描述'}</div></div><div className="flex gap-2"><button className="btn-secondary" onClick={() => setDefaultRoute(r.id)}>设为默认</button><button className="btn-ghost" onClick={() => toggleRoute(r)}>{r.active ? '禁用' : '启用'}</button></div></div><div className="mt-3 rounded bg-warm-50 p-3 text-sm">{r.nodes.map((n) => <div key={n.id}><span className="tag tag-blue mr-2">{n.layer || 'domain'}</span>{n.id} → {n.agent} · dep: {n.dependencies.join(',') || '无'}</div>)}</div></div>)}{!routes.length && <div className="text-caption text-warm-400">暂无路线</div>}</div>
        </section>

        <section className="card p-6">
          <h2 className="text-h3">审计日志</h2>
          <div className="mt-4 space-y-2">{logs.slice(0, 40).map((log) => <div key={log.id} className="rounded bg-warm-50 px-3 py-2 text-sm">{new Date(log.timestamp).toLocaleString()} · {log.action}</div>)}</div>
        </section>
      </main>
    </div>
  );
}
