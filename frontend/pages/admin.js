import { useEffect, useMemo, useState } from 'react';

const AGENTS = ['Orchestrator', 'Architect', 'CodeGen', 'Review', 'Test', 'Deploy'];
const PROVIDERS = [
  { value: 'openai', label: 'OpenAI', home: 'https://api.openai.com/v1', icon: 'O' },
  { value: 'deepseek', label: 'DeepSeek', home: 'https://api.deepseek.com/v1', icon: 'D' },
  { value: 'minimax', label: 'MiniMax', home: 'https://api.minimax.chat/v1', icon: 'M' },
  { value: 'zhipu', label: '智谱 GLM', home: 'https://open.bigmodel.cn/api/paas/v4', icon: 'G' },
  { value: 'qwen', label: '通义千问 Qwen', home: 'https://dashscope.aliyuncs.com/compatible-mode/v1', icon: 'Q' },
  { value: 'doubao', label: '字节豆包', home: 'https://ark.cn-beijing.volces.com/api/v3', icon: 'B' },
  { value: 'custom_openai', label: '自定义 OpenAI', home: 'https://your-openai-compatible-api/v1', icon: 'C' },
  { value: 'anthropic', label: 'Anthropic', home: 'https://api.anthropic.com', icon: 'A' },
  { value: 'ollama', label: 'Ollama', home: 'http://localhost:11434', icon: 'L' },
  { value: 'mock', label: 'Mock', home: '本地 Mock，无需官网地址', icon: 'K' }
];

export default function AdminPage() {
  const [models, setModels] = useState([]);
  const [bindings, setBindings] = useState([]);
  const [audits, setAudits] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [notice, setNotice] = useState('');
  const [selectedId, setSelectedId] = useState(null);
  const [testState, setTestState] = useState({});
  const [modelForm, setModelForm] = useState({ provider: 'openai', modelName: 'gpt-4o-mini', apiKey: '', baseUrl: '' });
  const [bindForm, setBindForm] = useState({ role: 'Orchestrator', modelConfigId: '', prompt: '' });

  useEffect(() => { refresh(); }, []);
  const selectedModel = useMemo(() => models.find((m) => m.id === selectedId) || models[0], [models, selectedId]);

  function authHeaders(extra = {}) {
    const token = typeof window !== 'undefined' ? localStorage.getItem('agenthub_token') : '';
    return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
  }

  async function refresh() {
    const [modelRes, bindRes, auditRes, templateRes] = await Promise.all([
      fetch('/api/admin/model-config', { headers: authHeaders() }),
      fetch('/api/admin/role-bind', { headers: authHeaders() }),
      fetch('/api/admin/audit-log', { headers: authHeaders() }),
      fetch('/api/tasks/templates/list')
    ]);
    if (modelRes.status === 401) setNotice('请先在 IM 首页登录管理员账号');
    if (modelRes.ok) {
      const data = await modelRes.json();
      setModels(data);
      if (!selectedId && data[0]) setSelectedId(data[0].id);
    }
    if (bindRes.ok) setBindings(await bindRes.json());
    if (auditRes.ok) setAudits(await auditRes.json());
    if (templateRes.ok) setTemplates(await templateRes.json());
  }

  async function saveModel(event) {
    event.preventDefault();
    const res = await fetch('/api/admin/model-config', { method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify(modelForm) });
    setNotice(res.ok ? '模型配置已保存，可在下方列表中检测连接' : '模型配置保存失败');
    if (res.ok) { setModelForm((v) => ({ ...v, apiKey: '' })); await refresh(); }
  }

  async function bindRole(event) {
    event.preventDefault();
    const body = { ...bindForm, modelConfigId: Number(bindForm.modelConfigId || selectedModel?.id || models[0]?.id) };
    const res = await fetch('/api/admin/role-bind', { method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify(body) });
    setNotice(res.ok ? '角色绑定已保存' : '角色绑定失败');
    if (res.ok) await refresh();
  }

  async function testModel(model) {
    setSelectedId(model.id);
    setTestState((prev) => ({ ...prev, [model.id]: { status: 'checking', message: '正在检测连接...' } }));
    const res = await fetch(`/api/admin/model-config/${model.id}/test`, { method: 'POST', headers: authHeaders() });
    const data = await res.json();
    setTestState((prev) => ({ ...prev, [model.id]: { ...data, status: data.status || (res.ok ? 'success' : 'failed') } }));
    setNotice(res.ok && data.status === 'success' ? `${model.provider}/${model.modelName} 连接正常` : `${model.provider}/${model.modelName} 检测失败`);
  }

  function providerMeta(provider) { return PROVIDERS.find((p) => p.value === provider) || PROVIDERS[0]; }
  function modelField(name, value) { setModelForm((prev) => ({ ...prev, [name]: value })); }
  function bindField(name, value) { setBindForm((prev) => ({ ...prev, [name]: value })); }

  function StatusPill({ state }) {
    if (!state) return <span className="tag tag-warm">未检测</span>;
    if (state.status === 'checking') return <span className="tag tag-blue">检测中</span>;
    if (state.status === 'success') return <span className="tag tag-green">连接正常</span>;
    return <span className="tag tag-red">连接失败</span>;
  }

  function ModelCard({ model }) {
    const meta = providerMeta(model.provider);
    const state = testState[model.id];
    const active = selectedModel?.id === model.id;
    return <button type="button" onClick={() => setSelectedId(model.id)} className={`group w-full rounded-xl border bg-white p-5 text-left shadow-warm-sm transition-all duration-200 hover:border-primary-200 hover:shadow-warm-md ${active ? 'border-primary-500 ring-2 ring-primary-100' : 'border-warm-150'}`}>
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-4"><span className="flex h-12 w-12 items-center justify-center rounded-xl bg-warm-50 text-lg font-bold text-warm-600">{meta.icon}</span><div><div className="flex items-center gap-2 text-h3"><span>{model.modelName}</span><span className="tag tag-warm">{model.provider}</span></div><div className="mt-1 text-sm text-primary-500">{model.baseUrl || meta.home}</div></div></div>
        <div className="flex min-w-[260px] items-center justify-end gap-3"><StatusPill state={state} /><button type="button" className="btn-secondary text-sm px-3 py-1.5" onClick={(e) => { e.stopPropagation(); testModel(model); }}>刷新</button></div>
      </div>
      <div className="mt-4 grid gap-2 text-sm text-warm-500 md:grid-cols-3"><div>编号：#{model.id}</div><div>状态：{model.isActive ? '启用' : '停用'}</div><div>延迟：{state?.latencyMs != null ? `${state.latencyMs} ms` : '未检测'}</div></div>
      {state?.message && <div className={`mt-3 rounded-lg px-3 py-2 text-sm ${state.status === 'failed' ? 'bg-danger-50 text-danger-500' : 'bg-warm-50 text-warm-600'}`}>{state.message}</div>}
    </button>;
  }

  return <div className="min-h-screen bg-warm-50 p-8 text-warm-800"><div className="mx-auto max-w-7xl">
    <div className="mb-8 flex items-center justify-between"><div><h1 className="text-h1">AgentHub 管理控制台</h1><p className="mt-1.5 text-body">模型源配置、连接检测、角色绑定、DAG 模板与审计日志</p></div><div className="flex gap-3"><a className="btn-secondary" href="/im">返回 IM</a><button className="btn-secondary" onClick={refresh}>刷新全部</button></div></div>
    {notice && <div className="mb-5 rounded-lg bg-primary-50 p-3.5 text-sm text-primary-600">{notice}</div>}

    <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]"><section className="card p-6"><h2 className="text-h3 mb-5">新增模型源</h2><form onSubmit={saveModel} className="grid gap-5 md:grid-cols-2"><label className="block text-h4">Provider<select className="input-field mt-1.5" value={modelForm.provider} onChange={(e) => modelField('provider', e.target.value)}>{PROVIDERS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}</select></label><label className="block text-h4">模型名称<input className="input-field mt-1.5" value={modelForm.modelName} onChange={(e) => modelField('modelName', e.target.value)} required /></label><label className="block text-h4">API Key<input type="password" className="input-field mt-1.5" value={modelForm.apiKey} onChange={(e) => modelField('apiKey', e.target.value)} placeholder="Ollama/Mock 可留空" /></label><label className="block text-h4">Base URL<input className="input-field mt-1.5" value={modelForm.baseUrl} onChange={(e) => modelField('baseUrl', e.target.value)} placeholder={providerMeta(modelForm.provider).home} /></label><div className="md:col-span-2"><button className="btn-primary">保存模型源</button></div></form></section>
      <section className="card p-6"><h2 className="text-h3 mb-5">角色绑定</h2><form onSubmit={bindRole} className="space-y-5"><label className="block text-h4">Agent 角色<select className="input-field mt-1.5" value={bindForm.role} onChange={(e) => bindField('role', e.target.value)}>{AGENTS.map((agent) => <option key={agent} value={agent}>{agent}</option>)}</select></label><label className="block text-h4">模型配置<select className="input-field mt-1.5" value={bindForm.modelConfigId || selectedModel?.id || ''} onChange={(e) => bindField('modelConfigId', e.target.value)}>{models.map((model) => <option key={model.id} value={model.id}>#{model.id} {model.provider}/{model.modelName}</option>)}</select></label><label className="block text-h4">角色 Prompt<textarea rows={4} className="input-field mt-1.5" value={bindForm.prompt} onChange={(e) => bindField('prompt', e.target.value)} /></label><button className="btn-primary">保存绑定</button></form></section></div>

    <section className="card mt-6 p-6"><div className="mb-5 flex items-center justify-between"><div><h2 className="text-h3">模型源连接检测</h2><p className="mt-1 text-caption">支持 DeepSeek 全系列、MiniMax、智谱 GLM、Qwen、豆包、自定义 OpenAI API。</p></div>{selectedModel && <div className="rounded-lg bg-warm-50 px-4 py-2 text-sm text-warm-600">当前选中：#{selectedModel.id} {selectedModel.provider}/{selectedModel.modelName}</div>}</div><div className="space-y-4">{models.map((model) => <ModelCard key={model.id} model={model} />)}</div></section>

    <section className="card mt-6 p-6"><h2 className="text-h3 mb-5">审计日志</h2><div className="overflow-auto"><table className="w-full text-left text-sm"><thead><tr className="divider"><th className="py-3 pr-4 text-h4 text-warm-600">时间</th><th className="py-3 pr-4 text-h4 text-warm-600">用户</th><th className="py-3 pr-4 text-h4 text-warm-600">Agent</th><th className="py-3 pr-4 text-h4 text-warm-600">动作</th><th className="py-3 pr-4 text-h4 text-warm-600">风险</th><th className="py-3 pr-4 text-h4 text-warm-600">决策</th><th className="py-3 text-h4 text-warm-600">Hash</th></tr></thead><tbody>{audits.map((audit) => <tr key={audit.id} className="divider"><td className="py-3 pr-4 text-warm-700">{audit.timestamp}</td><td className="py-3 pr-4 text-warm-700">{audit.userId}</td><td className="py-3 pr-4 text-warm-700">{audit.agentId}</td><td className="py-3 pr-4 text-warm-700">{audit.action}</td><td className="py-3 pr-4"><span className={`tag ${audit.riskLevel === 'high' ? 'tag-red' : audit.riskLevel === 'medium' ? 'tag-warm' : 'tag-green'}`}>{audit.riskLevel}</span></td><td className="py-3 pr-4 text-warm-700">{audit.decision}</td><td className="py-3 font-mono text-xs text-warm-500">{audit.contentHash?.slice(0, 12)}</td></tr>)}</tbody></table></div></section>
  </div></div>;
}