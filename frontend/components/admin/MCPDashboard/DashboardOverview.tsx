import type { JSX } from 'react';
import type { MCPDashboardData } from '../../../types';
import MetricCard from './shared/MetricCard';
import StatusBadge from './shared/StatusBadge';
import SimpleChart from './shared/SimpleChart';

interface DashboardOverviewProps {
  data: MCPDashboardData | null;
  loading: boolean;
  onRefresh: () => void;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
}

export default function DashboardOverview({ data, loading, onRefresh }: DashboardOverviewProps): JSX.Element {
  if (loading && !data) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="rounded-xl border bg-slate-50 px-5 py-4 animate-pulse">
              <div className="h-3 w-16 rounded bg-warm-200 mb-2" />
              <div className="h-8 w-20 rounded bg-warm-200" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center">
        <span className="text-5xl mb-4">📡</span>
        <p className="text-sm text-warm-500">无法加载仪表盘数据</p>
        <button className="mt-2 text-sm text-primary-500 underline" onClick={onRefresh}>重试</button>
      </div>
    );
  }

  const { health, agents, sessions, messages, tokens, tools, system, database, recentEvents } = data;

  // Prepare agent status chart data
  const agentChartData = Object.entries(agents.byStatus || {}).map(([status, info]) => ({
    label: status === 'online' ? '在线' : status === 'offline' ? '离线' : status === 'sleeping' ? '休眠' : status,
    value: info.count,
    color: status === 'online' ? '#10b981' : status === 'offline' ? '#ef4444' : '#f59e0b',
  }));

  // Prepare tool chart data
  const toolChartData = (tools.topTools || []).slice(0, 5).map((t: { name: string; count: number }) => ({
    label: t.name,
    value: t.count,
  }));

  return (
    <div className="space-y-6">
      {/* Health banner */}
      <div className={`rounded-xl border px-5 py-3 flex items-center gap-3 ${
        health?.status === 'healthy' ? 'bg-green-50 border-green-200' : 'bg-amber-50 border-amber-200'
      }`}>
        <span className="text-2xl">{health?.status === 'healthy' ? '✅' : '⚠️'}</span>
        <div>
          <span className={`text-sm font-semibold ${health?.status === 'healthy' ? 'text-green-700' : 'text-amber-700'}`}>
            系统状态：{health?.status === 'healthy' ? '健康' : '降级'}
          </span>
          {health?.issues && health.issues.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {health.issues.map((issue, i) => (
                <span key={i} className="inline-block rounded bg-red-100 px-2 py-0.5 text-xs text-red-600">{issue}</span>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Metric cards row 1 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="Agents"
          value={agents.total}
          subtitle={`在线 ${agents.byStatus?.online?.count || 0} / 离线 ${agents.byStatus?.offline?.count || 0} / 休眠 ${agents.byStatus?.sleeping?.count || 0}`}
          icon="🤖"
          color="blue"
        />
        <MetricCard
          title="活跃 WebSocket"
          value={sessions.activeWebSocket}
          subtitle={`今日 ${sessions.today} 个会话`}
          icon="💬"
          color={sessions.activeWebSocket > 0 ? 'green' : 'slate'}
        />
        <MetricCard
          title="今日消息"
          value={messages.today}
          subtitle={`本周 ${messages.thisWeek} 条消息`}
          icon="📨"
          color="blue"
        />
        <MetricCard
          title="今日 Token"
          value={formatTokens(tokens.today)}
          subtitle={`本周 ${formatTokens(tokens.thisWeek)}`}
          icon="⚡"
          color="amber"
        />
      </div>

      {/* Metric cards row 2 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="工具调用 (今日)"
          value={tools.todayCalls}
          subtitle={`成功率 ${tools.todayCalls > 0 ? Math.round((tools.todaySuccess / tools.todayCalls) * 100) : 0}%`}
          icon="🔧"
          color={tools.todaySuccess === tools.todayCalls ? 'green' : 'amber'}
        />
        <MetricCard
          title="CPU"
          value={`${system.cpuPercent > 0 ? system.cpuPercent : '—'}%`}
          subtitle={system.note}
          icon="🖥️"
          color={system.cpuPercent > 80 ? 'red' : system.cpuPercent > 50 ? 'amber' : 'green'}
        />
        <MetricCard
          title="内存"
          value={`${system.memoryPercent > 0 ? system.memoryPercent : '—'}%`}
          subtitle={system.memoryUsedGB > 0 ? `${system.memoryUsedGB} / ${system.memoryTotalGB} GB` : 'psutil 未安装'}
          icon="🧠"
          color={system.memoryPercent > 80 ? 'red' : system.memoryPercent > 50 ? 'amber' : 'green'}
        />
        <MetricCard
          title="数据库"
          value={database.connected ? '已连接' : '异常'}
          subtitle={`连接池 ${database.poolSize || '?'}`}
          icon="🗄️"
          color={database.connected ? 'green' : 'red'}
        />
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Agent status distribution */}
        <div className="rounded-xl border border-warm-200 bg-white px-5 py-4">
          <h3 className="text-sm font-semibold text-warm-800 mb-3">Agent 状态分布</h3>
          <SimpleChart data={agentChartData} type="donut" height={180} />
          {/* Adapter breakdown */}
          <div className="mt-4 border-t border-warm-100 pt-3">
            <p className="text-xs text-warm-500 mb-2">各类型分布</p>
            <div className="flex flex-wrap gap-2">
              {Object.entries(
                Object.values(agents.byStatus || {}).reduce<Record<string, number>>((acc, s) => {
                  Object.entries(s.adapters as Record<string, number> || {}).forEach(([k, v]) => {
                    acc[k] = (acc[k] || 0) + v;
                  });
                  return acc;
                }, {})
              ).map(([adapter, count]) => (
                <span key={adapter} className="rounded bg-warm-100 px-2 py-0.5 text-xs text-warm-600">
                  {adapter || '(default)'}: {count}
                </span>
              ))}
            </div>
          </div>
        </div>

        {/* Top tools chart */}
        <div className="rounded-xl border border-warm-200 bg-white px-5 py-4">
          <h3 className="text-sm font-semibold text-warm-800 mb-3">最常用工具 TOP 5</h3>
          {toolChartData.length > 0 ? (
            <SimpleChart data={toolChartData} type="bar" height={180} />
          ) : (
            <div className="flex items-center justify-center text-xs text-warm-400" style={{ height: 180 }}>
              今日尚无工具调用
            </div>
          )}
          {/* Token per model */}
          {tokens.perModel && tokens.perModel.length > 0 && (
            <div className="mt-4 border-t border-warm-100 pt-3">
              <p className="text-xs text-warm-500 mb-2">各模型 Token 消耗</p>
              <div className="space-y-1.5">
                {tokens.perModel.slice(0, 5).map((m: { model: string; tokens: number }) => (
                  <div key={m.model} className="flex items-center gap-2 text-xs">
                    <span className="w-20 truncate text-warm-600">{m.model}</span>
                    <div className="flex-1 h-3 rounded-full bg-warm-100 overflow-hidden">
                      <div
                        className="h-full rounded-full bg-primary-400 transition-all"
                        style={{ width: `${Math.min(100, (m.tokens / Math.max(...tokens.perModel.map((x: { tokens: number }) => x.tokens), 1)) * 100)}%` }}
                      />
                    </div>
                    <span className="w-16 text-right text-warm-500 font-mono">{formatTokens(m.tokens)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Performance metrics */}
      {data.performance && Object.keys(data.performance).length > 0 && (
        <div className="rounded-xl border border-warm-200 bg-white px-5 py-4">
          <h3 className="text-sm font-semibold text-warm-800 mb-3">性能指标</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            {((): JSX.Element[] => {
              const models = data.performance.models as Record<string, Record<string, number>> | undefined;
              if (!models) return [];
              return Object.entries(models).slice(0, 4).map(([model, metrics]) => (
                <div key={model} className="rounded-lg bg-warm-50 px-3 py-2">
                  <p className="font-medium text-warm-700 truncate">{model}</p>
                  <p className="text-warm-500 mt-1">
                    avg: {metrics.avgLatencyMs?.toFixed(0) || '—'}ms · p95: {metrics.p95LatencyMs?.toFixed(0) || '—'}ms
                  </p>
                  <p className="text-warm-400">
                    success: {metrics.successRate != null ? `${(metrics.successRate * 100).toFixed(1)}%` : '—'}
                  </p>
                </div>
              ));
            })()}
          </div>
        </div>
      )}

      {/* Recent events stream */}
      {recentEvents && recentEvents.length > 0 && (
        <div className="rounded-xl border border-warm-200 bg-white px-5 py-4">
          <h3 className="text-sm font-semibold text-warm-800 mb-3">最近事件</h3>
          <div className="space-y-1 max-h-40 overflow-y-auto">
            {(() => {
              const events = recentEvents as Array<{ timestamp?: string; decision?: string; action?: string; agentId?: string; riskLevel?: string }>;
              return events.slice(0, 15).map((event, i) => (
                <div key={i} className="flex items-center gap-3 text-xs py-1 border-b border-warm-50 last:border-0">
                  <span className="w-16 text-warm-400 font-mono shrink-0">
                    {event.timestamp?.slice(11, 19) || ''}
                  </span>
                  <StatusBadge
                    variant={event.decision === 'approve' ? 'success' : 'warning'}
                    size="sm"
                  />
                  <span className="text-warm-600 font-medium">{event.action}</span>
                  <span className="text-warm-400">{event.agentId}</span>
                  <span className={`ml-auto font-mono text-[10px] ${
                    event.riskLevel === 'L3' ? 'text-red-500' :
                    event.riskLevel === 'L2' ? 'text-amber-500' : 'text-warm-400'
                  }`}>
                    {event.riskLevel}
                  </span>
                </div>
              ));
            })()}
          </div>
        </div>
      )}
    </div>
  );
}
