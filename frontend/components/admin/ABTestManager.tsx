'use client';

// Agent A/B Testing Manager (P2-3)
// Create, run, and analyze A/B tests comparing agent variants.
// Includes traffic split visualization, quality comparison charts,
// and statistical significance analysis.

import { useState, useEffect, type JSX } from 'react';
import { useABTestStore, type ABTestConfig, type ABTestVariant } from '../../stores/abTestStore';

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

// ── Sub-components ────────────────────────────────────────────────────

function MiniBar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="h-2 bg-warm-150 rounded-full overflow-hidden">
      <div className={`h-full rounded-full transition-all duration-500 ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

function VariantMetricsCard({ label, metrics, isWinner }: {
  label: string;
  metrics: ABTestConfig['metrics']['variantA'];
  isWinner?: boolean;
}) {
  return (
    <div className={`rounded-xl border p-4 ${isWinner ? 'border-green-300 bg-green-50/50' : 'border-warm-200 bg-white'}`}>
      <div className="flex items-center gap-2 mb-3">
        <h4 className="text-sm font-semibold text-warm-800">Variant {label}</h4>
        {isWinner && <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-100 text-green-700 font-medium">🏆 胜出</span>}
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

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center text-xs">
      <span className="text-warm-500">{label}</span>
      <span className="font-mono font-medium text-warm-700">{value}</span>
    </div>
  );
}

function SignificanceBadge({ pct }: { pct: number }) {
  let color = 'bg-warm-100 text-warm-600';
  let label = '数据不足';
  if (pct >= 99) { color = 'bg-green-100 text-green-700'; label = '99%+ 显著'; }
  else if (pct >= 95) { color = 'bg-green-100 text-green-700'; label = '95% 显著'; }
  else if (pct >= 90) { color = 'bg-amber-100 text-amber-700'; label = '90% 趋势'; }
  else if (pct >= 50) { color = 'bg-warm-100 text-warm-600'; label = `${pct}% 收集`; }

  return <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${color}`}>{label}</span>;
}

// ── Main Component ────────────────────────────────────────────────────

export default function ABTestManager(): JSX.Element {
  const {
    tests, selectedTestId, loading, demoMode,
    loadTests, selectTest, createTest, startTest, pauseTest, completeTest, deleteTest, getWinner,
  } = useABTestStore();

  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({
    name: '',
    description: '',
    agentId: '',
    trafficSplit: 50,
  });

  useEffect(() => { void loadTests(); }, []);

  const selectedTest = tests.find((t) => t.id === selectedTestId) || null;
  const winner = selectedTest ? getWinner(selectedTest) : null;

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
            流量分割 · 质量对比 · 统计显著性分析 — 数据驱动 Agent 优化决策
          </p>
        </div>
        <div className="flex items-center gap-2">
          {demoMode && (
            <span className="tag tag-amber text-[11px] flex items-center gap-1">
              <span className="material-symbols-outlined text-[14px]">info</span>Demo
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
            tests.map((test) => (
              <button
                key={test.id}
                onClick={() => selectTest(test.id)}
                className={`w-full text-left rounded-xl border p-3.5 transition-all duration-200 hover:shadow-sm ${
                  selectedTestId === test.id
                    ? 'border-primary-300 bg-primary-50/50 shadow-sm'
                    : 'border-warm-200 bg-white hover:border-primary-200'
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
                  <span>流量: {100 - test.trafficSplit}/{test.trafficSplit}</span>
                  <span>样本: {test.metrics.totalRequests}</span>
                  <SignificanceBadge pct={test.metrics.significance} />
                </div>
              </button>
            ))
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
            <div className="rounded-xl border border-warm-200 bg-white p-5 space-y-4">
              {/* Header */}
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <h4 className="text-base font-semibold text-warm-900">{selectedTest.name}</h4>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${STATUS_COLORS[selectedTest.status]}`}>
                      {STATUS_LABELS[selectedTest.status]}
                    </span>
                  </div>
                  <p className="text-sm text-warm-500">{selectedTest.description}</p>
                </div>
                <div className="flex items-center gap-1.5">
                  {selectedTest.status === 'draft' && (
                    <button onClick={() => startTest(selectedTest.id)} className="btn-primary text-xs px-3 py-1">▶ 启动</button>
                  )}
                  {selectedTest.status === 'running' && (
                    <>
                      <button onClick={() => pauseTest(selectedTest.id)} className="btn-secondary text-xs px-3 py-1">⏸ 暂停</button>
                      <button onClick={() => completeTest(selectedTest.id)} className="btn-primary text-xs px-3 py-1">✅ 完成</button>
                    </>
                  )}
                  {selectedTest.status === 'paused' && (
                    <button onClick={() => startTest(selectedTest.id)} className="btn-primary text-xs px-3 py-1">▶ 继续</button>
                  )}
                  <button onClick={() => deleteTest(selectedTest.id)} className="btn-ghost text-xs text-red-500 px-2 py-1">🗑️</button>
                </div>
              </div>

              {/* Traffic Split Visualization */}
              <div>
                <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">流量分配</h5>
                <div className="flex items-center gap-3">
                  <div className="flex-1 h-8 bg-warm-100 rounded-lg overflow-hidden flex">
                    <div
                      className="h-full bg-primary-200 flex items-center justify-center text-xs font-medium text-primary-700 transition-all"
                      style={{ width: `${100 - selectedTest.trafficSplit}%` }}
                    >
                      A ({100 - selectedTest.trafficSplit}%)
                    </div>
                    <div
                      className="h-full bg-amber-200 flex items-center justify-center text-xs font-medium text-amber-700 transition-all"
                      style={{ width: `${selectedTest.trafficSplit}%` }}
                    >
                      B ({selectedTest.trafficSplit}%)
                    </div>
                  </div>
                </div>
              </div>

              {/* Variant Comparison */}
              <div className="grid grid-cols-2 gap-3">
                <VariantMetricsCard
                  label="A"
                  metrics={selectedTest.metrics.variantA}
                  isWinner={winner === 'A'}
                />
                <VariantMetricsCard
                  label="B"
                  metrics={selectedTest.metrics.variantB}
                  isWinner={winner === 'B'}
                />
              </div>

              {/* Statistical Summary */}
              <div className="rounded-lg bg-warm-50 p-4">
                <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">统计分析</h5>
                <div className="grid grid-cols-3 gap-4 text-center">
                  <div>
                    <div className="text-2xl font-bold text-primary-600">{selectedTest.metrics.totalRequests}</div>
                    <div className="text-[10px] text-warm-500">总样本量</div>
                  </div>
                  <div>
                    <div className="text-2xl font-bold text-amber-600">{selectedTest.metrics.significance}%</div>
                    <div className="text-[10px] text-warm-500">置信度</div>
                  </div>
                  <div>
                    <div className="text-2xl font-bold text-green-600">
                      {winner || '—'}
                    </div>
                    <div className="text-[10px] text-warm-500">领先变体</div>
                  </div>
                </div>
              </div>

              {/* Variant Details */}
              <div>
                <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">变体配置</h5>
                {selectedTest.variants.map((v) => (
                  <div key={v.id} className="rounded-lg border border-warm-150 p-3 mb-2 last:mb-0">
                    <span className="text-sm font-medium text-warm-800">Variant {v.id}: {v.label}</span>
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
          <div className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm" onClick={() => setShowCreate(false)} />
          <div className="fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2 w-full max-w-md bg-white rounded-2xl shadow-xl p-6">
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
                  void createTest(createForm);
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
