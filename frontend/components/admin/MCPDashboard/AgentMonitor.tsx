import { useEffect, useState, type JSX } from 'react';
import StatusBadge from './shared/StatusBadge';

interface AgentInfo {
  agentId: string;
  userId: string;
  domain: string;
  status: string;
  adapterType: string;
  baseModelName: string;
  riskLevel: string;
  dutyNote: string;
  displayName: string;
  avatarUrl: string;
  capabilityTags: string[];
  baseUrl: string;
}

interface AgentStats {
  agentId: string;
  todayCalls: number;
  weekCalls: number;
  todayTokens: number;
  toolStats: Array<{ name: string; count: number; successCount: number }>;
  recentCalls: Array<{ timestamp: string; tokens: number; type: string }>;
}

function authHeaders(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  const token = localStorage.getItem('agenthub_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export default function AgentMonitor(): JSX.Element {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [filterDomain, setFilterDomain] = useState('');
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [agentStats, setAgentStats] = useState<AgentStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);
  const [actionMsg, setActionMsg] = useState('');

  async function fetchAgents(): Promise<void> {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams();
      if (filterStatus) params.set('status', filterStatus);
      if (filterDomain) params.set('domain', filterDomain);
      const res = await fetch(`/api/admin/mcp/agents?${params.toString()}`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setAgents(await res.json() as AgentInfo[]);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load agents');
    } finally {
      setLoading(false);
    }
  }

  async function fetchAgentStats(agentId: string): Promise<void> {
    setStatsLoading(true);
    try {
      const res = await fetch(`/api/admin/mcp/agents/${encodeURIComponent(agentId)}/stats`, { headers: authHeaders() });
      if (res.ok) setAgentStats(await res.json() as AgentStats);
    } catch { /* ignore */ }
    finally { setStatsLoading(false); }
  }

  async function setStatus(agentId: string, newStatus: string): Promise<void> {
    setActionMsg('');
    try {
      const res = await fetch(`/api/admin/mcp/agents/${encodeURIComponent(agentId)}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ status: newStatus }),
      });
      if (res.ok) {
        setActionMsg(`${agentId} → ${newStatus}`);
        void fetchAgents();
      }
    } catch (e) {
      setActionMsg(`Failed: ${e instanceof Error ? e.message : 'error'}`);
    }
  }

  async function cancelAgent(agentId: string): Promise<void> {
    if (!confirm(`强制终止 ${agentId} 的所有运行中调用？`)) return;
    try {
      await fetch(`/api/admin/mcp/agents/${encodeURIComponent(agentId)}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
      });
      setActionMsg(`${agentId} 已发送取消信号`);
    } catch (e) {
      setActionMsg(`Failed: ${e instanceof Error ? e.message : 'error'}`);
    }
  }

  useEffect(() => { void fetchAgents(); }, [filterStatus, filterDomain]);

  useEffect(() => {
    if (selectedAgent) {
      void fetchAgentStats(selectedAgent);
    } else {
      setAgentStats(null);
    }
  }, [selectedAgent]);

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <select
          className="rounded-lg border border-warm-200 bg-warm-100 px-3 py-1.5 text-sm outline-none focus:border-primary-300"
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
        >
          <option value="">全部状态</option>
          <option value="online">在线</option>
          <option value="offline">离线</option>
          <option value="sleeping">休眠</option>
        </select>
        <select
          className="rounded-lg border border-warm-200 bg-warm-100 px-3 py-1.5 text-sm outline-none focus:border-primary-300"
          value={filterDomain}
          onChange={(e) => setFilterDomain(e.target.value)}
        >
          <option value="">全部领域</option>
          <option value="orchestrator">调度</option>
          <option value="architect">架构</option>
          <option value="codegen">代码生成</option>
          <option value="review">审查</option>
          <option value="test">测试</option>
          <option value="deploy">部署</option>
        </select>
        <button className="btn-secondary text-sm px-3 py-1.5" onClick={() => void fetchAgents()}>
          {loading ? '加载中...' : '刷新'}
        </button>
        {actionMsg && <span className="text-xs text-primary-600">{actionMsg}</span>}
      </div>

      {error && <div className="text-sm text-danger-500">{error}</div>}

      {/* Agent list + detail split */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_380px] gap-4">
        {/* Left: Agent list */}
        <div className="space-y-2">
          {loading ? (
            <div className="text-sm text-warm-400 py-8 text-center">加载中...</div>
          ) : agents.length === 0 ? (
            <div className="text-sm text-warm-400 py-8 text-center">无匹配 Agent</div>
          ) : (
            agents.map((a) => (
              <div
                key={a.agentId}
                className={`rounded-xl border bg-warm-100 px-4 py-3 cursor-pointer transition-colors ${
                  selectedAgent === a.agentId ? 'border-primary-400 ring-1 ring-primary-200' : 'border-warm-200 hover:border-warm-300'
                }`}
                onClick={() => setSelectedAgent(selectedAgent === a.agentId ? null : a.agentId)}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 flex items-center gap-3">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-warm-200 text-warm-600 text-xs font-bold">
                      {(a.displayName || a.agentId)[0]}
                    </span>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold text-warm-800 truncate">{a.agentId}</span>
                        <StatusBadge variant={a.status as 'online' | 'offline' | 'sleeping'} size="sm" />
                      </div>
                      <div className="text-xs text-warm-500 mt-0.5">
                        {a.adapterType || 'default'} · {a.domain || '—'} · {a.riskLevel || 'L1'}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                    {a.status !== 'online' && (
                      <button className="rounded bg-success-100 px-2 py-1 text-[11px] text-success-700 hover:bg-success-200" onClick={() => void setStatus(a.agentId, 'online')}>
                        上线
                      </button>
                    )}
                    {a.status === 'online' && (
                      <button className="rounded bg-warning-100 px-2 py-1 text-[11px] text-warning-700 hover:bg-warning-200" onClick={() => void setStatus(a.agentId, 'offline')}>
                        下线
                      </button>
                    )}
                    <button className="rounded bg-danger-100 px-2 py-1 text-[11px] text-danger-600 hover:bg-danger-200" onClick={() => void cancelAgent(a.agentId)}>
                      终止
                    </button>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>

        {/* Right: Agent detail stats */}
        <div className="rounded-xl border border-warm-200 bg-warm-100 p-4 min-h-48">
          {!selectedAgent ? (
            <div className="flex flex-col items-center justify-center h-full text-center py-8">
              <span className="text-3xl mb-2">[left]</span>
              <p className="text-sm text-warm-400">选择一个 Agent 查看详情</p>
            </div>
          ) : statsLoading ? (
            <div className="text-sm text-warm-400 py-8 text-center">加载统计中...</div>
          ) : agentStats ? (
            <div className="space-y-4">
              <h3 className="text-sm font-semibold text-warm-800">{agentStats.agentId} 统计</h3>
              <div className="grid grid-cols-3 gap-2">
                <div className="rounded-lg bg-primary-50 px-3 py-2 text-center">
                  <div className="text-lg font-bold text-primary-700">{agentStats.todayCalls}</div>
                  <div className="text-[10px] text-primary-500">今日调用</div>
                </div>
                <div className="rounded-lg bg-warning-50 px-3 py-2 text-center">
                  <div className="text-lg font-bold text-warning-700">{agentStats.weekCalls}</div>
                  <div className="text-[10px] text-warning-500">本周调用</div>
                </div>
                <div className="rounded-lg bg-success-50 px-3 py-2 text-center">
                  <div className="text-lg font-bold text-success-700">{(agentStats.todayTokens / 1000).toFixed(1)}K</div>
                  <div className="text-[10px] text-success-500">今日 Token</div>
                </div>
              </div>
              {agentStats.toolStats.length > 0 && (
                <div>
                  <p className="text-xs text-warm-500 mb-2">工具使用</p>
                  {agentStats.toolStats.map((t) => (
                    <div key={t.name} className="flex items-center gap-2 text-xs py-0.5">
                      <span className="text-warm-600 w-24 truncate">{t.name}</span>
                      <span className="text-warm-400">{t.count}次</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="text-sm text-warm-400 py-8 text-center">暂无统计数据</div>
          )}
        </div>
      </div>
    </div>
  );
}
