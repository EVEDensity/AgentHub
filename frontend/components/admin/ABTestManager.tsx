'use client';

// Agent A/B Testing Manager (L5)
// Create, run, and analyze A/B tests comparing agent variants.
// L5: Real backend API integration with statistical significance analysis,
// p-value display, Cohen's d effect size, confidence intervals, and
// side-by-side variant comparison charts.

import { useState, useEffect, useCallback, type JSX } from 'react';
import {
  useABTestStore,
  type ABTestComputedResult,
  type BackendVariantStats,
} from '../../stores/abTestStore';

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  running: '运行中',
  paused: '已暂停',
  completed: '已完成',
};
const STATUS_COLORS: Record<string, string> = {
  draft: 'bg-warm-100 text-warm-600',
  running: 'bg-green-100 text-green-700',
  paused: 'bg-amber-100 text-amber-700',
  completed: 'bg-blue-100 text-blue-700',
};

// ── Helpers ────────────────────────────────────────────────────────────

function formatPValue(p: number): string {
  if (p < 0.001) return 'p < 0.001';
  if (p < 0.01) return `p = ${p.toFixed(3)}`;
  return `p = ${p.toFixed(4)}`;
}

function confidenceLabel(level: number): { text: string; color: string } {
  if (level >= 99) return { text: '99%+ 极显著', color: 'bg-green-100 text-green-700' };
  if (level >= 95) return { text: '95% 显著', color: 'bg-green-100 text-green-700' };
  if (level >= 90) return { text: '90% 趋势', color: 'bg-amber-100 text-amber-700' };
  if (level >= 50) return { text: `${level.toFixed(0)}% 收集`, color: 'bg-warm-100 text-warm-600' };
  return { text: '数据不足', color: 'bg-warm-100 text-warm-600' };
}

function effectSizeLabel(d: number): string {
  const abs = Math.abs(d);
  if (abs >= 0.8) return '(大)';
  if (abs >= 0.5) return '(中)';
  if (abs >= 0.2) return '(小)';
  return '(极小)';
}

// ── Sub-components ────────────────────────────────────────────────────

function MiniBar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="h-2 bg-warm-150 rounded-full overflow-hidden">
      <div className={`h-full rounded-full transition-all duration-500 ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center text-xs">
      <span className="text-warm-500">{label}</span>
      <span className="font-mono font-medium text-warm-700">{value}</span>
    </div>
  );
}

function SignificanceBadge({ significance }: { significance: number }) {
  const { text, color } = confidenceLabel(significance);
  return <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${color}`}>{text}</span>;
}

function WinnerBadge({ variantId, confidence }: { variantId: string; confidence: number }) {
  return (
    <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-semibold border border-green-200">
      <span className="text-[14px]">[trophy]</span>
      Variant {variantId} 胜出 ({confidence.toFixed(0)}%)
    </span>
  );
}

function SideBySideBarChart({
  labelA, valueA, labelB, valueB,
  maxValue, unit, colorA, colorB, title,
}: {
  labelA: string; valueA: number; labelB: string; valueB: number;
  maxValue: number; unit: string; colorA: string; colorB: string; title: string;
}) {
  const pctA = maxValue > 0 ? Math.min((valueA / maxValue) * 100, 100) : 0;
  const pctB = maxValue > 0 ? Math.min((valueB / maxValue) * 100, 100) : 0;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-[10px] text-warm-400">
        <span>{title}</span>
        <span className="font-mono">
          <span className={colorA.replace('bg-', 'text-')}>{valueA.toFixed(1)}{unit}</span>
          {' / '}
          <span className={colorB.replace('bg-', 'text-')}>{valueB.toFixed(1)}{unit}</span>
        </span>
      </div>
      <div className="flex gap-1 h-3.5">
        <div className="flex-1 bg-warm-100 rounded-full overflow-hidden flex flex-col justify-center">
          <div className={`h-full rounded-full transition-all duration-500 ${colorA}`} style={{ width: `${pctA}%` }} />
        </div>
        <span className="text-[9px] text-warm-400 w-5 text-center self-center">{labelA}</span>
        <span className="text-[9px] text-warm-400 w-5 text-center self-center">{labelB}</span>
        <div className="flex-1 bg-warm-100 rounded-full overflow-hidden flex flex-col justify-center">
          <div className={`h-full rounded-full transition-all duration-500 ${colorB}`} style={{ width: `${pctB}%` }} />
        </div>
      </div>
    </div>
  );
}

function VariantMetricsCard({
  label,
  metrics,
  isWinner,
}: {
  label: string;
  metrics: import('../../stores/abTestStore').VariantMetrics;
  isWinner?: boolean;
}) {
  if (!metrics) return null;
  return (
    <div className={`rounded-xl border p-4 ${isWinner ? 'border-green-300 bg-green-50/50' : 'border-warm-200 bg-warm-100'}`}>
      <div className="flex items-center gap-2 mb-3">
        <h4 className="text-sm font-semibold text-warm-800">Variant {label}</h4>
        {isWinner && <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-100 text-green-700 font-medium">[trophy] 胜出</span>}
      </div>
      <div className="space-y-2.5">
        <MetricRow label="请求数" value={metrics.requests.toLocaleString()} />
        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-warm-500">质量评分</span>
            <span className="font-mono font-medium text-warm-700">{metrics.avgQuality.toFixed(1)}/10</span>
          </div>
          <MiniBar value={metrics.avgQuality} max={10} color="bg-primary-400" />
        </div>
        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-warm-500">满意度</span>
            <span className="font-mono font-medium text-warm-700">{metrics.userSatisfaction.toFixed(1)}/10</span>
          </div>
          <MiniBar value={metrics.userSatisfaction} max={10} color="bg-amber-400" />
        </div>
        <MetricRow label="平均延迟" value={`${metrics.avgLatencyMs.toFixed(0)}ms`} />
        <MetricRow label="平均 Token" value={metrics.avgTokenUsage.toLocaleString()} />
        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-warm-500">成功率</span>
            <span className="font-mono font-medium text-warm-700">{(metrics.successRate * 100).toFixed(1)}%</span>
          </div>
          <MiniBar value={metrics.successRate * 100} max={100} color="bg-green-400" />
        </div>
      </div>
    </div>
  );
}

// L5: Backend-powered variant stats card (uses BackendVariantStats).
function BackendVariantCard({
  variantId,
  stats,
  isWinner,
}: {
  variantId: string;
  stats: BackendVariantStats;
  isWinner?: boolean;
}) {
  return (
    <div className={`rounded-xl border p-4 ${isWinner ? 'border-green-300 bg-green-50/50' : 'border-warm-200 bg-warm-100'}`}>
      <div className="flex items-center gap-2 mb-3">
        <h4 className="text-sm font-semibold text-warm-800">Variant {variantId}</h4>
        {isWinner && <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-100 text-green-700 font-medium">[trophy] 胜出</span>}
      </div>
      <div className="space-y-2.5">
        <MetricRow label="样本量" value={stats.count.toLocaleString()} />
        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-warm-500">平均质量</span>
            <span className="font-mono font-medium text-warm-700">{stats.mean_quality.toFixed(2)}</span>
          </div>
          <MiniBar value={stats.mean_quality} max={10} color="bg-primary-400" />
        </div>
        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-warm-500">平均满意度</span>
            <span className="font-mono font-medium text-warm-700">{stats.mean_satisfaction.toFixed(2)}</span>
          </div>
          <MiniBar value={stats.mean_satisfaction} max={10} color="bg-amber-400" />
        </div>
        <MetricRow label="平均延迟" value={`${stats.mean_latency_ms.toFixed(0)}ms`} />
        <MetricRow label="平均 Token" value={stats.mean_tokens.toLocaleString()} />
        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-warm-500">成功率</span>
            <span className="font-mono font-medium text-warm-700">{(stats.success_rate * 100).toFixed(1)}%</span>
          </div>
          <MiniBar value={stats.success_rate * 100} max={100} color="bg-green-400" />
        </div>
      </div>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────

export default function ABTestManager(): JSX.Element {
  const {
    tests, selectedTestId, loading, error, demoMode, computedResults,
    loadTests, selectTest, createTest, startTest, pauseTest, completeTest, deleteTest, getWinner,
    getResults,
  } = useABTestStore();

  const [showCreate, setShowCreate] = useState(false);
  const [computingId, setComputingId] = useState<string | null>(null);
  const [createForm, setCreateForm] = useState({
    name: '',
    description: '',
    agentId: '',
    trafficSplit: 50,
  });

  useEffect(() => { void loadTests(); }, []);

  const selectedTest = tests.find((t) => t.id === selectedTestId) || null;
  const winner = selectedTest ? getWinner(selectedTest) : null;
  const backendResult: ABTestComputedResult | null =
    selectedTestId ? (computedResults[selectedTestId] ?? null) : null;

  // Auto-load backend results for completed experiments.
  useEffect(() => {
    if (selectedTest && selectedTest.status === 'completed' && !backendResult) {
      void getResults(selectedTest.id);
    }
  }, [selectedTest?.id, selectedTest?.status]);

  const handleComputeResults = useCallback(async (id: string) => {
    setComputingId(id);
    try {
      await completeTest(id);
    } finally {
      setComputingId(null);
    }
  }, [completeTest]);

  // Build demo-mode metrics for display even when backend result exists.
  const demoMetrics = selectedTest?.metrics;
  const variantA = demoMetrics?.variantA;
  const variantB = demoMetrics?.variantB;

  return (
    <section className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-warm-900 flex items-center gap-2">
            <span className="material-symbols-outlined text-primary-500">science</span>
            Agent A/B 测试
          </h3>
          <p className="text-sm text-warm-500 mt-0.5">
            流量分割 · 质量对比 · 统计显著性 (t检验) · 数据驱动 Agent 优化决策
          </p>
        </div>
        <div className="flex items-center gap-2">
          {demoMode && (
            <span className="tag tag-amber text-[11px] flex items-center gap-1">
              <span className="material-symbols-outlined text-[14px]">info</span>Demo
            </span>
          )}
          {error && (
            <span className="tag tag-red text-[11px] flex items-center gap-1">
              <span className="material-symbols-outlined text-[14px]">error</span>{error}
            </span>
          )}
          <button onClick={() => setShowCreate(true)} className="btn-primary text-sm">+ 新建测试</button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Left: Test List */}
        <div className="lg:col-span-1 space-y-2 max-h-[600px] overflow-y-auto">
          {loading ? (
            Array.from({ length: 3 }).map((_, i) => <div key={i} className="skeleton skeleton-text h-20 rounded-xl" />)
          ) : tests.length === 0 ? (
            <div className="rounded-xl border border-dashed border-warm-300 bg-warm-50 px-4 py-10 text-center">
              <span className="material-symbols-outlined text-3xl text-warm-300 mb-2 block">experiment</span>
              <p className="text-sm text-warm-500">暂未创建 A/B 测试</p>
            </div>
          ) : (
            tests.map((test) => {
              const sig = test.metrics?.significance ?? 0;
              return (
                <button
                  key={test.id}
                  onClick={() => selectTest(test.id)}
                  className={`w-full text-left rounded-xl border p-3.5 transition-all duration-200 hover:shadow-sm ${
                    selectedTestId === test.id
                      ? 'border-primary-300 bg-primary-50/50 shadow-sm'
                      : 'border-warm-200 bg-warm-100 hover:border-primary-200'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <h4 className="text-sm font-semibold text-warm-800 truncate">{test.name}</h4>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium shrink-0 ${STATUS_COLORS[test.status]}`}>
                      {STATUS_LABELS[test.status]}
                    </span>
                  </div>
                  <p className="text-xs text-warm-500 line-clamp-1 mb-2">{test.description}</p>
                  <div className="flex items-center gap-3 text-[10px] text-warm-400">
                    <span>流量: {100 - (test.traffic_split ?? 50)}/{test.traffic_split ?? 50}</span>
                    <span>样本: {test.metrics?.totalRequests ?? test.total_impressions ?? 0}</span>
                    <SignificanceBadge significance={sig} />
                  </div>
                </button>
              );
            })
          )}
        </div>

        {/* Right: Detail Panel */}
        <div className="lg:col-span-2">
          {!selectedTest ? (
            <div className="rounded-xl border border-dashed border-warm-300 bg-warm-50 px-6 py-16 text-center">
              <span className="material-symbols-outlined text-4xl text-warm-300 mb-2 block">lab_profile</span>
              <p className="text-sm text-warm-500">选择或创建一个 A/B 测试开始分析</p>
            </div>
          ) : (
            <div className="rounded-xl border border-warm-200 bg-warm-100 p-5 space-y-4">
              {/* Header */}
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <h4 className="text-base font-semibold text-warm-900">{selectedTest.name}</h4>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${STATUS_COLORS[selectedTest.status]}`}>
                      {STATUS_LABELS[selectedTest.status]}
                    </span>
                    {backendResult?.winner_variant_id && (
                      <WinnerBadge variantId={backendResult.winner_variant_id} confidence={backendResult.confidence_level} />
                    )}
                  </div>
                  <p className="text-sm text-warm-500">{selectedTest.description}</p>
                </div>
                <div className="flex items-center gap-1.5">
                  {selectedTest.status === 'draft' && (
                    <button onClick={() => startTest(selectedTest.id)} className="btn-primary text-xs px-3 py-1">[play] 启动</button>
                  )}
                  {selectedTest.status === 'running' && (
                    <>
                      <button onClick={() => pauseTest(selectedTest.id)} className="btn-secondary text-xs px-3 py-1">[pause] 暂停</button>
                      <button
                        onClick={() => handleComputeResults(selectedTest.id)}
                        disabled={computingId === selectedTest.id}
                        className="btn-primary text-xs px-3 py-1"
                      >
                        {computingId === selectedTest.id ? '计算中...' : '[check] 完成并计算'}
                      </button>
                    </>
                  )}
                  {selectedTest.status === 'paused' && (
                    <>
                      <button onClick={() => startTest(selectedTest.id)} className="btn-primary text-xs px-3 py-1">[play] 继续</button>
                      <button
                        onClick={() => handleComputeResults(selectedTest.id)}
                        disabled={computingId === selectedTest.id}
                        className="btn-primary text-xs px-3 py-1"
                      >
                        {computingId === selectedTest.id ? '计算中...' : '[check] 完成并计算'}
                      </button>
                    </>
                  )}
                  {selectedTest.status === 'completed' && !backendResult && (
                    <button
                      onClick={() => handleComputeResults(selectedTest.id)}
                      disabled={computingId === selectedTest.id}
                      className="btn-primary text-xs px-3 py-1"
                    >
                      {computingId === selectedTest.id ? '计算中...' : '[sync] 重新计算'}
                    </button>
                  )}
                  <button onClick={() => deleteTest(selectedTest.id)} className="btn-ghost text-xs text-red-500 px-2 py-1">[delete]</button>
                </div>
              </div>

              {/* Traffic Split Visualization */}
              <div>
                <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">流量分配</h5>
                <div className="flex items-center gap-3">
                  <div className="flex-1 h-8 bg-warm-100 rounded-lg overflow-hidden flex">
                    <div
                      className="h-full bg-primary-200 flex items-center justify-center text-xs font-medium text-primary-700 transition-all"
                      style={{ width: `${100 - (selectedTest.traffic_split ?? 50)}%` }}
                    >
                      A ({100 - (selectedTest.traffic_split ?? 50)}%)
                    </div>
                    <div
                      className="h-full bg-amber-200 flex items-center justify-center text-xs font-medium text-amber-700 transition-all"
                      style={{ width: `${selectedTest.traffic_split ?? 50}%` }}
                    >
                      B ({selectedTest.traffic_split ?? 50}%)
                    </div>
                  </div>
                </div>
              </div>

              {/* L5: Backend-computed results (takes priority when available) */}
              {backendResult && backendResult.variant_stats && (
                <>
                  {/* Variant Comparison Cards from backend */}
                  <div className="grid grid-cols-2 gap-3">
                    {Object.entries(backendResult.variant_stats).map(([vid, vstats]) => (
                      <BackendVariantCard
                        key={vid}
                        variantId={vid}
                        stats={vstats}
                        isWinner={backendResult.winner_variant_id === vid}
                      />
                    ))}
                  </div>

                  {/* Side-by-side comparison chart */}
                  <div>
                    <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">指标对比</h5>
                    <div className="space-y-3 bg-warm-50 rounded-lg p-4">
                      {(() => {
                        const variants = Object.entries(backendResult.variant_stats);
                        if (variants.length < 2) return null;
                        const [v0, v1] = variants;
                        const s0 = v0[1];
                        const s1 = v1[1];
                        const maxQ = Math.max(s0.mean_quality, s1.mean_quality, 0.1);
                        const maxSat = Math.max(s0.mean_satisfaction, s1.mean_satisfaction, 0.1);
                        const maxLat = Math.max(s0.mean_latency_ms, s1.mean_latency_ms, 0.1);
                        const maxTok = Math.max(s0.mean_tokens, s1.mean_tokens, 0.1);
                        const maxSR = Math.max(s0.success_rate, s1.success_rate, 0.01);
                        return (
                          <>
                            <SideBySideBarChart
                              title="平均质量"
                              labelA={v0[0]} valueA={s0.mean_quality}
                              labelB={v1[0]} valueB={s1.mean_quality}
                              maxValue={maxQ} unit=""
                              colorA="bg-primary-400" colorB="bg-primary-400"
                            />
                            <SideBySideBarChart
                              title="平均满意度"
                              labelA={v0[0]} valueA={s0.mean_satisfaction}
                              labelB={v1[0]} valueB={s1.mean_satisfaction}
                              maxValue={maxSat} unit=""
                              colorA="bg-amber-400" colorB="bg-amber-400"
                            />
                            <SideBySideBarChart
                              title="平均延迟"
                              labelA={v0[0]} valueA={s0.mean_latency_ms}
                              labelB={v1[0]} valueB={s1.mean_latency_ms}
                              maxValue={maxLat} unit="ms"
                              colorA="bg-red-300" colorB="bg-red-300"
                            />
                            <SideBySideBarChart
                              title="平均 Token"
                              labelA={v0[0]} valueA={s0.mean_tokens}
                              labelB={v1[0]} valueB={s1.mean_tokens}
                              maxValue={maxTok} unit=""
                              colorA="bg-purple-300" colorB="bg-purple-300"
                            />
                            <SideBySideBarChart
                              title="成功率"
                              labelA={v0[0]} valueA={s0.success_rate * 100}
                              labelB={v1[0]} valueB={s1.success_rate * 100}
                              maxValue={maxSR * 100} unit="%"
                              colorA="bg-green-400" colorB="bg-green-400"
                            />
                          </>
                        );
                      })()}
                    </div>
                  </div>
                </>
              )}

              {/* Demo-mode variant comparison (fallback) */}
              {!backendResult && variantA && variantB && (
                <div className="grid grid-cols-2 gap-3">
                  <VariantMetricsCard label="A" metrics={variantA} isWinner={winner === 'A'} />
                  <VariantMetricsCard label="B" metrics={variantB} isWinner={winner === 'B'} />
                </div>
              )}

              {/* L5: Statistical Summary — real backend results or demo fallback */}
              <div className="rounded-lg bg-warm-50 p-4">
                <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">统计分析</h5>

                {backendResult ? (
                  /* Real statistical output from backend */
                  <div className="space-y-3">
                    <div className="grid grid-cols-4 gap-4 text-center">
                      <div>
                        <div className="text-2xl font-bold text-primary-600">
                          {Object.values(backendResult.variant_stats).reduce((s, v) => s + v.count, 0)}
                        </div>
                        <div className="text-[10px] text-warm-500">总样本量</div>
                      </div>
                      <div>
                        <div className="text-2xl font-bold text-amber-600">
                          {backendResult.confidence_level.toFixed(1)}%
                        </div>
                        <div className="text-[10px] text-warm-500">置信度</div>
                      </div>
                      <div>
                        <div className="text-2xl font-bold text-green-600">
                          {backendResult.winner_variant_id || '—'}
                        </div>
                        <div className="text-[10px] text-warm-500">领先变体</div>
                      </div>
                      <div>
                        <div className="text-2xl font-bold text-purple-600">
                          {Math.abs(backendResult.effect_size).toFixed(3)}
                        </div>
                        <div className="text-[10px] text-warm-500">效应量 (Cohen's d)</div>
                      </div>
                    </div>

                    {/* Detailed stats row */}
                    <div className="grid grid-cols-3 gap-4 text-center text-[10px] text-warm-600 pt-2 border-t border-warm-200">
                      <div>
                        <span className="font-mono font-medium">{formatPValue(backendResult.p_value)}</span>
                        <div className="text-warm-400">p值 (Welch's t)</div>
                      </div>
                      <div>
                        <span className="font-mono font-medium">
                          Cohen's d = {backendResult.effect_size.toFixed(3)} {effectSizeLabel(backendResult.effect_size)}
                        </span>
                        <div className="text-warm-400">效应量</div>
                      </div>
                      <div>
                        <span className="font-mono font-medium">{backendResult.test_method}</span>
                        <div className="text-warm-400">检验方法</div>
                      </div>
                    </div>

                    {/* Per-variant sample sizes */}
                    <div className="grid grid-cols-2 gap-2 pt-1.5 border-t border-warm-200">
                      {Object.entries(backendResult.variant_stats).map(([vid, vstats]) => (
                        <div key={vid} className="text-center">
                          <span className="text-[10px] text-warm-400">Variant {vid}: </span>
                          <span className="text-xs font-mono font-medium text-warm-700">{vstats.count.toLocaleString()} 样本</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  /* Demo-mode fallback stats */
                  <div className="grid grid-cols-3 gap-4 text-center">
                    <div>
                      <div className="text-2xl font-bold text-primary-600">{selectedTest.metrics?.totalRequests ?? 0}</div>
                      <div className="text-[10px] text-warm-500">总样本量</div>
                    </div>
                    <div>
                      <div className="text-2xl font-bold text-amber-600">{selectedTest.metrics?.significance ?? 0}%</div>
                      <div className="text-[10px] text-warm-500">置信度</div>
                    </div>
                    <div>
                      <div className="text-2xl font-bold text-green-600">
                        {winner || '—'}
                      </div>
                      <div className="text-[10px] text-warm-500">领先变体</div>
                    </div>
                  </div>
                )}
              </div>

              {/* Variant Config Details */}
              <div>
                <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">变体配置</h5>
                {(selectedTest.variants || []).map((v) => (
                  <div key={v.id} className="rounded-lg border border-warm-150 p-3 mb-2 last:mb-0">
                    <span className="text-sm font-medium text-warm-800">
                      Variant {v.id}: {v.label || v.name || ''}
                    </span>
                    <pre className="text-xs text-warm-600 font-mono bg-warm-50 rounded p-2 mt-1.5 max-h-24 overflow-y-auto whitespace-pre-wrap">
                      {JSON.stringify(v.config, null, 2)}
                    </pre>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Create Modal */}
      {showCreate && (
        <>
          <div className="fixed inset-0 z-50 bg-black/60" onClick={() => setShowCreate(false)} />
          <div className="fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2 w-full max-w-md bg-warm-100 rounded-2xl shadow-xl p-6">
            <h4 className="text-lg font-semibold text-warm-900 mb-4">新建 A/B 测试</h4>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-warm-500 mb-1">测试名称 *</label>
                <input
                  className="input w-full text-sm"
                  placeholder="例如: Prompt 优化对比"
                  value={createForm.name}
                  onChange={(e) => setCreateForm((f) => ({ ...f, name: e.target.value }))}
                />
              </div>
              <div>
                <label className="block text-xs text-warm-500 mb-1">描述</label>
                <input
                  className="input w-full text-sm"
                  placeholder="测试目的和对比维度"
                  value={createForm.description}
                  onChange={(e) => setCreateForm((f) => ({ ...f, description: e.target.value }))}
                />
              </div>
              <div>
                <label className="block text-xs text-warm-500 mb-1">目标 Agent ID</label>
                <input
                  className="input w-full text-sm"
                  placeholder="agent-code-reviewer"
                  value={createForm.agentId}
                  onChange={(e) => setCreateForm((f) => ({ ...f, agentId: e.target.value }))}
                />
              </div>
              <div>
                <label className="block text-xs text-warm-500 mb-1">
                  B 组流量占比: {createForm.trafficSplit}%
                </label>
                <input
                  type="range"
                  min={5}
                  max={50}
                  value={createForm.trafficSplit}
                  onChange={(e) => setCreateForm((f) => ({ ...f, trafficSplit: Number(e.target.value) }))}
                  className="w-full"
                />
                <div className="flex justify-between text-[10px] text-warm-400">
                  <span>A 组: {100 - createForm.trafficSplit}%</span>
                  <span>B 组: {createForm.trafficSplit}%</span>
                </div>
              </div>
            </div>
            <div className="flex gap-2 mt-5">
              <button
                className="btn-primary flex-1"
                disabled={!createForm.name.trim()}
                onClick={() => {
                  void createTest({
                    name: createForm.name,
                    description: createForm.description,
                    agent_id: createForm.agentId,
                    traffic_split: createForm.trafficSplit,
                  });
                  setShowCreate(false);
                  setCreateForm({ name: '', description: '', agentId: '', trafficSplit: 50 });
                }}
              >
                创建
              </button>
              <button className="btn-secondary flex-1" onClick={() => setShowCreate(false)}>取消</button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
