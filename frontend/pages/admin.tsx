import { useRouter } from 'next/router';
import { useEffect, useMemo, useState, type FormEvent, type JSX } from 'react';
import type { Agent, AgentRoute, AgentRouteNode, AuditLog, MemoryDetail, MemoryFileInfo, SkillMeta, SkillDetail, User } from '../types';
import TokenUsageHeatmap from '../components/heatmap/TokenUsageHeatmap';
import AgentCanvas from '../components/flow/AgentCanvas';
import MarkdownRenderer from '../components/chat/MarkdownRenderer';

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
  const router = useRouter();
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
  const [adapterOptions, setAdapterOptions] = useState<Array<{ id: string; name: string; description: string; default_model: string; default_base_url: string; requires_api_key: boolean; category: string }>>([]);
  const [selectedAdapterInfo, setSelectedAdapterInfo] = useState<{ id: string; name: string; description: string; default_model: string; default_base_url: string; requires_api_key: boolean; category: string } | null>(null);
  const [editSelectedAdapterInfo, setEditSelectedAdapterInfo] = useState<{ id: string; name: string; description: string; default_model: string; default_base_url: string; requires_api_key: boolean; category: string } | null>(null);
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

  const [memoryLoading, setMemoryLoading] = useState(false);
  const [memoryError, setMemoryError] = useState('');
  const [memoryKeyword, setMemoryKeyword] = useState('');
  const [memoryFiles, setMemoryFiles] = useState<MemoryFileInfo[]>([]);
  const [activeMemoryFile, setActiveMemoryFile] = useState<string | null>(null);
  const [memoryDetail, setMemoryDetail] = useState<MemoryDetail | null>(null);
  const [memoryBodyDraft, setMemoryBodyDraft] = useState('');
  const [memoryDirty, setMemoryDirty] = useState(false);
  const [memoryPreview, setMemoryPreview] = useState(false);

  // ── Skills module state ────────────────────────────────────────
  const [skillList, setSkillList] = useState<SkillMeta[]>([]);
  const [skillLoading, setSkillLoading] = useState(false);
  const [skillError, setSkillError] = useState('');
  const [skillKeyword, setSkillKeyword] = useState('');
  const [skillCategoryFilter, setSkillCategoryFilter] = useState<string>('');
  const [activeSkillName, setActiveSkillName] = useState<string | null>(null);
  const [activeSkillSource, setActiveSkillSource] = useState<string | null>(null);
  const [skillDetail, setSkillDetail] = useState<SkillDetail | null>(null);
  const [skillDetailLoading, setSkillDetailLoading] = useState(false);
  const [skillMetaExpanded, setSkillMetaExpanded] = useState(false);

  useEffect(() => {
    const u = localStorage.getItem('agenthub_user');
    if (u) setUser(JSON.parse(u) as User);
    void refresh();
    void fetchAdapters();
  }, []);

  useEffect(() => {
    if (activeMenu === '记忆') {
      void loadMemoryFiles();
    }
    if (activeMenu === '技能') {
      void loadSkills();
    }
  }, [activeMenu]);

  const filteredMemoryFiles = useMemo(() => {
    const kw = memoryKeyword.trim().toLowerCase();
    if (!kw) return memoryFiles;
    return memoryFiles.filter((f) => {
      return f.name.toLowerCase().includes(kw)
        || f.filename.toLowerCase().includes(kw)
        || (f.description || '').toLowerCase().includes(kw);
    });
  }, [memoryFiles, memoryKeyword]);

  const filteredSkills = useMemo(() => {
    let result = skillList;
    const kw = skillKeyword.trim().toLowerCase();
    if (kw) {
      result = result.filter((s) => {
        return s.name.toLowerCase().includes(kw)
          || (s.display_name || '').toLowerCase().includes(kw)
          || (s.description || '').toLowerCase().includes(kw)
          || s.source.toLowerCase().includes(kw)
          || (s.version || '').toLowerCase().includes(kw)
          || (s.category || '').toLowerCase().includes(kw)
          || (s.tags || []).some((t) => t.toLowerCase().includes(kw));
      });
    }
    if (skillCategoryFilter) {
      result = result.filter((s) => s.category === skillCategoryFilter);
    }
    return result;
  }, [skillList, skillKeyword, skillCategoryFilter]);

  const skillCategories = useMemo(() => {
    const cats = new Set(skillList.map((s) => s.category || '其他'));
    return Array.from(cats).sort((a, b) => {
      if (a === '其他') return 1;
      if (b === '其他') return -1;
      return a.localeCompare(b);
    });
  }, [skillList]);

  // Primary → subcategories map for hierarchical display
  const skillCategoryTree = useMemo(() => {
    const tree: Record<string, Set<string>> = {};
    for (const s of skillList) {
      const pri = s.category || '其他';
      const sub = s.subcategory || '未分类';
      (tree[pri] ??= new Set()).add(sub);
    }
    return tree;
  }, [skillList]);

  const groupedSkills = useMemo(() => {
    const result: Record<string, SkillMeta[]> = {};
    for (const skill of filteredSkills) {
      (result[skill.source] ??= []).push(skill);
    }
    return result;
  }, [filteredSkills]);

  const skillTokens = useMemo(
    () => filteredSkills.reduce((sum, s) => sum + Math.ceil(s.content_length / 4), 0),
    [filteredSkills],
  );

  async function fetchAdapters(): Promise<void> {
    const res = await fetch('/api/adapters', { headers: authHeaders() });
    if (res.ok) {
      const data = await res.json() as { adapters: typeof adapterOptions };
      setAdapterOptions(data.adapters);
    }
  }

  function handleAdapterChange(value: string, mode: 'create' | 'edit'): void {
    const adapter = adapterOptions.find(a => a.id === value);
    if (mode === 'create') {
      setNewAgent((p) => ({
        ...p,
        adapterType: value,
        baseModelName: adapter?.default_model || '',
        baseUrl: adapter?.default_base_url || '',
      }));
      setSelectedAdapterInfo(adapter || null);
    } else {
      setEditAgent((p) => ({
        ...p,
        adapterType: value,
        baseModelName: adapter?.default_model || '',
        baseUrl: adapter?.default_base_url || '',
      }));
      setEditSelectedAdapterInfo(adapter || null);
    }
  }

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

  async function loadMemoryFiles(): Promise<void> {
    setMemoryLoading(true);
    setMemoryError('');
    try {
      const res = await fetch('/api/memory/files');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as MemoryFileInfo[];
      setMemoryFiles(data);

      const pick = activeMemoryFile && data.find((f) => f.filename === activeMemoryFile)
        ? activeMemoryFile
        : data[0]?.filename;
      if (pick) {
        setActiveMemoryFile(pick);
        await loadMemoryDetail(pick);
      } else {
        setActiveMemoryFile(null);
        setMemoryDetail(null);
        setMemoryBodyDraft('');
        setMemoryDirty(false);
      }
    } catch (e: unknown) {
      setMemoryError(e instanceof Error ? e.message : '加载记忆失败');
    } finally {
      setMemoryLoading(false);
    }
  }

  async function loadMemoryDetail(filename: string): Promise<void> {
    try {
      const res = await fetch(`/api/memory/files/${encodeURIComponent(filename)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const detail = (await res.json()) as MemoryDetail;
      setMemoryDetail(detail);
      setMemoryBodyDraft(detail.body || '');
      setMemoryDirty(false);
      setMemoryPreview(false);
    } catch (e: unknown) {
      setMemoryError(e instanceof Error ? e.message : '读取记忆详情失败');
    }
  }

  async function saveMemoryDetail(): Promise<void> {
    if (!activeMemoryFile || !memoryDetail) return;
    try {
      const res = await fetch(`/api/memory/files/${encodeURIComponent(activeMemoryFile)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: memoryDetail.meta.name,
          description: memoryDetail.meta.description,
          type: memoryDetail.meta.type,
          body: memoryBodyDraft,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setNotice('记忆已保存');
      await loadMemoryFiles();
      await loadMemoryDetail(activeMemoryFile);
    } catch (e: unknown) {
      setMemoryError(e instanceof Error ? e.message : '保存失败');
    }
  }

  function handleExportMemory(): void {
    if (!activeMemoryFile) return;
    const a = document.createElement('a');
    a.href = `/api/memory/files/${encodeURIComponent(activeMemoryFile)}/export`;
    a.download = activeMemoryFile;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  // ── Skills module helpers ──────────────────────────────────────────

  async function loadSkills(): Promise<void> {
    setSkillLoading(true);
    setSkillError('');
    try {
      const res = await fetch('/api/skills');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json() as { skills: SkillMeta[] };
      setSkillList(data.skills || []);

      // Persist active skill selection across reloads
      if (activeSkillName && activeSkillSource) {
        const stillExists = (data.skills || []).some(
          (s) => s.name === activeSkillName && s.source === activeSkillSource
        );
        if (stillExists) {
          await loadSkillDetail(activeSkillName, activeSkillSource);
        } else {
          setActiveSkillName(null);
          setActiveSkillSource(null);
          setSkillDetail(null);
        }
      }
    } catch (e: unknown) {
      setSkillError(e instanceof Error ? e.message : '加载技能列表失败');
    } finally {
      setSkillLoading(false);
    }
  }

  async function loadSkillDetail(name: string, source: string): Promise<void> {
    setSkillDetailLoading(true);
    setSkillMetaExpanded(false);
    try {
      const url = `/api/skills/${encodeURIComponent(name)}?source=${encodeURIComponent(source)}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const detail = (await res.json()) as SkillDetail;
      setSkillDetail(detail);
      setActiveSkillName(name);
      setActiveSkillSource(source);
    } catch (e: unknown) {
      setSkillError(e instanceof Error ? e.message : '读取技能详情失败');
    } finally {
      setSkillDetailLoading(false);
    }
  }

  function handleExportSkill(): void {
    if (!activeSkillName || !activeSkillSource) return;
    const a = document.createElement('a');
    a.href = `/api/skills/${encodeURIComponent(activeSkillName)}/raw?source=${encodeURIComponent(activeSkillSource)}`;
    a.download = `${activeSkillName}_SKILL.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
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
      setSelectedAdapterInfo(null);
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
    const adapter = adapterOptions.find(a => a.id === agent.adapterType) || null;
    setEditSelectedAdapterInfo(adapter);
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
    setEditSelectedAdapterInfo(null);
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

  useEffect(() => {
    if (!router.isReady) return;
    const menu = router.query.menu;
    if (typeof menu === 'string' && (SETTINGS_MENU as readonly string[]).includes(menu)) {
      setActiveMenu(menu as MenuItem);
    }
  }, [router.isReady, router.query.menu]);

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

  function renderMemoryModule(): JSX.Element {
    return (
      <section className="overflow-hidden rounded-2xl border border-warm-200 bg-white">
        <div className="grid min-h-[720px] grid-cols-[300px_1fr]">
          <aside className="border-r border-warm-150 bg-[#FBFAF8]">
            <div className="border-b border-warm-150 px-4 py-3">
              <div className="text-base font-semibold text-warm-900">项目记忆</div>
              <div className="text-xs text-warm-500">项目</div>
            </div>

            <div className="border-b border-warm-150 px-4 py-3">
              <div className="mb-2 text-sm font-medium text-warm-800">资源管理器</div>
              <input
                className="w-full rounded-lg border border-warm-200 bg-white px-3 py-2 text-sm outline-none focus:border-primary-300"
                placeholder="搜索项目记忆文件..."
                value={memoryKeyword}
                onChange={(e) => setMemoryKeyword(e.target.value)}
              />
            </div>

            <div className="h-[calc(720px-140px)] overflow-auto px-3 py-3">
              {memoryLoading ? <div className="px-2 py-2 text-xs text-warm-500">加载中...</div> : null}
              {memoryError ? <div className="px-2 py-2 text-xs text-red-500">{memoryError}</div> : null}
              {!memoryLoading && !filteredMemoryFiles.length ? (
                <div className="px-2 py-2 text-xs text-warm-400">暂无记忆文件</div>
              ) : null}

              {filteredMemoryFiles.map((f) => (
                <button
                  key={f.filename}
                  className={`mb-1 block w-full rounded-lg border px-3 py-2 text-left ${activeMemoryFile === f.filename ? 'border-warm-300 bg-warm-100' : 'border-transparent hover:border-warm-200 hover:bg-warm-50'}`}
                  onClick={() => {
                    setActiveMemoryFile(f.filename);
                    void loadMemoryDetail(f.filename);
                  }}
                >
                  <div className="truncate text-sm font-medium text-warm-800">{f.name}</div>
                  <div className="mt-1 truncate text-xs text-warm-500">{f.filename}</div>
                </button>
              ))}
            </div>
          </aside>

          <div className="min-w-0">
            <header className="flex items-center justify-between border-b border-warm-150 px-5 py-3">
              <div>
                <div className="text-lg font-semibold text-warm-900">{memoryDetail?.meta.name || 'MEMORY.md'}</div>
                <div className="text-xs text-warm-500">{activeMemoryFile || '未选择文件'}</div>
              </div>
              <div className="flex items-center gap-2">
                <button className="btn-secondary px-3 py-1.5 text-sm" onClick={() => void loadMemoryFiles()}>刷新</button>
                <button className="btn-secondary px-3 py-1.5 text-sm" onClick={() => setMemoryPreview((v) => !v)}>{memoryPreview ? '编辑' : '预览'}</button>
                <button className="btn-primary px-3 py-1.5 text-sm" disabled={!memoryDirty} onClick={() => void saveMemoryDetail()}>保存</button>
                <button className="btn-primary px-3 py-1.5 text-sm" disabled={!activeMemoryFile} onClick={() => handleExportMemory()}>导出</button>
              </div>
            </header>

            <div className="grid h-[calc(720px-61px)] grid-cols-2">
              <div className="border-r border-warm-150">
                <div className="flex items-center justify-between border-b border-warm-150 px-4 py-2">
                  <span className="text-xs font-medium tracking-wide text-warm-600">编辑</span>
                  <span className="text-xs text-warm-400">MARKDOWN</span>
                </div>
                <textarea
                  className="h-[calc(720px-101px)] w-full resize-none border-0 bg-white px-4 py-3 font-mono text-[13px] leading-6 text-warm-800 outline-none"
                  value={memoryBodyDraft}
                  onChange={(e) => {
                    setMemoryBodyDraft(e.target.value);
                    setMemoryDirty(true);
                  }}
                  placeholder="请输入记忆内容..."
                  disabled={memoryPreview || !memoryDetail}
                />
              </div>
              <div>
                <div className="flex items-center justify-between border-b border-warm-150 px-4 py-2">
                  <span className="text-xs font-medium tracking-wide text-warm-600">预览</span>
                  <span className="text-xs text-warm-400">已渲染</span>
                </div>
                <div className="h-[calc(720px-101px)] overflow-auto bg-[#FCFCFB] px-4 py-3 text-[14px] leading-7 text-warm-800">
                  <pre className="whitespace-pre-wrap font-sans">{memoryBodyDraft || '（空内容）'}</pre>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    );
  }

  function renderSkillsModule(): JSX.Element {
    const SOURCE_LABELS: Record<string, string> = { user: '用户', project: '项目', plugin: '插件', mcp: 'MCP' };
    const SOURCE_ICONS: Record<string, string> = { user: 'person', project: 'folder', plugin: 'extension', mcp: 'hub' };
    const SOURCE_ORDER = ['user', 'project', 'plugin', 'mcp'];

    // Loading state
    if (skillLoading && !skillList.length) {
      return (
        <section className="flex justify-center py-12">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-warm-400 border-t-transparent" />
        </section>
      );
    }

    // Error state
    if (skillError && !skillList.length) {
      return (
        <section className="card p-6">
          <h2 className="text-h3">技能</h2>
          <p className="mt-3 text-sm text-red-500">{skillError}</p>
          <button className="btn-secondary mt-3" onClick={() => loadSkills()}>重试</button>
        </section>
      );
    }

    // Empty state
    if (!skillLoading && !skillList.length) {
      return (
        <section className="rounded-2xl border border-dashed border-warm-200 bg-white p-12 text-center">
          <span className="material-symbols-outlined text-[48px] text-warm-400 mb-3 block">auto_awesome</span>
          <p className="text-sm text-warm-500">暂无本地技能</p>
          <p className="text-xs text-warm-400 mt-1">
            将 SKILL.md 文件放入 ~/.claude/skills/ 或项目 .claude/skills/ 目录即可自动识别
          </p>
        </section>
      );
    }

    return (
      <section className="overflow-hidden rounded-2xl border border-warm-200 bg-white h-[calc(100vh-165px)]">
        <div className="grid h-full grid-cols-[360px_1fr] overflow-hidden">
          {/* ── Left sidebar: stats, search, skill list ─────────────── */}
          <aside className="border-r border-warm-150 bg-[#FBFAF8] flex flex-col overflow-hidden">
            {/* Stats bar */}
            <div className="border-b border-warm-150 px-4 py-3">
              <div className="flex items-stretch gap-0">
                <div className="flex-1 text-center py-1">
                  <div className="text-[22px] font-bold leading-none text-warm-800">{filteredSkills.length}</div>
                  <div className="mt-0.5 text-[10px] uppercase tracking-wider text-warm-400">总数</div>
                </div>
                <div className="w-px bg-warm-150" />
                <div className="flex-1 text-center py-1">
                  <div className="text-[22px] font-bold leading-none text-warm-800">{skillCategories.length}</div>
                  <div className="mt-0.5 text-[10px] uppercase tracking-wider text-warm-400">分类</div>
                </div>
                <div className="w-px bg-warm-150" />
                <div className="flex-1 text-center py-1">
                  <div className="text-[22px] font-bold leading-none text-warm-800">{skillTokens.toLocaleString()}</div>
                  <div className="mt-0.5 text-[10px] uppercase tracking-wider text-warm-400">Tokens</div>
                </div>
              </div>
            </div>

            {/* Search + filters */}
            <div className="border-b border-warm-150 px-4 py-3 space-y-2">
              <div className="flex items-center gap-2 rounded-lg border border-warm-200 bg-white px-3 py-2 transition-colors focus-within:border-warm-400 focus-within:ring-2 focus-within:ring-warm-300/20">
                <span className="material-symbols-outlined text-[18px] text-warm-400 shrink-0">search</span>
                <input
                  className="min-w-0 flex-1 bg-transparent text-sm text-warm-800 outline-none placeholder:text-warm-400"
                  placeholder="搜索名称、描述、标签..."
                  value={skillKeyword}
                  onChange={(e) => setSkillKeyword(e.target.value)}
                />
                {skillKeyword && (
                  <button
                    type="button"
                    className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-warm-400 hover:text-warm-600"
                    onClick={() => setSkillKeyword('')}
                  >
                    <span className="material-symbols-outlined text-[14px]">close</span>
                  </button>
                )}
              </div>
              <div className="flex items-center gap-2">
                {skillCategories.length > 1 && (
                  <select
                    className="flex-1 min-h-9 rounded-lg border border-warm-200 bg-white px-3 text-sm text-warm-700 outline-none"
                    value={skillCategoryFilter}
                    onChange={(e) => setSkillCategoryFilter(e.target.value)}
                  >
                    <option value="">全部分类</option>
                    {skillCategories.map((cat) => {
                      const subs = skillCategoryTree[cat] || new Set<string>();
                      const subList = Array.from(subs).sort();
                      return (
                        <option key={cat} value={cat}>
                          {cat}{subList.length > 0 ? ` (${subList.join(' · ')})` : ''}
                        </option>
                      );
                    })}
                  </select>
                )}
                <button
                  className="btn-ghost shrink-0 px-2 py-1.5 rounded-lg"
                  onClick={() => loadSkills()}
                  title="刷新技能列表"
                >
                  <span className="material-symbols-outlined text-[18px]">refresh</span>
                </button>
              </div>
              {(skillKeyword || skillCategoryFilter) && (
                <div className="text-xs text-warm-400">
                  显示 {filteredSkills.length} / {skillList.length} 个技能
                  <button
                    className="ml-2 underline hover:text-warm-600"
                    onClick={() => { setSkillKeyword(''); setSkillCategoryFilter(''); }}
                  >
                    清除筛选
                  </button>
                </div>
              )}
            </div>

            {/* Skill list grouped by source */}
            <div className="flex-1 overflow-auto">
              {filteredSkills.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
                  <span className="material-symbols-outlined text-[36px] text-warm-400 mb-2">search_off</span>
                  <p className="text-sm text-warm-500">没有匹配的技能</p>
                  <p className="text-xs text-warm-400 mt-1">尝试调整搜索关键词或分类筛选</p>
                </div>
              ) : (
                SOURCE_ORDER.map((source) => {
                  const group = groupedSkills[source];
                  if (!group?.length) return null;

                  const sourceLabel = SOURCE_LABELS[source] || source;
                  const sourceTokenCount = group.reduce((sum, s) => sum + Math.ceil(s.content_length / 4), 0);

                  return (
                    <div key={source} className="border-b border-warm-100 last:border-b-0">
                      {/* Group header */}
                      <div className="flex items-center gap-2 px-4 py-2 bg-warm-50 sticky top-0 z-[1] border-b border-warm-100">
                        <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-warm-200 text-warm-600">
                          <span className="material-symbols-outlined text-[14px]">{SOURCE_ICONS[source] || 'inventory_2'}</span>
                        </span>
                        <span className="text-xs font-semibold text-warm-700">{sourceLabel}</span>
                        <span className="text-[11px] text-warm-400">{group.length} 个</span>
                        <span className="text-[10px] text-warm-400 ml-auto">~{sourceTokenCount.toLocaleString()}t</span>
                      </div>

                      {/* Skills in group */}
                      <div className="flex flex-col p-1">
                        {group.map((skill) => (
                          <button
                            key={`${skill.source}-${skill.name}`}
                            onClick={() => loadSkillDetail(skill.name, skill.source)}
                            className={`group rounded-lg border px-3 py-2 text-left transition-colors ${
                              activeSkillName === skill.name && activeSkillSource === skill.source
                                ? 'border-warm-300 bg-warm-100'
                                : 'border-transparent hover:border-warm-200 hover:bg-warm-50'
                            } ${skill.enabled === false ? 'opacity-50' : ''}`}
                          >
                            <div className="flex items-start gap-2">
                              <span className="mt-px material-symbols-outlined text-[15px] text-warm-400 shrink-0">
                                auto_awesome
                              </span>
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-1.5 flex-wrap">
                                  <span className="text-[12px] font-semibold text-warm-800 truncate">
                                    {skill.display_name || skill.name}
                                  </span>
                                  {skill.version && (
                                    <span className="rounded-full bg-warm-100 px-1.5 py-px text-[10px] text-warm-500 shrink-0">
                                      v{skill.version}
                                    </span>
                                  )}
                                </div>
                                <p className="mt-0.5 text-[11px] leading-4 text-warm-500 break-words line-clamp-1">
                                  {skill.description || '（无描述）'}
                                </p>
                                <div className="mt-1 flex items-center gap-x-2 text-[10px] text-warm-400">
                                  {skill.subcategory && (
                                    <>
                                      <span className="text-[10px] text-warm-500">{skill.subcategory}</span>
                                      <span className="text-warm-300">·</span>
                                    </>
                                  )}
                                  <span>~{Math.ceil(skill.content_length / 4).toLocaleString()}t</span>
                                  <span className="text-warm-300">·</span>
                                  <span>{skill.body_lines}行</span>
                                </div>
                              </div>
                              <span className="material-symbols-outlined text-[14px] text-warm-400 opacity-0 group-hover:opacity-60 transition-all group-hover:translate-x-0.5 shrink-0 mt-0.5">
                                chevron_right
                              </span>
                            </div>
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </aside>

          {/* ── Right panel: skill detail ───────────────────────────── */}
          <div className="min-w-0 flex flex-col overflow-hidden">
            {!skillDetail ? (
              /* Empty state: no skill selected */
              <div className="flex-1 flex flex-col items-center justify-center text-center px-8">
                <span className="material-symbols-outlined text-[56px] text-warm-300 mb-4">auto_awesome</span>
                <h3 className="text-base font-semibold text-warm-700 mb-1">选择一个技能</h3>
                <p className="text-sm text-warm-500 max-w-xs leading-relaxed">
                  从左侧列表中选择一个技能，查看其详细信息、元数据和原始 SKILL.md 内容。
                </p>
                {skillList.length > 0 && (
                  <p className="text-xs text-warm-400 mt-3">
                    已扫描 {skillList.length} 个本地技能，来自 {skillList.filter((s, i, arr) => arr.findIndex(x => x.source === s.source) === i).length} 个来源
                  </p>
                )}
              </div>
            ) : (
              <>
                {/* Detail header — compact single row */}
                <header className="flex items-center justify-between border-b border-warm-150 px-4 py-2 shrink-0 gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-[15px] font-semibold text-warm-900 truncate">
                        {typeof skillDetail.meta?.name === 'string' ? skillDetail.meta.name : (activeSkillName || '技能详情')}
                      </span>
                      {typeof skillDetail.meta?.version === 'string' && skillDetail.meta.version && (
                        <span className="rounded-full bg-warm-100 px-1.5 py-px text-[10px] font-medium text-warm-500 shrink-0">
                          v{skillDetail.meta.version}
                        </span>
                      )}
                      <span className="rounded bg-warm-100 px-1.5 py-px text-[10px] text-warm-500 shrink-0">
                        {SOURCE_LABELS[skillDetail.source] || skillDetail.source}
                      </span>
                    </div>
                    <div className="text-[11px] text-warm-400 mt-0.5 truncate max-w-lg">{skillDetail.path}</div>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button className="btn-ghost px-2 py-1 text-xs rounded-lg" onClick={() => loadSkillDetail(activeSkillName!, activeSkillSource!)}>
                      <span className="material-symbols-outlined text-[16px]">refresh</span>
                    </button>
                    <button className="btn-primary px-3 py-1 text-xs rounded-lg" onClick={() => handleExportSkill()}>
                      导出
                    </button>
                  </div>
                </header>

                {/* Compact metadata bar — key info inline, expandable for details */}
                {skillDetail.meta && Object.keys(skillDetail.meta).length > 0 && (() => {
                  const meta = skillDetail.meta;
                  const description = typeof meta.description === 'string' ? meta.description : '';
                  const authorsRaw = meta.authors;
                  const authorsText = Array.isArray(authorsRaw)
                    ? authorsRaw.map(String).join(' · ')
                    : typeof authorsRaw === 'string' ? authorsRaw : '';
                  const category = skillDetail.category || '';
                  const subcategory = skillDetail.subcategory || '';
                  const credentials = Array.isArray(meta.credentials) ? meta.credentials : [];
                  const tags = Array.isArray(meta.tags) ? meta.tags : [];
                  const hasExtra = credentials.length > 0 || tags.length > 0;

                  return (
                    <div className="border-b border-warm-100 bg-white">
                      {/* Always-visible compact row */}
                      <div className="flex items-center gap-3 px-4 py-1.5 flex-wrap">
                        {description && (
                          <span className="text-[12px] text-warm-600 truncate max-w-[360px]">{description}</span>
                        )}
                        {(category || subcategory) && (
                          <span className="inline-flex items-center gap-1 text-[11px] shrink-0">
                            <span className="text-warm-300">分类</span>
                            <span className="text-warm-600 font-medium">{category}</span>
                            {subcategory && (
                              <>
                                <span className="text-warm-300">/</span>
                                <span className="text-warm-500">{subcategory}</span>
                              </>
                            )}
                          </span>
                        )}
                        {activeSkillName && (
                          <code className="text-[11px] bg-warm-50 px-1.5 py-0.5 rounded text-warm-500 font-mono shrink-0">{activeSkillName}</code>
                        )}
                        {authorsText && (
                          <span className="text-[11px] text-warm-500 truncate max-w-[200px] shrink-0">
                            <span className="text-warm-300">作者</span> {authorsText}
                          </span>
                        )}
                        {hasExtra && (
                          <button
                            className="ml-auto shrink-0 text-[11px] text-warm-400 hover:text-warm-600 flex items-center gap-0.5"
                            onClick={() => setSkillMetaExpanded(!skillMetaExpanded)}
                          >
                            {skillMetaExpanded ? '收起' : '更多详情'}
                            <span className="material-symbols-outlined text-[14px] transition-transform" style={{ transform: skillMetaExpanded ? 'rotate(180deg)' : 'rotate(0deg)' }}>
                              expand_more
                            </span>
                          </button>
                        )}
                      </div>

                      {/* Expandable details */}
                      {skillMetaExpanded && hasExtra && (
                        <div className="px-4 pb-2 space-y-1.5 border-t border-warm-100 pt-1.5">
                          {credentials.length > 0 && (
                            <div>
                              <div className="text-[10px] font-medium text-warm-400 mb-1">入参 / 凭据</div>
                              <div className="flex flex-wrap gap-1">
                                {credentials.map((cred, idx) => {
                                  const credName = typeof cred === 'string' ? cred : (cred?.name || String(cred));
                                  return (
                                    <span key={idx} className="inline-flex items-center gap-1 rounded bg-warm-50 border border-warm-200 px-1.5 py-0.5 text-[10px] text-warm-600">
                                      <span className="material-symbols-outlined text-[11px] text-warm-400">key</span>
                                      {credName}
                                    </span>
                                  );
                                })}
                              </div>
                            </div>
                          )}
                          {tags.length > 0 && (
                            <div className="flex flex-wrap gap-1">
                              {tags.map((tag: unknown, idx: number) => (
                                <span key={idx} className="rounded-full bg-warm-100 px-1.5 py-0.5 text-[10px] text-warm-500">
                                  #{String(tag)}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })()}

                {/* Raw content — gets all remaining space */}
                <div className="flex-1 min-h-0 flex flex-col">
                  <div className="flex items-center justify-between border-b border-warm-100 bg-warm-50 px-4 py-1.5 shrink-0">
                    <span className="text-[11px] font-medium text-warm-500">SKILL.md</span>
                    <span className="text-[10px] text-warm-400">
                      {typeof skillDetail.meta?.content_length === 'number'
                        ? `${(skillDetail.meta.content_length / 1024).toFixed(1)} KB`
                        : ''}
                    </span>
                  </div>
                  {skillDetailLoading ? (
                    <div className="flex justify-center py-12">
                      <div className="h-5 w-5 animate-spin rounded-full border-2 border-warm-400 border-t-transparent" />
                    </div>
                  ) : (
                    <div className="flex-1 overflow-auto bg-[#FCFCFB] px-5 py-4">
                      {skillDetail.raw ? (
                        <MarkdownRenderer content={skillDetail.raw} />
                      ) : (
                        <span className="text-sm text-warm-400">（空内容）</span>
                      )}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </section>
    );
  }

  function renderModuleContent(): JSX.Element {
    if (activeMenu === '服务商') return renderServiceProviderModule();
    if (activeMenu === 'Agent Flow') return renderAgentFlowModule();
    if (activeMenu === 'Token 用量') return <TokenUsageHeatmap />;
    if (activeMenu === '记忆') return renderMemoryModule();
    if (activeMenu === '技能') return renderSkillsModule();

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
          <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 px-4" onClick={() => { setIsCreatingAgent(false); setSelectedAdapterInfo(null); cancelEditAgent(); }}>
            <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-2xl bg-white p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
              <div className="mb-4 flex items-center justify-between">
                <h3 className="text-h3">{editingAgentId ? `编辑服务商：${editingAgentId}` : '添加服务商'}</h3>
                <button className="btn-ghost" onClick={() => { setIsCreatingAgent(false); setSelectedAdapterInfo(null); cancelEditAgent(); }}>
                  关闭
                </button>
              </div>

              {notice && <div className="mb-4 rounded-lg bg-warning-50 p-3 text-sm text-warning-600">{notice}</div>}

              {isCreatingAgent ? (
                <form onSubmit={createAgent} className="grid gap-3 md:grid-cols-2">
                  <input className="input-field" placeholder="自定义名称（Agent ID）" value={newAgent.agentId} onChange={(e) => setNewAgent((p) => ({ ...p, agentId: e.target.value }))} />
                  <input className="input-field" placeholder="业务 Domain（如 codegen）" value={newAgent.domain} onChange={(e) => setNewAgent((p) => ({ ...p, domain: e.target.value }))} />
                  <div className="flex flex-col gap-1">
                    <select className="input-field" value={newAgent.adapterType} onChange={(e) => handleAdapterChange(e.target.value, 'create')}>
                      <option value="">-- 请选择适配器类型 --</option>
                      {adapterOptions.map((adapter) => (
                        <option key={adapter.id} value={adapter.id}>
                          {adapter.name}（{adapter.id}）{adapter.category === 'mock' ? '★ 推荐开发测试' : ''}
                        </option>
                      ))}
                    </select>
                    {selectedAdapterInfo && (
                      <div className="rounded bg-warm-50 px-3 py-2 text-xs text-warm-600">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-warm-700">{selectedAdapterInfo.name}</span>
                          <span className={`rounded px-1.5 py-0.5 text-[10px] ${selectedAdapterInfo.category === 'mock' ? 'bg-green-100 text-green-700' : selectedAdapterInfo.category === 'local' ? 'bg-blue-100 text-blue-700' : selectedAdapterInfo.category === 'custom' ? 'bg-purple-100 text-purple-700' : 'bg-warm-100 text-warm-600'}`}>
                            {selectedAdapterInfo.category === 'mock' ? '无需网络' : selectedAdapterInfo.category === 'local' ? '本地部署' : selectedAdapterInfo.category === 'custom' ? '自定义' : '云端服务'}
                          </span>
                          {!selectedAdapterInfo.requires_api_key && (
                            <span className="rounded bg-green-100 px-1.5 py-0.5 text-[10px] text-green-700">免 API Key</span>
                          )}
                        </div>
                        <p className="mt-1">{selectedAdapterInfo.description}</p>
                      </div>
                    )}
                  </div>
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
                  <div className="flex flex-col gap-1">
                    <select className="input-field" value={editAgent.adapterType} onChange={(e) => handleAdapterChange(e.target.value, 'edit')}>
                      <option value="">-- 请选择适配器类型 --</option>
                      {adapterOptions.map((adapter) => (
                        <option key={adapter.id} value={adapter.id}>
                          {adapter.name}（{adapter.id}）
                        </option>
                      ))}
                    </select>
                    {editSelectedAdapterInfo && (
                      <div className="rounded bg-warm-50 px-3 py-2 text-xs text-warm-600">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-warm-700">{editSelectedAdapterInfo.name}</span>
                          <span className={`rounded px-1.5 py-0.5 text-[10px] ${editSelectedAdapterInfo.category === 'mock' ? 'bg-green-100 text-green-700' : editSelectedAdapterInfo.category === 'local' ? 'bg-blue-100 text-blue-700' : editSelectedAdapterInfo.category === 'custom' ? 'bg-purple-100 text-purple-700' : 'bg-warm-100 text-warm-600'}`}>
                            {editSelectedAdapterInfo.category === 'mock' ? '无需网络' : editSelectedAdapterInfo.category === 'local' ? '本地部署' : editSelectedAdapterInfo.category === 'custom' ? '自定义' : '云端服务'}
                          </span>
                        </div>
                        <p className="mt-1">{editSelectedAdapterInfo.description}</p>
                      </div>
                    )}
                  </div>
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
