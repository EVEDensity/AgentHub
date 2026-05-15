import { useEffect, useState, type JSX } from 'react';
import type { User } from '../types';

const PROVIDERS = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'azure', label: 'Azure OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'ollama', label: 'Ollama' },
  { value: 'gemini', label: 'Gemini' },
] as const;

interface ModelConfig {
  id?: string;
  provider: string;
  model: string;
  baseUrl: string;
  apiKey: string;
  group: string;
  priority: number;
}

interface AuditLog {
  id: string;
  action: string;
  detail: string;
  timestamp: string;
}

interface TestState {
  status: 'checking' | 'success' | 'failed';
  message: string;
}

export default function Admin(): JSX.Element {
  const [user, setUser] = useState<User | null>(null);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [modelForm, setModelForm] = useState<ModelConfig>({
    provider: 'openai',
    model: '',
    baseUrl: '',
    apiKey: '',
    group: 'default',
    priority: 0,
  });
  const [testState, setTestState] = useState<TestState>({ status: 'checking', message: '待测试' });
  const [notice, setNotice] = useState<string>('');

  useEffect(() => {
    const savedUser = localStorage.getItem('agenthub_user');
    if (savedUser) setUser(JSON.parse(savedUser) as User);
    fetchModels();
    fetchLogs();
  }, []);

  function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
    const token = localStorage.getItem('agenthub_token');
    return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
  }

  async function fetchModels(): Promise<void> {
    const res = await fetch('/api/admin/model-config', { headers: authHeaders() });
    if (res.ok) setModels((await res.json()) as ModelConfig[]);
  }

  async function fetchLogs(): Promise<void> {
    const res = await fetch('/api/admin/audit-logs', { headers: authHeaders() });
    if (res.ok) setLogs((await res.json()) as AuditLog[]);
  }

  function modelField(key: keyof ModelConfig, value: string | number): void {
    setModelForm((prev) => ({ ...prev, [key]: value }));
  }

  async function saveModel(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const res = await fetch('/api/admin/model-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(modelForm),
    });
    if (res.ok) {
      setNotice('模型配置已保存');
      fetchModels();
      setModelForm({ provider: 'openai', model: '', baseUrl: '', apiKey: '', group: 'default', priority: 0 });
    } else {
      const data = await res.json();
      setNotice(data.detail || '保存失败');
    }
  }

  async function deleteModel(id: string): Promise<void> {
    const res = await fetch(`/api/admin/model-config/${id}`, { method: 'DELETE', headers: authHeaders() });
    if (res.ok) {
      setNotice('已删除');
      fetchModels();
    }
  }

  async function testModel(id: string): Promise<void> {
    setTestState({ status: 'checking', message: '测试中...' });
    const res = await fetch(`/api/admin/model-config/${id}/test`, { headers: authHeaders() });
    const data = await res.json();
    setTestState({ status: res.ok ? 'success' : 'failed', message: (data.message as string) || (data.detail as string) || '未知' });
  }

  return (
    <div className="min-h-screen bg-warm-50 text-warm-800">
      <header className="border-b border-warm-150 bg-white px-8 py-4">
        <div className="mx-auto flex max-w-6xl items-center justify-between">
          <div className="flex items-center gap-4">
            <h1 className="text-h2 text-warm-800">管理控制台</h1>
            <span className="tag tag-warm">{user?.name} / {user?.role}</span>
          </div>
          <a className="btn-ghost" href="/">← 返回 IM</a>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-8 py-8">
        {notice && (
          <div className="mb-6 rounded-lg bg-warning-50 p-4 text-sm text-warning-600">
            {notice}
            <button className="ml-3 text-warning-500 hover:text-warning-600" onClick={() => setNotice('')}>✕</button>
          </div>
        )}

        <div className="grid gap-8 xl:grid-cols-[1.2fr_0.8fr]">
          <section className="card p-6">
            <h2 className="text-h3 text-warm-800">新增模型源</h2>
            <form onSubmit={saveModel} className="mt-5 grid gap-5 md:grid-cols-2">
              <label className="block text-h4 text-warm-700">
                Provider
                <select className="input-field mt-2" value={modelForm.provider}
                  onChange={(e) => modelField('provider', e.target.value)}>
                  {PROVIDERS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </label>
              <label className="block text-h4 text-warm-700">
                Model
                <input className="input-field mt-2" value={modelForm.model}
                  onChange={(e) => modelField('model', e.target.value)} />
              </label>
              <label className="block text-h4 text-warm-700">
                Base URL
                <input className="input-field mt-2" value={modelForm.baseUrl}
                  onChange={(e) => modelField('baseUrl', e.target.value)} />
              </label>
              <label className="block text-h4 text-warm-700">
                API Key
                <input type="password" className="input-field mt-2" value={modelForm.apiKey}
                  onChange={(e) => modelField('apiKey', e.target.value)} />
              </label>
              <label className="block text-h4 text-warm-700">
                Group
                <input className="input-field mt-2" value={modelForm.group}
                  onChange={(e) => modelField('group', e.target.value)} />
              </label>
              <label className="block text-h4 text-warm-700">
                Priority
                <input type="number" className="input-field mt-2" value={modelForm.priority}
                  onChange={(e) => modelField('priority', Number(e.target.value))} />
              </label>
              <div className="md:col-span-2">
                <button className="btn-primary">保存配置</button>
              </div>
            </form>
          </section>

          <section className="card p-6">
            <h2 className="text-h3 text-warm-800">角色绑定</h2>
            <div className="mt-5 space-y-3">
              {['admin', 'developer', 'viewer'].map((role) => (
                <div key={role} className="flex items-center justify-between rounded-lg bg-warm-50 px-4 py-3">
                  <span className="text-body text-warm-700">{role}</span>
                  <span className="tag tag-blue">已绑定</span>
                </div>
              ))}
            </div>
          </section>
        </div>

        <section className="card mt-8 p-6">
          <h2 className="text-h3 text-warm-800">模型配置列表</h2>
          <div className="mt-5 space-y-3">
            {models.length === 0 ? (
              <p className="text-caption text-warm-400">暂无配置</p>
            ) : (
              models.map((m) => (
                <div key={m.id} className="flex items-center justify-between rounded-lg bg-warm-50 px-4 py-3">
                  <div className="flex items-center gap-4">
                    <span className="tag tag-blue">{m.provider}</span>
                    <span className="text-body text-warm-700">{m.model}</span>
                    <span className="text-caption text-warm-400">{m.group}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <button className="btn-ghost text-caption text-primary-500" onClick={() => testModel(m.id!)}>测试</button>
                    <button className="btn-ghost text-caption text-danger-500" onClick={() => deleteModel(m.id!)}>删除</button>
                  </div>
                </div>
              ))
            )}
          </div>
          {testState.status !== 'checking' && (
            <div className={`mt-4 rounded-lg p-3 text-sm ${
              testState.status === 'success' ? 'bg-success-50 text-success-600' : 'bg-danger-50 text-danger-600'
            }`}>
              {testState.message}
            </div>
          )}
        </section>

        <section className="card mt-8 p-6">
          <h2 className="text-h3 text-warm-800">审计日志</h2>
          <div className="mt-5 space-y-2">
            {logs.length === 0 ? (
              <p className="text-caption text-warm-400">暂无日志</p>
            ) : (
              logs.map((log) => (
                <div key={log.id} className="flex items-start gap-3 rounded-lg bg-warm-50 px-4 py-3">
                  <span className="tag tag-warm shrink-0">{new Date(log.timestamp).toLocaleString()}</span>
                  <span className="text-body text-warm-700">{log.action}</span>
                  <span className="text-caption text-warm-400">{log.detail}</span>
                </div>
              ))
            )}
          </div>
        </section>
      </main>
    </div>
  );
}