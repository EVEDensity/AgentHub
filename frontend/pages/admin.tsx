import { useEffect, useMemo, useState, type JSX } from 'react';
import type { AuditLog, ModelConfig, ModelFormState, BindFormState, TestState } from '../types';

const AGENTS = ['Orchestrator', 'Architect', 'CodeGen', 'Review', 'Test', 'Deploy'] as const;

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
  { value: 'mock', label: 'Mock', home: '本地 Mock，无需官网地址', icon: 'K' },
] as const;

export default function AdminPage(): JSX.Element {
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [bindings, setBindings] = useState<Array<Record<string, unknown>>>([]);
  const [audits, setAudits] = useState<AuditLog[]>([]);
  const [templates, setTemplates] = useState<Array<Record<string, unknown>>>([]);
  const [notice, setNotice] = useState<string>('');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [testState, setTestState] = useState<TestState>({});
  const [modelForm, setModelForm] = useState<ModelFormState>({ provider: 'openai', modelName: 'gpt-4o-mini', apiKey: '', baseUrl: '' });
  const [bindForm, setBindForm] = useState<BindFormState>({ role: 'Orchestrator', modelConfigId: '', prompt: '' });

  useEffect(() => { refresh(); }, []);
  const selectedModel = useMemo(() => models.find((m) => m.id === selectedId) || models[0], [models, selectedId]);

  function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
    const token = typeof window !== 'undefined' ? localStorage.getItem('agenthub_token') : '';
    return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
  }

  async function refresh(): Promise<void> {
    const [modelRes, bindRes, auditRes, templateRes] = await Promise.all([
      fetch('/api/admin/model-config', { headers: authHeaders() }),
      fetch('/api/admin/role-bind', { headers: authHeaders() }),
      fetch('/api/admin/audit-log', { headers: authHeaders() }),
      fetch('/api/tasks/templates/list'),
    ]);
    if (modelRes.status === 401) setNotice('请先在 IM 首页登录管理员账号');
    if (modelRes.ok) {
      const data = (await modelRes.json()) as ModelConfig[];
      setModels(data);
      if (!selectedId && data[0]) setSelectedId(data[0].id);
    }
    if (bindRes.ok) setBindings(await bindRes.json() as Array<Record<string, unknown>>);
    if (auditRes.ok) setAudits(await auditRes.json() as AuditLog[]);
    if (templateRes.ok) setTemplates(await templateRes.json() as Array<Record<string, unknown>>);
  }

  async function saveModel(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const res = await fetch('/api/admin/model-config', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(modelForm),
    });
    setNotice(res.ok ? '模型配置已保存，可在下方列表中检测连接' : '模型配置保存失败');
    if (res.ok) {
      setModelForm((v) => ({ ...v, apiKey: '' }));
      await refresh();
    }
  }

  async function bindRole(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const body = { ...bindForm, modelConfigId: Number(bindForm.modelConfigId || selectedModel?.id || models[0]?.id) };
    const res = await fetch('/api/admin/role-bind', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    });
    setNotice(res.ok ? '角色绑定已保存' : '角色绑定失败');
    if (res.ok) await refresh();
  }

  async function testModel(model: ModelConfig): Promise<void> {
    setSelectedId(model.id);
    setTestState((prev) => ({ ...prev, [model.id]: { status: 'checking', message: '正在检测连接...' } }));
    const res = await fetch(`/api/admin/model-config/${model.id}/test`, { method: 'POST', headers: authHeaders() });
    const data = await res.json() as { status?: string; message?: string; latencyMs?: number };
    const resultStatus = (data.status || (res.ok ? 'success' : 'failed')) as 'checking' | 'success' | 'failed';
    setTestState((prev) => ({ ...prev, [model.id]: { status: resultStatus, message: data.message || '', latencyMs: data.latencyMs } }));
    setNotice(res.ok && data.status === 'success' ? `${model.provider}/${model.modelName} 连接正常` : `${model.provider}/${model.modelName} 检测失败`);
  }

  function providerMeta(provider: string): typeof PROVIDERS[number] {
    return PROVIDERS.find((p) => p.value === provider) || PROVIDERS[0];
  }

  function modelField<K extends keyof ModelFormState>(name: K, value: ModelFormState[K]): void {
    setModelForm((prev) => ({ ...prev, [name]: value }));
  }

  function bindField<K extends keyof BindFormState>(name: K, value: BindFormState[K]): void {
    setBindForm((prev) => ({ ...prev, [name]: value }));
  }

  function StatusPill({ state }: { state: TestState[number] | undefined }): JSX.Element {
    if (!state) return <span className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-500">未检测</span>;
    if (state.status === 'checking') return <span className="rounded-full bg-blue-50 px-3 py-1 text-xs text-blue-700">检测中</span>;
    if (state.status === 'success') return <span className="rounded-full bg-green-50 px-3 py-1 text-xs text-green-700">连接正常</span>;
    return <span className="rounded-full bg-red-50 px-3 py-1 text-xs text-red-700">连接失败</span>;
  }

  function ModelCard({ model }: { model: ModelConfig }): JSX.Element {
    const meta = providerMeta(model.provider);
    const state = testState[model.id];
    const active = selectedModel?.id === model.id;
    return (
      <button type="button" onClick={() => setSelectedId(model.id)}
        className={`group w-full rounded-2xl border bg-white p-5 text-left shadow-sm transition hover:border-blue-200 hover:bg-blue-50/30 ${active ? 'border-blue-400 ring-2 ring-blue-100' : 'border-slate-200'}`}>
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-100 text-lg font-bold text-slate-700">{meta.icon}</span>
            <div>
              <div className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                <span>{model.modelName}</span>
                <span className="rounded-md bg-amber-50 px-2 py-0.5 text-xs text-amber-700">{model.provider}</span>
              </div>
              <div className="mt-1 text-sm text-blue-600">{model.baseUrl || meta.home}</div>
            </div>
          </div>
          <div className="flex min-w-[260px] items-center justify-end gap-3">
            <StatusPill state={state} />
            <button type="button" className="rounded-xl border bg-white px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
              onClick={(e) => { e.stopPropagation(); testModel(model); }}>刷新</button>
          </div>
        </div>
        <div className="mt-4 grid gap-2 text-sm text-slate-500 md:grid-cols-3">
          <div>编号：{model.id}</div>
          <div>状态：{model.isActive ? '启用' : '停用'}</div>
          <div>延迟：{state?.latencyMs != null ? `${state.latencyMs} ms` : '未检测'}</div>
        </div>
        {state?.message && (
          <div className={`mt-3 rounded-xl px-3 py-2 text-sm ${state.status === 'failed' ? 'bg-red-50 text-red-700' : 'bg-slate-50 text-slate-600'}`}>
            {state.message}
          </div>
        )}
      </button>
    );
  }

  return (
    <div className="min-h-screen bg-slate-100 p-8 text-slate-900">
      <div className="mx-auto max-w-7xl">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">AgentHub 管理控制台</h1>
            <p className="mt-1 text-slate-500">模型源配置、连接检测、角色绑定、DAG 模板与审计日志</p>
          </div>
          <div className="flex gap-3">
            <a className="rounded-xl border bg-white px-4 py-2 hover:bg-slate-50" href="/im">返回 IM</a>
            <button className="rounded-xl border bg-white px-4 py-2 hover:bg-slate-50" onClick={refresh}>刷新全部</button>
          </div>
        </div>

        {notice && <div className="mb-4 rounded-xl bg-blue-50 p-3 text-blue-700">{notice}</div>}

        <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
          <section className="rounded-2xl bg-white p-6 shadow-sm">
            <h2 className="mb-4 text-xl font-semibold">新增模型源</h2>
            <form onSubmit={saveModel} className="grid gap-4 md:grid-cols-2">
              <label className="block text-sm font-medium">
                Provider
                <select className="mt-2 w-full rounded-lg border px-3 py-2" value={modelForm.provider}
                  onChange={(e) => modelField('provider', e.target.value)}>
                  {PROVIDERS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </label>
              <label className="block text-sm font-medium">
                模型名称
                <input className="mt-2 w-full rounded-lg border px-3 py-2" value={modelForm.modelName}
                  onChange={(e) => modelField('modelName', e.target.value)} required />
              </label>
              <label className="block text-sm font-medium">
                API Key
                <input type="password" className="mt-2 w-full rounded-lg border px-3 py-2" value={modelForm.apiKey}
                  onChange={(e) => modelField('apiKey', e.target.value)} placeholder="Ollama/Mock 可留空" />
              </label>
              <label className="block text-sm font-medium">
                Base URL
                <input className="mt-2 w-full rounded-lg border px-3 py-2" value={modelForm.baseUrl}
                  onChange={(e) => modelField('baseUrl', e.target.value)} placeholder={providerMeta(modelForm.provider).home} />
              </label>
              <div className="md:col-span-2">
                <button className="rounded-xl bg-blue-600 px-5 py-2 text-white hover:bg-blue-700">保存模型源</button>
              </div>
            </form>
          </section>

          <section className="rounded-2xl bg-white p-6 shadow-sm">
            <h2 className="mb-4 text-xl font-semibold">角色绑定</h2>
            <form onSubmit={bindRole} className="space-y-4">
              <label className="block text-sm font-medium">
                Agent 角色
                <select className="mt-2 w-full rounded-lg border px-3 py-2" value={bindForm.role}
                  onChange={(e) => bindField('role', e.target.value)}>
                  {AGENTS.map((agent) => <option key={agent} value={agent}>{agent}</option>)}
                </select>
              </label>
              <label className="block text-sm font-medium">
                模型配置
                <select className="mt-2 w-full rounded-lg border px-3 py-2"
                  value={bindForm.modelConfigId || selectedModel?.id || ''}
                  onChange={(e) => bindField('modelConfigId', e.target.value)}>
                  {models.map((model) => (
                    <option key={model.id} value={model.id}>#{model.id} {model.provider}/{model.modelName}</option>
                  ))}
                </select>
              </label>
              <label className="block text-sm font-medium">
                角色 Prompt
                <textarea rows={4} className="mt-2 w-full rounded-lg border px-3 py-2" value={bindForm.prompt}
                  onChange={(e) => bindField('prompt', e.target.value)} />
              </label>
              <button className="rounded-xl bg-blue-600 px-5 py-2 text-white hover:bg-blue-700">保存绑定</button>
            </form>
          </section>
        </div>

        <section className="mt-6 rounded-2xl bg-white p-6 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold">模型源连接检测</h2>
              <p className="mt-1 text-sm text-slate-500">支持 DeepSeek 全系列、MiniMax、智谱 GLM、Qwen、豆包、自定义 OpenAI API</p>
            </div>
            {selectedModel && (
              <div className="rounded-xl bg-slate-50 px-4 py-2 text-sm text-slate-600">
                当前选中：{selectedModel.id} {selectedModel.provider}/{selectedModel.modelName}
              </div>
            )}
          </div>
          <div className="space-y-4">
            {models.map((model) => <ModelCard key={model.id} model={model} />)}
          </div>
        </section>

        <section className="mt-6 rounded-2xl bg-white p-6 shadow-sm">
          <h2 className="mb-4 text-xl font-semibold">审计日志</h2>
          <div className="overflow-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b">
                  <th className="py-2">时间</th>
                  <th>用户</th>
                  <th>Agent</th>
                  <th>动作</th>
                  <th>风险</th>
                  <th>决策</th>
                  <th>Hash</th>
                </tr>
              </thead>
              <tbody>
                {audits.map((audit) => (
                  <tr key={audit.id} className="border-b">
                    <td className="py-2">{audit.timestamp}</td>
                    <td>{audit.userId}</td>
                    <td>{audit.agentId}</td>
                    <td>{audit.action}</td>
                    <td>{audit.riskLevel}</td>
                    <td>{audit.decision}</td>
                    <td className="font-mono text-xs">{audit.contentHash?.slice(0, 12)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}