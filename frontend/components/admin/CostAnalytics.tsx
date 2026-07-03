'use client';

import { useEffect, useState } from 'react';
import { useCostStore, type UsageDay } from '../../stores/costStore';

// ── Helpers ───────────────────────────────────────────────────────────────

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatCost(usd: number): string {
  if (usd >= 100) return `$${usd.toFixed(0)}`;
  if (usd >= 1) return `$${usd.toFixed(2)}`;
  return `$${usd.toFixed(4)}`;
}

function formatPct(n: number, total: number): string {
  if (total === 0) return '0%';
  return `${((n / total) * 100).toFixed(1)}%`;
}

function usagePercent(used: number, limit: number): number {
  if (limit <= 0) return 0;
  return Math.min(100, (used / limit) * 100);
}

function barColor(pct: number): string {
  if (pct >= 90) return 'bg-red-500';
  if (pct >= 70) return 'bg-amber-500';
  if (pct >= 50) return 'bg-blue-500';
  return 'bg-emerald-500';
}

// ── Simple bar chart (no external libs) ───────────────────────────────────

function TinyBarChart({ data, height = 120 }: { data: UsageDay[]; height?: number }) {
  if (!data.length) return <p className="text-xs text-warm-400 py-4 text-center">暂无数据</p>;

  const maxVal = Math.max(...data.map((d) => d.tokens), 1);
  return (
    <div className="flex items-end gap-1" style={{ height }}>
      {data.slice(-30).map((d, i) => {
        const h = Math.max(2, (d.tokens / maxVal) * height);
        return (
          <div
            key={i}
            className="flex-1 rounded-t bg-primary-400/60 hover:bg-primary-500 transition-colors"
            style={{ height: `${h}px`, minWidth: 2 }}
            title={`${d.date}: ${formatTokens(d.tokens)} tokens`}
          />
        );
      })}
    </div>
  );
}

// ── Anomaly badge ─────────────────────────────────────────────────────────

function AnomalyBadge({ days }: { days: UsageDay[] }) {
  if (days.length < 2) return null;
  const today = days[days.length - 1]?.tokens ?? 0;
  const yesterday = days[days.length - 2]?.tokens ?? 1;
  const spike = yesterday > 0 ? ((today - yesterday) / yesterday) * 100 : 0;

  if (spike > 200) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-semibold text-red-700 ring-1 ring-red-200">
        🚨 异常增长 +{spike.toFixed(0)}%
      </span>
    );
  }
  if (spike > 100) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-700 ring-1 ring-amber-200">
        ⚠️ 大幅增长 +{spike.toFixed(0)}%
      </span>
    );
  }
  return null;
}

// ── Main component ────────────────────────────────────────────────────────

export default function CostAnalytics(): JSX.Element {
  const {
    costLoading,
    costError,
    tokenData,
    billingByTenant,
    costEstimate,
    init,
  } = useCostStore();

  const [selectedTenant, setSelectedTenant] = useState<string>('');

  useEffect(() => {
    init();
  }, []);

  const days = tokenData?.days ?? [];
  const totalTokens = days.reduce((sum, d) => sum + d.tokens, 0);
  const totalSessions = days.reduce((sum, d) => sum + d.sessions, 0);
  const avgDailyTokens = days.length > 0 ? Math.round(totalTokens / days.length) : 0;
  const todayTokens = days.length > 0 ? days[days.length - 1].tokens : 0;
  const yesterdayTokens = days.length > 1 ? days[days.length - 2].tokens : 0;
  const dailyTrend = yesterdayTokens > 0 ? ((todayTokens - yesterdayTokens) / yesterdayTokens) * 100 : 0;

  const activeTenant = billingByTenant.find((t) => t.tenant_id === selectedTenant) || billingByTenant[0];

  return (
    <section className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-h3">💰 成本分析</h2>
          <p className="mt-1 text-sm text-warm-500">
            Token 消耗、成本估算与用量趋势
          </p>
        </div>
        {costError && (
          <span className="text-xs text-red-500 bg-red-50 px-2 py-1 rounded">{costError}</span>
        )}
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="card p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-warm-500">今日 Token</p>
          <p className="mt-1 text-2xl font-bold text-warm-900">
            {costLoading ? <span className="h-7 w-16 animate-pulse rounded bg-warm-200 inline-block" /> : formatTokens(todayTokens)}
          </p>
          <p className={`mt-0.5 text-xs font-medium ${dailyTrend >= 0 ? 'text-green-600' : 'text-red-500'}`}>
            {dailyTrend >= 0 ? '↑' : '↓'} {Math.abs(dailyTrend).toFixed(1)}% vs 昨日
          </p>
        </div>

        <div className="card p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-warm-500">月度总量</p>
          <p className="mt-1 text-2xl font-bold text-warm-900">
            {costLoading ? <span className="h-7 w-16 animate-pulse rounded bg-warm-200 inline-block" /> : formatTokens(totalTokens)}
          </p>
          <p className="mt-0.5 text-xs text-warm-400">{days.length} 天数据</p>
        </div>

        <div className="card p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-warm-500">估算成本</p>
          <p className="mt-1 text-2xl font-bold text-emerald-700">
            {costEstimate ? formatCost(costEstimate.total) : '—'}
          </p>
          <p className="mt-0.5 text-xs text-warm-400">基于模型定价估算</p>
        </div>

        <div className="card p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-warm-500">日均 Token</p>
          <p className="mt-1 text-2xl font-bold text-warm-900">{formatTokens(avgDailyTokens)}</p>
          <p className="mt-0.5 text-xs text-warm-400">
            会话 {totalSessions} 次
            <AnomalyBadge days={days} />
          </p>
        </div>
      </div>

      {/* Usage chart */}
      <div className="card p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-warm-800">📈 30 天 Token 消耗趋势</h3>
          <AnomalyBadge days={days} />
        </div>
        <TinyBarChart data={days} height={140} />
        <div className="mt-2 flex justify-between text-[11px] text-warm-400">
          <span>{days.length > 0 ? days[0]?.date : ''}</span>
          <span>{days.length > 0 ? days[days.length - 1]?.date : ''}</span>
        </div>
      </div>

      {/* Tenant billing */}
      {billingByTenant.length > 0 && (
        <div className="card p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-warm-800">🏢 租户配额 & 账单</h3>
            <select
              className="text-sm border border-warm-200 rounded-lg px-3 py-1.5 bg-white text-warm-700"
              value={selectedTenant}
              onChange={(e) => setSelectedTenant(e.target.value)}
            >
              <option value="">选择租户...</option>
              {billingByTenant.map((t) => (
                <option key={t.tenant_id} value={t.tenant_id}>{t.tenant_id}</option>
              ))}
            </select>
          </div>

          {activeTenant && (
            <div className="space-y-4">
              {/* Plan badge */}
              <div className="flex items-center gap-3">
                <span className={`inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs font-semibold ring-1 ${
                  activeTenant.plan === 'enterprise'
                    ? 'bg-purple-50 text-purple-700 ring-purple-200'
                    : activeTenant.plan === 'pro'
                    ? 'bg-blue-50 text-blue-700 ring-blue-200'
                    : 'bg-warm-100 text-warm-600 ring-warm-200'
                }`}>
                  {activeTenant.plan.toUpperCase()}
                </span>
                <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${
                  activeTenant.status === 'open' ? 'bg-green-50 text-green-600 ring-green-200' : 'bg-warm-100 text-warm-500 ring-warm-200'
                }`}>
                  {activeTenant.status === 'open' ? '● 进行中' : '○ 已关闭'}
                </span>
              </div>

              {/* Token quota bar */}
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-warm-500">月度 Token 配额</span>
                  <span className="text-warm-700 font-medium">
                    {formatTokens(activeTenant.used_tokens)} / {formatTokens(activeTenant.monthly_tokens)}
                    ({formatPct(activeTenant.used_tokens, activeTenant.monthly_tokens)})
                  </span>
                </div>
                <div className="h-3 w-full rounded-full bg-warm-100 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${barColor(usagePercent(activeTenant.used_tokens, activeTenant.monthly_tokens))}`}
                    style={{ width: `${usagePercent(activeTenant.used_tokens, activeTenant.monthly_tokens)}%` }}
                  />
                </div>
              </div>

              {/* Other metrics */}
              <div className="grid grid-cols-3 gap-4">
                <div className="rounded-lg bg-warm-50 p-3">
                  <p className="text-[11px] text-warm-500">已用会话</p>
                  <p className="text-lg font-bold text-warm-800">{activeTenant.used_sessions}</p>
                </div>
                <div className="rounded-lg bg-warm-50 p-3">
                  <p className="text-[11px] text-warm-500">活跃 Agent</p>
                  <p className="text-lg font-bold text-warm-800">{activeTenant.used_agents}</p>
                </div>
                <div className="rounded-lg bg-warm-50 p-3">
                  <p className="text-[11px] text-warm-500">计费周期</p>
                  <p className="text-sm font-medium text-warm-700">
                    {new Date(activeTenant.cycle_start).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })}
                    {' → '}
                    {new Date(activeTenant.cycle_end).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })}
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Cost breakdown table */}
      {costEstimate && costEstimate.byModel.length > 0 && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-warm-800 mb-3">📊 模型成本分布</h3>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-warm-100 text-left text-xs uppercase tracking-wide text-warm-500">
                <th className="pb-2 font-medium">模型</th>
                <th className="pb-2 font-medium text-right">Token</th>
                <th className="pb-2 font-medium text-right">成本</th>
                <th className="pb-2 font-medium text-right">占比</th>
              </tr>
            </thead>
            <tbody>
              {costEstimate.byModel.map((m) => (
                <tr key={m.model} className="border-b border-warm-50 last:border-0">
                  <td className="py-2 font-medium text-warm-700">{m.model}</td>
                  <td className="py-2 text-right text-warm-600 font-mono text-xs">{formatTokens(m.tokens)}</td>
                  <td className="py-2 text-right font-mono text-xs text-emerald-700">{formatCost(m.cost)}</td>
                  <td className="py-2 text-right text-warm-500">{formatPct(m.cost, costEstimate.total)}</td>
                </tr>
              ))}
              <tr className="font-semibold">
                <td className="pt-2 text-warm-800">合计</td>
                <td className="pt-2 text-right text-warm-800 font-mono text-xs">{formatTokens(costEstimate.byModel.reduce((s, m) => s + m.tokens, 0))}</td>
                <td className="pt-2 text-right font-mono text-xs text-emerald-700">{formatCost(costEstimate.total)}</td>
                <td className="pt-2 text-right text-warm-800">100%</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
