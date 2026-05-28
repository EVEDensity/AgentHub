import { useEffect, useState, type FormEvent, type JSX } from 'react';
import type { Agent, AgentRoute, AgentRouteNode, AuditLog, User } from '../types';
import TokenUsageHeatmap from '../components/heatmap/TokenUsageHeatmap';
import AgentCanvas from '../components/flow/AgentCanvas';

const SETTINGS_MENU = [
  '服务商',
  '权限',
  '通用',
  'IM 接入',
  'MCP',
  'Agent Flow',
  '技能',
  '记忆',
  '插件',
  'Computer Use',
  'Token 用量',
] as const;

type MenuItem = (typeof SETTINGS_MENU)[number];

export default function AdminPage(): JSX.Element {
  const [user, setUser] = useState<User | null>(null);
  const [notice, setNotice] = useState('');
  const [agents, setAgents] = useState<Agent[]>([]);
  const [routes, setRoutes] = useState<AgentRoute[]>([]);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [activeMenu, setActiveMenu] = useState<MenuItem>('服务商');
  const [newAgent, setNewAgent] = useState({
    agentId: '',
    domain: '',
    adapterType: 'deepseek',
    baseModelName: '',
    rankLevel: 'L1',
    dutyNote: '',
    baseUrl: '',
    apiKey: '',
  });
  const [agentTests, setAgentTests] = useState<Record<string, { status: 'checking' | 'success' | 'failed'; message: string }>>({});
  const [isCreatingAgent, setIsCreatingAgent] = useState(false);
  const [editingAgentId, setEditingAgentId] = useState<string | null>(null);
  const [editAgent, setEditAgent] = useState({
    agentId: '',
    domain: '',
    adapterType: 'deepseek',
    baseModelName: '',
    rankLevel: 'L1',
    dutyNote: '',
    baseUrl: '',
    apiKey: '',
  });
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

  const [defaultChatAgent, setDefaultChatAgent] = useState<string>('Orchestrator');

  async function refresh(): Promise<void> {
    const [a, r, l, d] = await Promise.all([
      fetch('/api/agent/registry', { headers: authHeaders() }),
      fetch('/api/admin/workflows', { headers: authHeaders() }),
      fetch('/api/admin/audit/logs', { headers: authHeaders() }),
      fetch('/api/admin/chat-defaults', { headers: authHeaders() }),
    ]);
    if (a.ok) setAgents((await a.json()) as Agent[]);
    if (r.ok) setRoutes((await r.json()) as AgentRoute[]);
    if (l.ok) setLogs((await l.json()) as AuditLog[]);
    if (d.ok) setDefaultChatAgent(((await d.json()) as { agentId: string }).agentId);
  }

  async function createAgent(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    const payload = {
      ...newAgent,
      rankLevel: newAgent.rankLevel,
    };

    const res = await fetch('/api/agent/registry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    setNotice(res.ok ? `已添加服务商：${newAgent.agentId}` : data.detail || '添加失败');
    if (res.ok) {
      setNewAgent({
        agentId: '',
        domain: '',
        adapterType: 'deepseek',
        baseModelName: '',
        rankLevel: 'L1',
        dutyNote: '',
        baseUrl: '',
        apiKey: '',
      });
      setIsCreatingAgent(false);
      await refresh();
    }
  }

  async function testAgent(agentId: string): Promise<void> {
    setAgentTests((p) => ({ ...p, [agentId]: { status: 'checking', message: '检测中...' } }));
    const res = await fetch(`/api/agent/registry/${encodeURIComponent(agentId)}/test`, { method: 'POST', headers: authHeaders() });
    const data = await res.json();
    const ok = res.ok && data.status === 'success';
    setAgentTests((p) => ({ ...p, [agentId]: { status: ok ? 'success' : 'failed', message: data.message || (ok ? '连接正常' : '连接失败') } }));
    if (res.ok) await refresh();
  }

  async function removeAgent(agentId: string): Promise<void> {
    if (!window.confirm(`确认删除服务商 ${agentId}？`)) return;
    try {
      const res = await fetch(`/api/agent/registry/${encodeURIComponent(agentId)}`, {
        method: 'DELETE',
        headers: authHeaders(),
      });
      const data = await res.json().catch(() => ({}));
      setNotice(res.ok ? `已删除：${agentId}` : (data as { detail?: string }).detail || '删除失败');
      if (res.ok) {
        if (editingAgentId === agentId) cancelEditAgent();
        await refresh();
      }
    } catch {
      setNotice('删除失败，请检查网络或登录状态');
    }
  }

  function startEditAgent(agent: Agent): void {
    setEditingAgentId(agent.agentId);
    setEditAgent({
      agentId: agent.agentId,
      domain: agent.domain,
      adapterType: agent.adapterType,
      baseModelName: agent.baseModelName || '',
      rankLevel: agent.rankLevel || 'L1',
      dutyNote: agent.dutyNote || '',
      baseUrl: agent.baseUrl || '',
      apiKey: '',
    });
  }

  function cancelEditAgent(): void {
    setEditingAgentId(null);
    setEditAgent({
      agentId: '',
      domain: '',
      adapterType: 'deepseek',
      baseModelName: '',
      rankLevel: 'L1',
      dutyNote: '',
      baseUrl: '',
      apiKey: '',
    });
  }

  async function saveAgentEdit(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    if (!editingAgentId) return;

    try {
      const res = await fetch(`/api/agent/registry/${encodeURIComponent(editingAgentId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(editAgent),
      });
      const data = await res.json();
      setNotice(res.ok ? `已更新服务商：${editingAgentId}` : data.detail || '更新失败');
      if (res.ok) {
        cancelEditAgent();
        await refresh();
      }
    } catch {
      setNotice('保存失败：网络连接异常，请检查后端服务是否运行');
    }
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

  async function createRoute(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    const payload = { name: form.name, description: form.description, triggerKeywords: form.triggerKeywords.split(',').map((x) => x.trim()).filter(Boolean), nodes: form.nodes, isDefault: false };
    const res = await fetch('/api/admin/workflows', { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(payload) });
    const data = await res.json();
    setNotice(res.ok ? `路线创建成功：${form.name}` : data.detail || '路线创建失败');
    if (res.ok) await refresh();
  }

  async function setDefaultRoute(id: number): Promise<void> {
    const res = await fetch(`/api/admin/workflows/${id}/default`, { method: 'POST', headers: authHeaders() });
    setNotice(res.ok ? '默认路线已更新' : '设置失败');
    if (res.ok) await refresh();
  }

  async function toggleRoute(route: AgentRoute): Promise<void> {
    const res = await fetch(`/api/admin/workflows/${route.id}/active`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ active: !route.active }),
    });
    setNotice(res.ok ? `路线已${route.active ? '禁用' : '启用'}` : '操作失败');
    if (res.ok) await refresh();
  }

  async function handleSetDefaultChatAgent(agentId: string): Promise<void> {
    const res = await fetch('/api/admin/chat-defaults', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ agentId }),
    });
    const data = await res.json();
    if (res.ok) {
      setDefaultChatAgent(agentId);
      setNotice(`已将 ${agentId} 设为默认对话模型。不含 @Agent 指令的日常对话将默认使用该模型。`);
    } else {
      setNotice((data as { detail?: string }).detail || '设置失败');
    }
  }

  function renderServiceProviderModule(): JSX.Element {
    const isDefault = (a: Agent) => a.agentId === defaultChatAgent;
    return (
      <section className="space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-[34px] font-semibold leading-tight text-warm-900">服务商</h2>
            <p className="mt-1 text-sm text-warm-500">管理 API 服务商以访问模型。</p>
          </div>
          <button className="btn-primary" onClick={() => setIsCreatingAgent(true)}>
            + 添加服务商
          </button>
        </div>

        {/* Default model explanation banner */}
        <div className="rounded-xl border border-primary-200 bg-primary-50 px-4 py-3">
          <div className="flex items-start gap-2">
            <svg className="h-5 w-5 shrink-0 text-primary-500 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
            <div>
              <p className="text-sm font-medium text-primary-800">关于默认对话模型</p>
              <p className="mt-0.5 text-sm text-primary-600">将某个服务商设为默认后，<strong>不含 @Agent 指令的日常对话</strong>将默认使用该模型进行响应。如需指定其他 Agent，在输入框中 @Agent 名称即可临时切换。</p>
            </div>
          </div>
        </div>

        <div className="space-y-3">
          {agents.map((a) => {
            const test = agentTests[a.agentId];
            const online = a.status === 'online';
            const isDefaultAgent = isDefault(a);
            return (
              <div
                key={a.agentId}
                className={`rounded-2xl border bg-white px-5 py-4 ${isDefaultAgent ? 'border-primary-400 ring-1 ring-primary-200' : online ? 'border-green-400' : 'border-warm-200'}`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={`inline-block h-2.5 w-2.5 rounded-full ${online ? 'bg-green-500' : 'bg-warm-400'}`} />
                      <span className="truncate text-2xl font-semibold text-warm-900">{a.agentId}</span>
                      <span className="rounded bg-warm-100 px-2 py-0.5 text-xs text-warm-600">{a.adapterType}</span>
                      {isDefaultAgent ? <span className="rounded bg-primary-100 px-2 py-0.5 text-xs font-medium text-primary-700">默认对话模型</span> : null}
                    </div>
                    <div className="mt-1 truncate text-sm text-warm-500">
                      {a.baseUrl || '未配置地址'} · {a.baseModelName || '未配置模型'}
                    </div>
                    <div className="mt-1 text-xs text-warm-400">
                      Domain: {a.domain} · 职责: {a.dutyNote || '无'} · 位次: {a.rankLevel || 'L1'}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {!isDefaultAgent && (
                      <button className="btn-secondary px-3 py-1 text-sm" onClick={() => handleSetDefaultChatAgent(a.agentId)}>
                        设为默认对话模型
                      </button>
                    )}
                    {isDefaultAgent && (
                      <span className="rounded bg-primary-50 px-3 py-1 text-xs text-primary-600">当前默认 · 日常对话使用此模型</span>
                    )}
                    <button className="btn-ghost px-3 py-1 text-sm" onClick={() => startEditAgent(a)}>
                      编辑
                    </button>
                    <button className="btn-ghost px-3 py-1 text-sm" onClick={() => testAgent(a.agentId)}>
                      测试
                    </button>
                    {a.agentId !== 'Orchestrator' && (
                      <button className="btn-ghost px-3 py-1 text-sm text-red-500" onClick={() => removeAgent(a.agentId)}>
                        删除
                      </button>
                    )}
                  </div>
                </div>
                {test ? (
                  <div className={`mt-3 rounded px-3 py-2 text-sm ${test.status === 'success' ? 'bg-green-50 text-green-700' : test.status === 'failed' ? 'bg-red-50 text-red-600' : 'bg-blue-50 text-blue-600'}`}>
                    {test.message}
                  </div>
                ) : null}
              </div>
            );
          })}
          {!agents.length && <div className="text-caption text-warm-400">暂无服务商</div>}
        </div>
      </section>
    );
  }

  function renderAgentsModule(): JSX.Element {
    return (
      <section className="space-y-6">
        <div className="card p-6">
          <h2 className="text-h3">新建 Agent 路线（低代码）</h2>
          <form onSubmit={createRoute} className="mt-4 space-y-3">
            <input className="input-field" placeholder="路线名称" value={form.name} onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))} />
            <input className="input-field" placeholder="关键词（逗号）" value={form.triggerKeywords} onChange={(e) => setForm((p) => ({ ...p, triggerKeywords: e.target.value }))} />
            <textarea className="input-field" rows={2} placeholder="描述" value={form.description} onChange={(e) => setForm((p) => ({ ...p, description: e.target.value }))} />
            <div className="flex gap-2"><button type="button" className="btn-secondary" onClick={() => addNode('meta')}>+Layer1</button><button type="button" className="btn-secondary" onClick={() => addNode('domain')}>+Layer2</button><button type="button" className="btn-secondary" onClick={() => addNode('micro')}>+Layer3</button></div>
            <div className="space-y-2">{form.nodes.map((n, i) => <div key={n.id + i} className="rounded border border-warm-150 p-2"><div className="mb-2 flex items-center gap-2"><select className="input-field h-8 py-1 text-xs" value={n.agent} onChange={(e) => patchNode(i, { agent: e.target.value })}>{agents.map((a) => <option key={a.agentId} value={a.agentId}>{a.agentId}</option>)}</select><select className="input-field h-8 py-1 text-xs" value={n.layer || 'domain'} onChange={(e) => patchNode(i, { layer: e.target.value as 'meta' | 'domain' | 'micro' })}><option value="meta">Layer1</option><option value="domain">Layer2</option><option value="micro">Layer3</option></select><button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => moveNode(i, -1)}>↑</button><button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => moveNode(i, 1)}>↓</button></div><input className="input-field h-8 py-1 text-xs" placeholder="依赖节点ID，逗号分隔" value={n.dependencies.join(',')} onChange={(e) => patchNode(i, { dependencies: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })} /></div>)}</div>
            <button className="btn-primary">新建路线</button>
          </form>
        </div>

        <div className="card p-6">
          <h2 className="text-h3">路线列表（默认/启用）</h2>
          <div className="mt-4 space-y-3">{routes.map((r) => <div key={r.id} className="rounded-lg border border-warm-150 bg-white p-4"><div className="flex items-center justify-between"><div><div className="text-h4">{r.name} {r.isDefault ? <span className="tag tag-green ml-2">默认</span> : null} {!r.active ? <span className="tag tag-red ml-2">禁用</span> : null}</div><div className="text-caption text-warm-500">{r.description || '无描述'}</div></div><div className="flex gap-2"><button className="btn-secondary" onClick={() => setDefaultRoute(r.id)}>设为默认</button><button className="btn-ghost" onClick={() => toggleRoute(r)}>{r.active ? '禁用' : '启用'}</button></div></div><div className="mt-3 rounded bg-warm-50 p-3 text-sm">{r.nodes.map((n) => <div key={n.id}><span className="tag tag-blue mr-2">{n.layer || 'domain'}</span>{n.id} → {n.agent} · dep: {n.dependencies.join(',') || '无'}</div>)}</div></div>)}{!routes.length && <div className="text-caption text-warm-400">暂无路线</div>}</div>
        </div>
      </section>
    );
  }

  const [flowMode, setFlowMode] = useState<'list' | 'canvas'>('list');
  const [editingFlow, setEditingFlow] = useState<AgentRoute | null>(null);

  async function deleteFlow(routeId: number) {
    const res = await fetch(`/api/admin/workflows/${routeId}`, {
      method: 'DELETE',
      headers: authHeaders(),
    });
    const result = await res.json();
    setNotice(res.ok ? '工作流已删除' : result.detail || '删除失败');
    if (res.ok) {
      setFlowMode('list');
      setEditingFlow(null);
      await refresh();
    }
  }

  async function saveFlow(data: {
    id?: number;
    name: string;
    description: string;
    triggerKeywords: string[];
    nodes: Array<{
      id: string;
      type: string;
      name: string;
      description: string;
      x: number;
      y: number;
      agent?: string;
      layer?: string;
      dependencies: string[];
    }>;
    edges: Array<{ from: string; to: string; label?: string }>;
    isDefault: boolean;
    active: boolean;
  }) {
    const payload = {
      name: data.name,
      description: data.description,
      triggerKeywords: data.triggerKeywords,
      nodes: data.nodes.map((n) => ({
        id: n.id,
        domain: n.agent ? agents.find((a) => a.agentId === n.agent)?.domain || 'orchestrator' : 'orchestrator',
        agent: n.agent || n.name,
        description: n.description,
        dependencies: n.dependencies,
        status: 'PENDING',
        layer: n.layer || 'domain',
      })),
      isDefault: data.isDefault,
    };
    const url = data.id ? `/api/admin/workflows/${data.id}` : '/api/admin/workflows';
    const method = data.id ? 'PUT' : 'POST';
    const res = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(payload),
    });
    const result = await res.json();
    setNotice(res.ok ? `工作流已保存：${data.name}` : result.detail || '保存失败');
    if (res.ok) {
      setFlowMode('list');
      setEditingFlow(null);
      await refresh();
    }
  }

  function renderAgentFlowModule(): JSX.Element {
    if (flowMode === 'canvas') {
      const initialData = editingFlow
        ? {
            id: editingFlow.id,
            name: editingFlow.name,
            description: editingFlow.description,
            triggerKeywords: editingFlow.triggerKeywords,
            nodes: editingFlow.nodes.map((n) => ({
              id: n.id,
              type: (n.layer === 'meta' ? 'start' : n.layer === 'micro' ? 'end' : 'agent') as 'start' | 'agent' | 'tool' | 'ifelse' | 'end',
              name: n.agent,
              description: n.description,
              x: 300 + Math.random() * 400,
              y: 200 + Math.random() * 300,
              agent: n.agent,
              layer: n.layer,
              dependencies: n.dependencies,
            })),
            edges: editingFlow.nodes.flatMap((n) =>
              n.dependencies.map((dep) => ({ from: dep, to: n.id }))
            ),
            isDefault: editingFlow.isDefault,
            active: editingFlow.active,
          }
        : undefined;
      return (
        <AgentCanvas
          embedded
          initialData={initialData}
          agents={agents}
          onSave={saveFlow}
          onDelete={() => editingFlow && deleteFlow(editingFlow.id)}
        />
      );
    }

    return (
      <section className="space-y-6">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-[28px] font-semibold text-warm-900">Agent Flow</h2>
            <p className="mt-1 text-sm text-warm-500">低代码 Agent 连接画布，拖拽构建业务流。</p>
          </div>
          <button className="btn-primary" onClick={() => { setEditingFlow(null); setFlowMode('canvas'); }}>
            + 新建工作流
          </button>
        </div>

        <div className="space-y-3">
          {routes.map((r) => (
            <div key={r.id} className="rounded-2xl border border-warm-200 bg-white px-5 py-4">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-lg font-semibold text-warm-900">{r.name}</span>
                    {r.isDefault ? <span className="tag tag-green">默认</span> : null}
                    {!r.active ? <span className="tag tag-red">禁用</span> : null}
                  </div>
                  <div className="mt-1 text-sm text-warm-500">{r.description || '无描述'}</div>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {r.triggerKeywords.map((k) => (
                      <span key={k} className="tag tag-warm text-[10px]">{k}</span>
                    ))}
                  </div>
                </div>
                <div className="flex shrink-0 gap-2">
                  <button className="btn-secondary text-sm" onClick={() => { setEditingFlow(r); setFlowMode('canvas'); }}>
                    编辑
                  </button>
                  <button className="btn-secondary text-sm" onClick={() => setDefaultRoute(r.id)}>
                    设为默认
                  </button>
                  <button className="btn-ghost text-sm" onClick={() => toggleRoute(r)}>
                    {r.active ? '禁用' : '启用'}
                  </button>
                </div>
              </div>
              <div className="mt-3 rounded-lg bg-warm-50 p-3">
                <div className="flex flex-wrap gap-2">
                  {r.nodes.map((n) => (
                    <div key={n.id} className="flex items-center gap-1 text-xs text-warm-600">
                      <span className="tag tag-blue text-[10px]">{n.layer || 'domain'}</span>
                      <span>{n.agent}</span>
                      {n.dependencies.length > 0 && (
                        <span className="text-warm-400">← {n.dependencies.join(',')}</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ))}
          {!routes.length && <div className="text-caption text-warm-400">暂无工作流</div>}
        </div>
      </section>
    );
  }

  function renderModuleContent(): JSX.Element {
    if (activeMenu === '服务商') return renderServiceProviderModule();
    if (activeMenu === 'Agent Flow') return renderAgentFlowModule();
    if (activeMenu === 'Token 用量') return <TokenUsageHeatmap />;

    return (
      <section className="card p-6">
        <h2 className="text-h3">{activeMenu}</h2>
        <p className="mt-3 text-sm text-warm-500">该模块已独立，等待配置项接入。</p>
      </section>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-warm-50 text-warm-800">
      <aside className="h-screen w-[254px] flex-none overflow-y-auto border-r border-warm-150 bg-[#F3F2F0]">
        <div className="sticky top-0 z-10 h-20 border-b border-warm-150 bg-[#ECEBE8] px-5 flex items-center text-xl font-semibold text-warm-800">设置</div>
        <nav className="py-3">
          {SETTINGS_MENU.map((item) => (
            <button
              key={item}
              className={`block w-full px-5 py-3 text-left text-[34px] leading-none ${activeMenu === item ? 'bg-[#ECEBE8] text-warm-900 font-medium' : 'text-warm-700 hover:bg-[#ECEBE8]'}`}
              onClick={() => setActiveMenu(item)}
            >
              <span className="text-[32px] align-middle">{item}</span>
            </button>
          ))}
        </nav>
      </aside>

      <div className="min-w-0 flex-1 overflow-hidden">
        <header className="border-b border-warm-150 bg-white px-8 py-4">
          <div className="mx-auto flex max-w-7xl items-center justify-between">
            <div className="flex items-center gap-3">
              <h1 className="text-h2">管理控制台</h1>
              <span className="tag tag-warm">{user?.name}/{user?.role}</span>
              <span className="tag tag-blue">当前模块：{activeMenu}</span>
            </div>
            <a className="btn-secondary" href="/">返回 IM</a>
          </div>
        </header>

        <main className="h-[calc(100vh-73px)] overflow-y-auto px-8 py-8">
          <div className="mx-auto max-w-7xl space-y-6">
            {notice && <div className="rounded-lg bg-warning-50 p-3 text-sm text-warning-600">{notice}</div>}
            {renderModuleContent()}
          </div>
        </main>

        {(isCreatingAgent || editingAgentId) ? (
          <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 px-4" onClick={() => { setIsCreatingAgent(false); cancelEditAgent(); }}>
            <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-2xl bg-white p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
              <div className="mb-4 flex items-center justify-between">
                <h3 className="text-h3">{editingAgentId ? `编辑服务商：${editingAgentId}` : '添加服务商'}</h3>
                <button className="btn-ghost" onClick={() => { setIsCreatingAgent(false); cancelEditAgent(); }}>
                  关闭
                </button>
              </div>

              {notice && <div className="mb-4 rounded-lg bg-warning-50 p-3 text-sm text-warning-600">{notice}</div>}

              {isCreatingAgent ? (
                <form onSubmit={createAgent} className="grid gap-3 md:grid-cols-2">
                  <input className="input-field" placeholder="自定义名称（Agent ID）" value={newAgent.agentId} onChange={(e) => setNewAgent((p) => ({ ...p, agentId: e.target.value }))} />
                  <input className="input-field" placeholder="业务 Domain（如 codegen）" value={newAgent.domain} onChange={(e) => setNewAgent((p) => ({ ...p, domain: e.target.value }))} />
                  <input className="input-field" placeholder="适配器类型（如 deepseek/openai）" value={newAgent.adapterType} onChange={(e) => setNewAgent((p) => ({ ...p, adapterType: e.target.value }))} />
                  <input className="input-field" placeholder="大模型基座名称（如 DeepSeek-V3）" value={newAgent.baseModelName} onChange={(e) => setNewAgent((p) => ({ ...p, baseModelName: e.target.value }))} />
                  <select className="input-field" value={newAgent.rankLevel} onChange={(e) => setNewAgent((p) => ({ ...p, rankLevel: e.target.value }))}>
                    <option value="L1">L1（一级位次）</option>
                    <option value="L2">L2（二级位次）</option>
                    <option value="L3">L3（三级位次）</option>
                  </select>
                  <input className="input-field" placeholder="API Base URL（可选）" value={newAgent.baseUrl} onChange={(e) => setNewAgent((p) => ({ ...p, baseUrl: e.target.value }))} />
                  <textarea className="input-field md:col-span-2" rows={2} placeholder="职责备注（可选）" value={newAgent.dutyNote} onChange={(e) => setNewAgent((p) => ({ ...p, dutyNote: e.target.value }))} />
                  <input className="input-field md:col-span-2" placeholder="API Key（可选）" type="password" value={newAgent.apiKey} onChange={(e) => setNewAgent((p) => ({ ...p, apiKey: e.target.value }))} />
                  <div className="md:col-span-2 flex justify-end gap-2">
                    <button type="button" className="btn-secondary" onClick={() => setIsCreatingAgent(false)}>取消</button>
                    <button className="btn-primary">添加服务商</button>
                  </div>
                </form>
              ) : null}

              {editingAgentId ? (
                <form onSubmit={saveAgentEdit} className="grid gap-3 md:grid-cols-2">
                  <input className="input-field" value={editAgent.agentId} disabled />
                  <input className="input-field" placeholder="业务 Domain" value={editAgent.domain} onChange={(e) => setEditAgent((p) => ({ ...p, domain: e.target.value }))} />
                  <input className="input-field" placeholder="适配器类型" value={editAgent.adapterType} onChange={(e) => setEditAgent((p) => ({ ...p, adapterType: e.target.value }))} />
                  <input className="input-field" placeholder="大模型基座名称" value={editAgent.baseModelName} onChange={(e) => setEditAgent((p) => ({ ...p, baseModelName: e.target.value }))} />
                  <select className="input-field" value={editAgent.rankLevel} onChange={(e) => setEditAgent((p) => ({ ...p, rankLevel: e.target.value }))}>
                    <option value="L1">L1（一级位次）</option>
                    <option value="L2">L2（二级位次）</option>
                    <option value="L3">L3（三级位次）</option>
                  </select>
                  <input className="input-field" placeholder="API Base URL" value={editAgent.baseUrl} onChange={(e) => setEditAgent((p) => ({ ...p, baseUrl: e.target.value }))} />
                  <textarea className="input-field md:col-span-2" rows={2} placeholder="职责备注" value={editAgent.dutyNote} onChange={(e) => setEditAgent((p) => ({ ...p, dutyNote: e.target.value }))} />
                  <input className="input-field md:col-span-2" placeholder="API Key（留空则保持不变，输入新值将替换）" type="password" value={editAgent.apiKey} onChange={(e) => setEditAgent((p) => ({ ...p, apiKey: e.target.value }))} />
                  <div className="md:col-span-2 flex justify-end gap-2">
                    <button type="button" className="btn-secondary" onClick={cancelEditAgent}>取消</button>
                    <button className="btn-primary">保存</button>
                  </div>
                </form>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
