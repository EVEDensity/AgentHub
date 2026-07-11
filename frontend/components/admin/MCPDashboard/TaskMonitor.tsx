import { useEffect, useState, type JSX } from 'react';
import StatusBadge from './shared/StatusBadge';

interface TaskItem {
  id: string;
  sessionId: string;
  status: string;
  dag: string;
  currentNodeId: string | null;
  templateId: number | null;
  agentRouteId: number | null;
  createdAt: string;
  updatedAt: string;
  totalNodes: number;
  completedNodes: number;
  progressPercent: number;
}

interface TaskDetail extends TaskItem {
  dagParsed: Record<string, unknown>;
  executionHistory: Array<Record<string, unknown>>;
}

interface TemplateItem {
  id: number;
  name: string;
  category: string;
  keywords: string[];
  dag: Record<string, unknown>;
  usageCount: number;
  createdAt: string;
}

interface RouteItem {
  id: number;
  name: string;
  description: string;
  keywords: string[];
  nodes: Array<Record<string, unknown>>;
  isDefault: number;
  active: number;
  createdAt: string;
  updatedAt: string;
}

function authHeaders(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  const token = localStorage.getItem('agenthub_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export default function TaskMonitor(): JSX.Element {
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [subTab, setSubTab] = useState<'tasks' | 'history' | 'templates' | 'routes'>('tasks');
  const [actionMsg, setActionMsg] = useState('');

  // Templates
  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);

  // Routes
  const [routes, setRoutes] = useState<RouteItem[]>([]);
  const [routesLoading, setRoutesLoading] = useState(false);

  async function fetchTasks(): Promise<void> {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams();
      if (filterStatus) params.set('status', filterStatus);
      const res = await fetch(`/api/admin/mcp/tasks?${params.toString()}`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setTasks(await res.json() as TaskItem[]);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed');
    } finally {
      setLoading(false);
    }
  }

  async function fetchDetail(tid: string): Promise<void> {
    setDetailLoading(true);
    try {
      const res = await fetch(`/api/admin/mcp/tasks/${encodeURIComponent(tid)}`, { headers: authHeaders() });
      if (res.ok) setDetail(await res.json() as TaskDetail);
    } catch { /* ignore */ }
    finally { setDetailLoading(false); }
  }

  async function cancelTask(tid: string): Promise<void> {
    if (!confirm(`确定取消任务 ${tid}？`)) return;
    try {
      const res = await fetch(`/api/admin/mcp/tasks/${encodeURIComponent(tid)}/cancel`, {
        method: 'POST',
        headers: authHeaders(),
      });
      if (res.ok) {
        setActionMsg(`已取消 ${tid}`);
        void fetchTasks();
      }
    } catch (e) {
      setActionMsg(`Failed: ${e instanceof Error ? e.message : 'error'}`);
    }
  }

  async function fetchTemplates(): Promise<void> {
    setTemplatesLoading(true);
    try {
      const res = await fetch('/api/admin/mcp/tasks/templates/list', { headers: authHeaders() });
      if (res.ok) setTemplates(await res.json() as TemplateItem[]);
    } catch { /* ignore */ }
    finally { setTemplatesLoading(false); }
  }

  async function fetchRoutes(): Promise<void> {
    setRoutesLoading(true);
    try {
      const res = await fetch('/api/admin/mcp/tasks/routes/list', { headers: authHeaders() });
      if (res.ok) setRoutes(await res.json() as RouteItem[]);
    } catch { /* ignore */ }
    finally { setRoutesLoading(false); }
  }

  async function toggleRoute(rid: number): Promise<void> {
    try {
      await fetch(`/api/admin/mcp/tasks/routes/${rid}/toggle`, { method: 'POST', headers: authHeaders() });
      void fetchRoutes();
    } catch { /* ignore */ }
  }

  useEffect(() => { void fetchTasks(); }, [filterStatus]);
  useEffect(() => {
    if (subTab === 'templates') void fetchTemplates();
    if (subTab === 'routes') void fetchRoutes();
  }, [subTab]);

  return (
    <div className="space-y-4">
      {/* Sub-tabs */}
      <div className="flex items-center gap-1 border-b border-warm-150 pb-0">
        {(['tasks', 'history', 'templates', 'routes'] as const).map((k) => (
          <button
            key={k}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              subTab === k ? 'border-primary-400 text-primary-700' : 'border-transparent text-warm-500 hover:text-warm-700'
            }`}
            onClick={() => setSubTab(k)}
          >
            {{ tasks: '运行中', history: '历史', templates: '模板', routes: '路由' }[k]}
          </button>
        ))}
      </div>

      {/* Tasks view */}
      {subTab === 'tasks' && (
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_380px] gap-4">
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <select
                className="rounded-lg border border-warm-200 bg-warm-100 px-3 py-1.5 text-sm"
                value={filterStatus}
                onChange={(e) => setFilterStatus(e.target.value)}
              >
                <option value="">全部状态</option>
                <option value="RUNNING">运行中</option>
                <option value="PENDING">等待中</option>
                <option value="COMPLETED">已完成</option>
                <option value="FAILED">失败</option>
                <option value="CANCELLED">已取消</option>
              </select>
              <button className="btn-secondary text-sm px-3 py-1.5" onClick={() => void fetchTasks()}>刷新</button>
              {actionMsg && <span className="text-xs text-primary-600">{actionMsg}</span>}
            </div>
            {error && <div className="text-sm text-danger-500">{error}</div>}
            {loading ? (
              <div className="text-sm text-warm-400 py-8 text-center">加载中...</div>
            ) : tasks.length === 0 ? (
              <div className="text-sm text-warm-400 py-8 text-center">无运行中任务</div>
            ) : (
              tasks.map((t) => (
                <div
                  key={t.id}
                  className={`rounded-xl border bg-warm-100 px-4 py-3 cursor-pointer ${
                    selectedId === t.id ? 'border-primary-400 ring-1 ring-primary-200' : 'border-warm-200 hover:border-warm-300'
                  }`}
                  onClick={() => { setSelectedId(t.id); void fetchDetail(t.id); }}
                >
                  <div className="flex items-center justify-between">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono text-warm-600 truncate">{t.id}</span>
                        <StatusBadge
                          variant={t.status === 'RUNNING' ? 'running' : t.status === 'COMPLETED' ? 'success' : t.status === 'FAILED' ? 'critical' : 'pending'}
                          size="sm"
                          label={t.status}
                        />
                      </div>
                      <div className="text-xs text-warm-500 mt-0.5">
                        Session: {t.sessionId} · 进度: {t.completedNodes}/{t.totalNodes} ({t.progressPercent}%)
                      </div>
                      {/* Progress bar */}
                      <div className="mt-2 h-1.5 rounded-full bg-warm-100 overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all ${
                            t.status === 'FAILED' ? 'bg-danger-400' : t.status === 'COMPLETED' ? 'bg-success-400' : 'bg-primary-400'
                          }`}
                          style={{ width: `${t.progressPercent}%` }}
                        />
                      </div>
                    </div>
                    {(t.status === 'RUNNING' || t.status === 'PENDING') && (
                      <button
                        className="shrink-0 rounded bg-danger-100 px-2 py-1 text-[11px] text-danger-600 hover:bg-danger-200 ml-2"
                        onClick={(e) => { e.stopPropagation(); void cancelTask(t.id); }}
                      >
                        取消
                      </button>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
          {/* Task detail panel */}
          <div className="rounded-xl border border-warm-200 bg-warm-100 p-4 min-h-48">
            {!selectedId ? (
              <div className="flex flex-col items-center justify-center h-full text-center py-8">
                <span className="text-3xl mb-2">[left]</span>
                <p className="text-sm text-warm-400">选择任务查看详情</p>
              </div>
            ) : detailLoading ? (
              <div className="text-sm text-warm-400 py-8 text-center">加载中...</div>
            ) : detail ? (
              <div className="space-y-3">
                <h3 className="text-sm font-semibold text-warm-800 font-mono truncate">{detail.id}</h3>
                <div className="text-xs text-warm-500">
                  <p>状态: {detail.status}</p>
                  <p>Session: {detail.sessionId}</p>
                  <p>创建: {detail.createdAt ? new Date(detail.createdAt).toLocaleString() : '—'}</p>
                </div>
                {detail.executionHistory.length > 0 && (
                  <div>
                    <p className="text-xs text-warm-500 mb-1">执行历史</p>
                    <div className="max-h-40 overflow-auto space-y-1">
                      {detail.executionHistory.map((h: Record<string, unknown>, i: number) => (
                        <div key={i} className="text-xs text-warm-600 bg-warm-50 rounded px-2 py-1">
                          {h.taskType as string} · {(h.success as boolean) ? '[check]' : '[cross]'} · {(h.durationMs as number) || 0}ms
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="text-sm text-warm-400 py-8 text-center">无法加载详情</div>
            )}
          </div>
        </div>
      )}

      {/* Templates view */}
      {subTab === 'templates' && (
        <div>
          {templatesLoading ? (
            <div className="text-sm text-warm-400 py-8 text-center">加载中...</div>
          ) : templates.length === 0 ? (
            <div className="text-sm text-warm-400 py-8 text-center">无 DAG 模板</div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {templates.map((t) => (
                <div key={t.id} className="rounded-xl border border-warm-200 bg-warm-100 px-4 py-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold text-warm-800">{t.name}</span>
                    <span className="text-[10px] bg-warm-100 text-warm-500 rounded px-2">{t.category}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {t.keywords.map((kw) => (
                      <span key={kw} className="text-[10px] bg-primary-50 text-primary-600 rounded px-1.5">{kw}</span>
                    ))}
                  </div>
                  <div className="mt-2 text-xs text-warm-400">
                    {((t.dag as Record<string, unknown>)?.nodes as Array<Record<string, unknown>>)?.length || 0} 个节点 · 使用 {t.usageCount} 次
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Routes view */}
      {subTab === 'routes' && (
        <div>
          {routesLoading ? (
            <div className="text-sm text-warm-400 py-8 text-center">加载中...</div>
          ) : routes.length === 0 ? (
            <div className="text-sm text-warm-400 py-8 text-center">无 Agent 路由</div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {routes.map((r) => (
                <div key={r.id} className={`rounded-xl border bg-warm-100 px-4 py-3 ${r.isDefault ? 'border-primary-300 ring-1 ring-primary-100' : 'border-warm-200'}`}>
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="text-sm font-semibold text-warm-800">{r.name}</span>
                      {r.isDefault ? <span className="ml-2 text-[10px] bg-primary-100 text-primary-600 rounded px-1.5">默认</span> : null}
                      <StatusBadge variant={r.active ? 'online' : 'offline'} size="sm" label={r.active ? '启用' : '禁用'} />
                    </div>
                    <button
                      className={`text-[11px] rounded px-2 py-1 ${r.active ? 'bg-danger-50 text-danger-600 hover:bg-danger-100' : 'bg-success-50 text-success-600 hover:bg-success-100'}`}
                      onClick={() => void toggleRoute(r.id)}
                    >
                      {r.active ? '禁用' : '启用'}
                    </button>
                  </div>
                  <p className="mt-1 text-xs text-warm-500">{r.description}</p>
                  <div className="mt-2 flex items-center gap-1">
                    {r.nodes.map((n: Record<string, unknown>, i: number) => (
                      <span key={i} className="flex items-center gap-1 text-[10px]">
                        <span className="bg-warm-100 text-warm-600 rounded px-1.5">{n.agent as string}</span>
                        {i < r.nodes.length - 1 && <span className="text-warm-300">→</span>}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
