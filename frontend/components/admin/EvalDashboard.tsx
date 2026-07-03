'use client';

import { useEffect, useState, useRef, type JSX, useCallback } from 'react';
import { useEvalStore, type GoldenDataset, type GoldenItem, type EvalRun, type ItemScore, type RegrDetail } from '../../stores/evalStore';

// ── Status Badge ──────────────────────────────────────────────────

function statusBadge(status: string): { bg: string; text: string; label: string } {
  switch (status) {
    case 'pending': return { bg: 'bg-gray-100', text: 'text-gray-700', label: '等待中' };
    case 'running': return { bg: 'bg-blue-100', text: 'text-blue-700', label: '运行中' };
    case 'completed': return { bg: 'bg-emerald-100', text: 'text-emerald-700', label: '已完成' };
    case 'failed': return { bg: 'bg-red-100', text: 'text-red-700', label: '失败' };
    case 'cancelled': return { bg: 'bg-amber-100', text: 'text-amber-700', label: '已取消' };
    default: return { bg: 'bg-gray-100', text: 'text-gray-700', label: status };
  }
}

// ── Regression Alert Banner ────────────────────────────────────────

function RegressionBanner({ details }: { details: RegrDetail[] }) {
  if (!details || details.length === 0) return null;
  return (
    <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 mb-4">
      <div className="flex items-center gap-2 text-sm font-semibold text-red-700 mb-2">
        <span className="material-symbols-outlined text-[18px]">warning</span>
        回归检测 — {details.length} 项指标退化
      </div>
      <div className="space-y-1">
        {details.map((d, i) => (
          <div key={i} className="text-xs text-red-600 flex items-center gap-2">
            <span className="font-mono">{d.metric}</span>
            <span>{d.baseline.toFixed(3)} → {d.current.toFixed(3)}</span>
            <span className="font-semibold">({d.change_pct.toFixed(1)}%)</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Metric Trend Chart (SVG line chart placeholder) ───────────────

function MetricTrendChart({ runs }: { runs: EvalRun[] }) {
  const completed = runs.filter((r) => r.status === 'completed').reverse();
  if (completed.length < 2) {
    return (
      <div className="card p-6 text-center text-sm text-warm-500">
        至少需要 2 次完成的评估运行才能显示趋势图。
      </div>
    );
  }

  const width = 600;
  const height = 200;
  const pad = { top: 20, right: 20, bottom: 30, left: 50 };
  const pw = width - pad.left - pad.right;
  const ph = height - pad.top - pad.bottom;

  // Extract overall_score from each run
  const points = completed.map((r, i) => {
    const metrics = (r.results?.metrics || r.results || {}) as Record<string, unknown>;
    const score = typeof metrics.overall_score === 'number' ? metrics.overall_score : 0;
    return { x: i, y: score, label: new Date(r.created_at).toLocaleDateString('zh-CN') };
  });

  const maxY = 1.0;
  const minY = Math.max(0, Math.min(...points.map((p) => p.y)) - 0.1);

  const xScale = (i: number) => pad.left + (points.length > 1 ? (i / (points.length - 1)) * pw : pw / 2);
  const yScale = (v: number) => pad.top + ph - ((v - minY) / (maxY - minY)) * ph;

  const pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${xScale(i)} ${yScale(p.y)}`).join(' ');

  return (
    <div className="card p-4 overflow-x-auto">
      <svg width={width} height={height} className="mx-auto">
        {/* Grid lines */}
        {[0, 0.25, 0.5, 0.75, 1.0].map((v) => (
          <g key={v}>
            <line x1={pad.left} y1={yScale(v)} x2={width - pad.right} y2={yScale(v)} stroke="#e5e7eb" strokeWidth={1} />
            <text x={pad.left - 5} y={yScale(v) + 4} textAnchor="end" className="text-[10px] fill-warm-400">{v.toFixed(2)}</text>
          </g>
        ))}
        {/* Line */}
        <path d={pathD} fill="none" stroke="#6366f1" strokeWidth={2} strokeLinejoin="round" />
        {/* Points */}
        {points.map((p, i) => (
          <g key={i}>
            <circle cx={xScale(i)} cy={yScale(p.y)} r={4} fill="#6366f1" />
            <text x={xScale(i)} y={height - 5} textAnchor="middle" className="text-[9px] fill-warm-500">{p.label}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}

// ── Tabs ───────────────────────────────────────────────────────────

type TabKey = 'datasets' | 'runs' | 'trends' | 'regressions';

const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: 'datasets', label: '数据集', icon: 'dataset' },
  { key: 'runs', label: '评估运行', icon: 'play_circle' },
  { key: 'trends', label: '指标趋势', icon: 'trending_up' },
  { key: 'regressions', label: '回归历史', icon: 'history' },
];

// ── Main Dashboard ─────────────────────────────────────────────────

export default function EvalDashboard(): JSX.Element {
  const {
    datasets, datasetsLoading,
    currentDataset, currentDatasetLoading,
    runs, runsLoading, currentRun, currentRunLoading,
    importResult,
    validationResult, validationLoading,
    coverageResult, coverageLoading,
    loadDatasets, createDataset, getDataset, updateDataset, deleteDataset,
    addItem, updateItem, deleteItem,
    importItems,
    loadRuns, createRun, getRun, cancelRun, pollRunUntilComplete,
    validateDataset, getCoverage,
    clearCurrent,
  } = useEvalStore();

  const [activeTab, setActiveTab] = useState<TabKey>('datasets');

  // Dataset form state
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [newTags, setNewTags] = useState('');

  // Item form state
  const [showItemForm, setShowItemForm] = useState(false);
  const [newQuery, setNewQuery] = useState('');
  const [newExpectedResp, setNewExpectedResp] = useState('');

  // Run form
  const [selectedDatasetForRun, setSelectedDatasetForRun] = useState('');
  const [runModel, setRunModel] = useState('mock-gpt');

  // Selected dataset for items view
  const [selectedDatasetId, setSelectedDatasetId] = useState<string | null>(null);

  // File input ref for import
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Init
  useEffect(() => {
    loadDatasets();
    loadRuns();
  }, []);

  // ── Handlers ─────────────────────────────────────────────────────

  const handleCreateDataset = useCallback(async () => {
    if (!newName.trim()) return;
    await createDataset({
      name: newName.trim(),
      description: newDesc.trim(),
      tags: newTags ? newTags.split(',').map((t) => t.trim()).filter(Boolean) : [],
    });
    setNewName('');
    setNewDesc('');
    setNewTags('');
    setShowCreateForm(false);
  }, [newName, newDesc, newTags, createDataset]);

  const handleAddItem = useCallback(async () => {
    if (!selectedDatasetId || !newQuery.trim()) return;
    await addItem(selectedDatasetId, {
      query: newQuery.trim(),
      expected_response: newExpectedResp.trim(),
    });
    setNewQuery('');
    setNewExpectedResp('');
    setShowItemForm(false);
  }, [selectedDatasetId, newQuery, newExpectedResp, addItem]);

  const handleRunEval = useCallback(async () => {
    if (!selectedDatasetForRun) return;
    const run = await createRun(selectedDatasetForRun, { model: runModel });
    if (run) {
      pollRunUntilComplete(run.id).catch(() => {});
    }
  }, [selectedDatasetForRun, runModel, createRun, pollRunUntilComplete]);

  const handleImportClick = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFileImport = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !selectedDatasetId) return;
    try {
      const text = await file.text();
      const items = JSON.parse(text) as GoldenItem[];
      await importItems(selectedDatasetId, items);
    } catch {
      // ignore parse errors
    }
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, [selectedDatasetId, importItems]);

  const handleExport = useCallback(async () => {
    if (!selectedDatasetId) return;
    const { exportItems } = useEvalStore.getState();
    const items = await exportItems(selectedDatasetId);
    const blob = new Blob([JSON.stringify(items, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `dataset-${selectedDatasetId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [selectedDatasetId]);

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div className="space-y-4">
      {/* Tab Bar */}
      <div className="flex gap-1 border-b border-warm-200 pb-0">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
              activeTab === tab.key
                ? 'bg-white text-primary-600 border border-b-white border-warm-200 -mb-[1px]'
                : 'text-warm-500 hover:text-warm-700 hover:bg-warm-50'
            }`}
          >
            <span className="material-symbols-outlined text-[16px] align-middle mr-1">{tab.icon}</span>
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Tab 1: Datasets ───────────────────────────────────────── */}
      {activeTab === 'datasets' && (
        <div className="space-y-4">
          {/* Toolbar */}
          <div className="flex items-center gap-3 flex-wrap">
            <button
              onClick={() => setShowCreateForm(!showCreateForm)}
              className="btn-primary text-sm px-3 py-1.5 rounded-lg"
            >
              + 新建数据集
            </button>
            {selectedDatasetId && (
              <>
                <button onClick={handleImportClick} className="btn-secondary text-sm px-3 py-1.5 rounded-lg">
                  导入 JSON
                </button>
                <input ref={fileInputRef} type="file" accept=".json" onChange={handleFileImport} className="hidden" />
                <button onClick={handleExport} className="btn-secondary text-sm px-3 py-1.5 rounded-lg">
                  导出 JSON
                </button>
                <button
                  onClick={() => { validateDataset(selectedDatasetId); }}
                  disabled={validationLoading}
                  className="btn-secondary text-sm px-3 py-1.5 rounded-lg"
                >
                  {validationLoading ? '验证中...' : '验证数据集'}
                </button>
                <button
                  onClick={() => { getCoverage(selectedDatasetId); }}
                  disabled={coverageLoading}
                  className="btn-secondary text-sm px-3 py-1.5 rounded-lg"
                >
                  {coverageLoading ? '分析中...' : '覆盖率分析'}
                </button>
              </>
            )}
          </div>

          {/* Create form */}
          {showCreateForm && (
            <div className="card p-4 space-y-3">
              <input
                className="input w-full text-sm"
                placeholder="数据集名称"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
              />
              <input
                className="input w-full text-sm"
                placeholder="描述（可选）"
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
              />
              <input
                className="input w-full text-sm"
                placeholder="标签，逗号分隔（可选）"
                value={newTags}
                onChange={(e) => setNewTags(e.target.value)}
              />
              <div className="flex gap-2">
                <button onClick={handleCreateDataset} className="btn-primary text-sm px-3 py-1.5 rounded-lg">创建</button>
                <button onClick={() => setShowCreateForm(false)} className="btn-secondary text-sm px-3 py-1.5 rounded-lg">取消</button>
              </div>
            </div>
          )}

          {/* Dataset list */}
          {datasetsLoading ? (
            <div className="flex justify-center py-8">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
            </div>
          ) : (
            <div className="space-y-2">
              {datasets.map((ds) => (
                <div
                  key={ds.id}
                  onClick={() => { setSelectedDatasetId(ds.id); getDataset(ds.id); }}
                  className={`card p-3 cursor-pointer hover:shadow-md transition-shadow ${
                    selectedDatasetId === ds.id ? 'ring-2 ring-primary-300' : ''
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="font-medium text-sm">{ds.name}</span>
                      <span className="text-xs text-warm-400 ml-2">v{ds.version}</span>
                      <span className="text-xs text-warm-400 ml-2">{ds.item_count} 条</span>
                    </div>
                    <div className="flex gap-1">
                      {ds.tags?.map((t, i) => (
                        <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-primary-50 text-primary-600">{t}</span>
                      ))}
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); deleteDataset(ds.id); }}
                      className="text-warm-400 hover:text-red-500 text-sm"
                    >
                      <span className="material-symbols-outlined text-[16px]">delete</span>
                    </button>
                  </div>
                  {ds.description && <p className="text-xs text-warm-500 mt-1">{ds.description}</p>}
                </div>
              ))}
              {datasets.length === 0 && (
                <p className="text-center text-sm text-warm-400 py-8">暂无数据集。点击"新建数据集"开始。</p>
              )}
            </div>
          )}

          {/* Dataset detail with items */}
          {currentDatasetLoading ? (
            <div className="flex justify-center py-4">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
            </div>
          ) : currentDataset ? (
            <div className="card p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-sm">{currentDataset.dataset.name} — 条目列表</h3>
                <button onClick={() => setShowItemForm(!showItemForm)} className="btn-primary text-xs px-2 py-1 rounded">
                  + 添加条目
                </button>
              </div>

              {showItemForm && (
                <div className="card p-3 space-y-2 bg-warm-50">
                  <textarea
                    className="input w-full text-sm"
                    placeholder="查询文本"
                    rows={2}
                    value={newQuery}
                    onChange={(e) => setNewQuery(e.target.value)}
                  />
                  <textarea
                    className="input w-full text-sm"
                    placeholder="期望响应（可选）"
                    rows={2}
                    value={newExpectedResp}
                    onChange={(e) => setNewExpectedResp(e.target.value)}
                  />
                  <div className="flex gap-2">
                    <button onClick={handleAddItem} className="btn-primary text-xs px-2 py-1 rounded">添加</button>
                    <button onClick={() => setShowItemForm(false)} className="btn-secondary text-xs px-2 py-1 rounded">取消</button>
                  </div>
                </div>
              )}

              <div className="space-y-1 max-h-80 overflow-y-auto">
                {currentDataset.items.map((item, i) => (
                  <div key={item.id || i} className="flex items-start gap-2 p-2 rounded hover:bg-warm-50">
                    <span className="text-xs text-warm-400 w-6 shrink-0">#{item.index || i + 1}</span>
                    <p className="text-xs flex-1 line-clamp-2">{item.query}</p>
                    <button
                      onClick={() => deleteItem(currentDataset.dataset.id, item.id)}
                      className="text-warm-400 hover:text-red-500 shrink-0"
                    >
                      <span className="material-symbols-outlined text-[14px]">close</span>
                    </button>
                  </div>
                ))}
              </div>

              {importResult && (
                <div className="text-xs text-emerald-600 bg-emerald-50 rounded px-3 py-1">
                  导入完成：{importResult.imported}/{importResult.total} 条
                </div>
              )}
            </div>
          ) : null}

          {/* Validation result */}
          {validationResult && (
            <div className="card p-4">
              <h3 className="font-semibold text-sm mb-2">验证结果</h3>
              <div className="space-y-2">
                {(validationResult.results as Array<Record<string, unknown>>)?.map((r, i) => (
                  <div key={i} className="p-2 rounded bg-warm-50 text-xs">
                    <div className="flex justify-between">
                      <span className="font-medium">#{String(r.item_index)} {String(r.query || '').slice(0, 60)}</span>
                      {r.error ? (
                        <span className="text-red-500">{String(r.error)}</span>
                      ) : (
                        <span className={r.exact_match === 1.0 ? 'text-emerald-600' : 'text-amber-600'}>
                          匹配: {String(r.exact_match)} | {Number(r.latency_ms).toFixed(0)}ms
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Coverage result */}
          {coverageResult && (
            <div className="card p-4">
              <h3 className="font-semibold text-sm mb-2">
                覆盖率分析 — 得分: {String(coverageResult.coverage_score)}
              </h3>
              <div className="grid grid-cols-4 gap-3 text-xs">
                <div className="p-2 bg-warm-50 rounded">
                  <div className="text-warm-400">总条目</div>
                  <div className="font-semibold">{String(coverageResult.total_items)}</div>
                </div>
                <div className="p-2 bg-warm-50 rounded">
                  <div className="text-warm-400">含工具调用</div>
                  <div className="font-semibold">{String(coverageResult.items_with_tool_calls)}</div>
                </div>
                <div className="p-2 bg-warm-50 rounded">
                  <div className="text-warm-400">含期望响应</div>
                  <div className="font-semibold">{String(coverageResult.items_with_expected_response)}</div>
                </div>
                <div className="p-2 bg-warm-50 rounded">
                  <div className="text-warm-400">类别数</div>
                  <div className="font-semibold">
                    {Object.keys(coverageResult.category_distribution as Record<string, unknown> || {}).length}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Tab 2: Eval Runs ───────────────────────────────────────── */}
      {activeTab === 'runs' && (
        <div className="space-y-4">
          {/* Run creation */}
          <div className="card p-4 flex items-center gap-3 flex-wrap">
            <select
              className="input text-sm"
              value={selectedDatasetForRun}
              onChange={(e) => setSelectedDatasetForRun(e.target.value)}
            >
              <option value="">-- 选择数据集 --</option>
              {datasets.map((ds) => (
                <option key={ds.id} value={ds.id}>{ds.name} ({ds.item_count} 条)</option>
              ))}
            </select>
            <input
              className="input text-sm w-32"
              placeholder="模型"
              value={runModel}
              onChange={(e) => setRunModel(e.target.value)}
            />
            <button onClick={handleRunEval} className="btn-primary text-sm px-3 py-1.5 rounded-lg">
              开始评估
            </button>
          </div>

          {/* Run list */}
          {runsLoading ? (
            <div className="flex justify-center py-8">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
            </div>
          ) : (
            <div className="space-y-2">
              {runs.map((run) => {
                const badge = statusBadge(run.status);
                const dataset = datasets.find((d) => d.id === run.dataset_id);
                return (
                  <div
                    key={run.id}
                    onClick={() => getRun(run.id)}
                    className={`card p-3 cursor-pointer hover:shadow-md transition-shadow ${
                      currentRun?.id === run.id ? 'ring-2 ring-primary-300' : ''
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${badge.bg} ${badge.text}`}>
                          {badge.label}
                        </span>
                        <span className="text-sm font-medium">
                          {dataset?.name || run.dataset_id?.slice(0, 8)}
                        </span>
                        {run.status === 'running' && (
                          <span className="inline-block h-3 w-3 animate-pulse rounded-full bg-blue-400" />
                        )}
                      </div>
                      <div className="flex items-center gap-2 text-xs text-warm-400">
                        <span>{new Date(run.created_at).toLocaleString('zh-CN')}</span>
                        {run.status === 'running' && (
                          <button
                            onClick={(e) => { e.stopPropagation(); cancelRun(run.id); }}
                            className="text-red-500 hover:text-red-700"
                          >
                            取消
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
              {runs.length === 0 && (
                <p className="text-center text-sm text-warm-400 py-8">暂无评估运行记录。</p>
              )}
            </div>
          )}

          {/* Run detail */}
          {currentRunLoading ? (
            <div className="flex justify-center py-4">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
            </div>
          ) : currentRun ? (
            <div className="card p-4 space-y-3">
              {/* Regression alert */}
              {currentRun.results && Boolean((currentRun.results as Record<string, unknown>).regression_detected) && (
                <RegressionBanner
                  details={(currentRun.results as Record<string, unknown>).regression_details as RegrDetail[] || []}
                />
              )}

              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-sm">运行详情 — {currentRun.id.slice(0, 8)}</h3>
                <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${statusBadge(currentRun.status).bg} ${statusBadge(currentRun.status).text}`}>
                  {statusBadge(currentRun.status).label}
                </span>
              </div>

              {/* Aggregate metrics */}
              {currentRun.results && (
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries((currentRun.results as Record<string, unknown>).metrics as Record<string, number> || {}).map(([k, v]) => (
                    <div key={k} className="p-2 bg-warm-50 rounded text-xs flex justify-between">
                      <span className="text-warm-500">{k}</span>
                      <span className="font-mono font-medium">{typeof v === 'number' ? v.toFixed(4) : String(v)}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Per-item results */}
              {currentRun.item_results && currentRun.item_results.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-warm-500 mb-2">逐条结果</h4>
                  <div className="space-y-1 max-h-60 overflow-y-auto">
                    {(currentRun.item_results as Array<Record<string, unknown>>).map((item: Record<string, unknown>, i: number) => {
                      const scores = (item.scores || {}) as Record<string, number>;
                      const em = scores?.exact_match ?? 0;
                      const fm = scores?.fuzzy_match ?? 0;
                      return (
                        <div key={i} className="flex items-center gap-2 p-1.5 rounded bg-warm-50 text-xs">
                          <span className="text-warm-400 w-6">#{Number(item.item_index || i)}</span>
                          <span className="flex-1 truncate">{String(item.query || '').slice(0, 50)}</span>
                          <span className={em >= 1 ? 'text-emerald-600' : em > 0 ? 'text-amber-600' : 'text-red-500'}>
                            EM:{em.toFixed(2)}
                          </span>
                          <span className={fm >= 0.8 ? 'text-emerald-600' : fm > 0.3 ? 'text-amber-600' : 'text-red-500'}>
                            FM:{fm.toFixed(2)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          ) : null}
        </div>
      )}

      {/* ── Tab 3: Metric Trends ───────────────────────────────────── */}
      {activeTab === 'trends' && (
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <select
              className="input text-sm"
              value={selectedDatasetId || ''}
              onChange={(e) => setSelectedDatasetId(e.target.value || null)}
            >
              <option value="">-- 选择数据集 --</option>
              {datasets.map((ds) => (
                <option key={ds.id} value={ds.id}>{ds.name}</option>
              ))}
            </select>
            <button
              onClick={() => { if (selectedDatasetId) loadRuns(selectedDatasetId); }}
              className="btn-primary text-sm px-3 py-1.5 rounded-lg"
            >
              加载运行数据
            </button>
          </div>
          <MetricTrendChart runs={runs} />
          {selectedDatasetId && runs.filter((r) => r.status === 'completed').length > 0 && (
            <div className="card p-4">
              <h3 className="text-sm font-semibold mb-2">最近运行汇总</h3>
              <div className="space-y-1">
                {runs.filter((r) => r.status === 'completed').slice(0, 10).map((run) => {
                  const metrics = (run.results?.metrics || run.results || {}) as Record<string, unknown>;
                  return (
                    <div key={run.id} className="flex justify-between text-xs p-1.5 rounded hover:bg-warm-50">
                      <span className="text-warm-400">{new Date(run.created_at).toLocaleString('zh-CN')}</span>
                      <span className="font-mono font-medium">
                        {typeof metrics.overall_score === 'number' ? (metrics.overall_score as number).toFixed(4) : 'N/A'}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Tab 4: Regression History ───────────────────────────────── */}
      {activeTab === 'regressions' && (
        <div className="space-y-4">
          {runsLoading ? (
            <div className="flex justify-center py-8">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
            </div>
          ) : (
            <div className="space-y-2">
              {runs
                .filter((r) => {
                  const res = (r.results || {}) as Record<string, unknown>;
                  return r.status === 'completed' && res.regression_detected;
                })
                .map((run) => {
                  const res = (run.results || {}) as Record<string, unknown>;
                  const details = (res.regression_details || []) as RegrDetail[];
                  const dataset = datasets.find((d) => d.id === run.dataset_id);
                  return (
                    <div key={run.id} className="card p-4 border-l-4 border-l-red-400">
                      <div className="flex items-center justify-between mb-2">
                        <div>
                          <span className="text-sm font-semibold text-red-700">
                            回归告警
                          </span>
                          <span className="text-xs text-warm-400 ml-2">
                            {dataset?.name || run.dataset_id?.slice(0, 8)}
                          </span>
                        </div>
                        <span className="text-xs text-warm-400">
                          {new Date(run.created_at).toLocaleString('zh-CN')}
                        </span>
                      </div>
                      <div className="space-y-1">
                        {details.map((d, i) => {
                          const severity = Math.abs(d.change_pct) > 20 ? '高' : Math.abs(d.change_pct) > 10 ? '中' : '低';
                          const sevColor = severity === '高' ? 'bg-red-100 text-red-700' : severity === '中' ? 'bg-amber-100 text-amber-700' : 'bg-yellow-100 text-yellow-700';
                          return (
                            <div key={i} className="flex items-center gap-2 text-xs">
                              <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${sevColor}`}>
                                {severity}
                              </span>
                              <span className="font-mono text-warm-600">{d.metric}</span>
                              <span className="text-warm-400">{d.baseline.toFixed(3)} → {d.current.toFixed(3)}</span>
                              <span className={`font-semibold ${d.change_pct < 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                                {d.change_pct.toFixed(1)}%
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              {runs.filter((r) => {
                const res = (r.results || {}) as Record<string, unknown>;
                return r.status === 'completed' && res.regression_detected;
              }).length === 0 && (
                <p className="text-center text-sm text-emerald-600 py-8">
                  未检测到回归。所有指标稳定或提升。
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
