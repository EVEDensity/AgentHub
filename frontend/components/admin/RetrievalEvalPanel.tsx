'use client';

import { useState, useCallback, type JSX, type KeyboardEvent } from 'react';

// ── Types ─────────────────────────────────────────────────────────────

interface EvalMetrics {
  ndcg_at_5: number;
  ndcg_at_10: number;
  mrr: number;
  recall_at_5: number;
  recall_at_10: number;
  precision_at_5: number;
  precision_at_10: number;
  ap: number;
}

interface EvalResultItem {
  id: string;
  score: number;
  content: string;
  source_id: string;
  chunk_index: number;
  relevant: boolean;
}

interface EvalResponse {
  query: string;
  collection: string;
  k: number;
  metrics: EvalMetrics;
  results: EvalResultItem[];
}

const COLLECTIONS = ['docs', 'code', 'memory', 'artifacts'] as const;

interface Props {
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
}

// ── Dev mode simulated metrics ─────────────────────────────────────────

function simulateMetrics(goldenCount: number, k: number): EvalMetrics {
  const hitRatio = Math.min(goldenCount / Math.max(k, 1), 1.0);
  return {
    ndcg_at_5: round(Math.min(0.3 + hitRatio * 0.6, 1.0)),
    ndcg_at_10: round(Math.min(0.25 + hitRatio * 0.65, 1.0)),
    mrr: round(Math.min(0.4 + Math.random() * 0.5, 1.0)),
    recall_at_5: round(hitRatio * 0.8),
    recall_at_10: round(Math.min(hitRatio, 1.0)),
    precision_at_5: round(hitRatio * 0.9),
    precision_at_10: round(hitRatio * 0.85),
    ap: round(hitRatio * 0.75),
  };
}

function simulateResults(query: string, goldenIds: string[], k: number): EvalResultItem[] {
  const goldenSet = new Set(goldenIds);
  const results: EvalResultItem[] = [];
  // Insert golden items in random positions among filler results
  const positions = new Set<number>();
  const goldenPositions: number[] = [];
  for (let i = 0; i < Math.min(goldenIds.length, k); i++) {
    let pos = Math.floor(Math.random() * k);
    while (positions.has(pos)) pos = (pos + 1) % k;
    positions.add(pos);
    goldenPositions.push(pos);
  }
  goldenPositions.sort((a, b) => a - b);

  let gi = 0;
  for (let i = 0; i < k; i++) {
    const isGolden = goldenPositions.includes(i);
    const id = isGolden
      ? goldenIds[gi++ % goldenIds.length]
      : `filler_${i}`;
    results.push({
      id,
      score: round(isGolden ? 0.95 - i * 0.05 : 0.7 - i * 0.04),
      content: isGolden
        ? `[Golden] Relevant document chunk about "${query}" — chunk #${i}`
        : `Non-relevant filler chunk about unrelated topic #${i}`,
      source_id: `doc_${i % 3}`,
      chunk_index: i,
      relevant: isGolden,
    });
  }
  return results;
}

function round(v: number): number {
  return parseFloat(v.toFixed(6));
}

// ── Component ─────────────────────────────────────────────────────────

export default function RetrievalEvalPanel({ authHeaders, setNotice }: Props): JSX.Element {
  const [query, setQuery] = useState('');
  const [collection, setCollection] = useState<string>('docs');
  const [topK, setTopK] = useState(10);
  const [goldenText, setGoldenText] = useState('');
  const [loading, setLoading] = useState(false);
  const [metrics, setMetrics] = useState<EvalMetrics | null>(null);
  const [results, setResults] = useState<EvalResultItem[]>([]);
  const [error, setError] = useState('');

  const goldenIds = goldenText
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.length > 0);

  const doEvaluate = useCallback(async () => {
    if (!query.trim()) return;
    if (goldenIds.length === 0) {
      setError('请至少输入一个 Golden Chunk ID');
      return;
    }
    setLoading(true);
    setError('');
    setMetrics(null);
    setResults([]);

    try {
      const res = await fetch('/platform/knowledge/evaluate-retrieval', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(),
        },
        body: JSON.stringify({
          query: query.trim(),
          collection,
          golden_chunk_ids: goldenIds,
          k: topK,
        }),
      });

      if (res.ok) {
        const data: EvalResponse = await res.json();
        setMetrics(data.metrics);
        setResults(data.results || []);
      } else {
        const err = await res.json().catch(() => ({}));
        setError((err as { detail?: string }).detail || '评测请求失败');
      }
    } catch {
      // Dev mode fallback: simulate evaluation metrics
      setMetrics(simulateMetrics(goldenIds.length, topK));
      setResults(simulateResults(query, goldenIds, topK));
      setNotice('后端不可用，使用模拟评测数据');
    } finally {
      setLoading(false);
    }
  }, [query, collection, topK, goldenIds, authHeaders, setNotice]);

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !loading) void doEvaluate();
  };

  // ── Render helpers ─────────────────────────────────────────────────

  const pct = (v: number) => `${(v * 100).toFixed(2)}%`;
  const score = (v: number) => v.toFixed(4);

  const metricCards: { label: string; value: string; desc: string }[] = metrics
    ? [
        { label: 'NDCG@5', value: score(metrics.ndcg_at_5), desc: 'Normalized DCG at 5' },
        { label: 'NDCG@10', value: score(metrics.ndcg_at_10), desc: 'Normalized DCG at 10' },
        { label: 'MRR', value: score(metrics.mrr), desc: 'Mean Reciprocal Rank' },
        { label: 'Recall@5', value: pct(metrics.recall_at_5), desc: 'Recall at 5' },
        { label: 'Recall@10', value: pct(metrics.recall_at_10), desc: 'Recall at 10' },
        { label: 'Precision@5', value: pct(metrics.precision_at_5), desc: 'Precision at 5' },
        { label: 'Precision@10', value: pct(metrics.precision_at_10), desc: 'Precision at 10' },
        { label: 'MAP', value: pct(metrics.ap), desc: 'Average Precision' },
      ]
    : [];

  return (
    <section className="space-y-4">
      <div className="card p-4">
        <h2 className="text-h3 mb-3">检索评估</h2>

        {/* ── Top row: inputs ────────────────────────────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Left: Query + Collection + Top-K */}
          <div className="space-y-3">
            <div>
              <label className="block text-xs font-semibold text-warm-500 uppercase tracking-wider mb-1">
                查询文本
              </label>
              <input
                type="text"
                className="input w-full"
                placeholder="输入检索查询..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={loading}
              />
            </div>
            <div className="flex gap-3">
              <div className="flex-1">
                <label className="block text-xs font-semibold text-warm-500 uppercase tracking-wider mb-1">
                  Collection
                </label>
                <select
                  className="input w-full"
                  value={collection}
                  onChange={(e) => setCollection(e.target.value)}
                  disabled={loading}
                >
                  {COLLECTIONS.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </div>
              <div className="flex-1">
                <label className="block text-xs font-semibold text-warm-500 uppercase tracking-wider mb-1">
                  Top-K: <span className="text-primary-600 font-bold">{topK}</span>
                </label>
                <input
                  type="range"
                  min={1}
                  max={50}
                  value={topK}
                  onChange={(e) => setTopK(Number(e.target.value))}
                  disabled={loading}
                  className="w-full accent-primary-500"
                />
              </div>
            </div>
          </div>

          {/* Middle: Golden IDs + Run button */}
          <div className="space-y-3">
            <div className="flex-1">
              <label className="block text-xs font-semibold text-warm-500 uppercase tracking-wider mb-1">
                Golden Chunk IDs
                <span className="ml-1 text-warm-400 font-normal normal-case tracking-normal">
                  (每行一个 ID)
                </span>
              </label>
              <textarea
                className="input w-full resize-y"
                rows={5}
                placeholder={`chunk_id_1\nchunk_id_2\nchunk_id_3`}
                value={goldenText}
                onChange={(e) => setGoldenText(e.target.value)}
                disabled={loading}
              />
            </div>
            <button
              className="btn-primary w-full"
              onClick={() => void doEvaluate()}
              disabled={loading || !query.trim() || goldenIds.length === 0}
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                  评测中...
                </span>
              ) : (
                '运行评测 (Run Evaluation)'
              )}
            </button>
            {error && (
              <p className="text-xs text-danger-600 bg-danger-50 rounded-lg px-3 py-2">{error}</p>
            )}
          </div>

          {/* Right: Metrics stat cards */}
          <div>
            <p className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">
              评测指标
            </p>
            {metrics ? (
              <div className="grid grid-cols-2 gap-2">
                {metricCards.map((mc) => (
                  <div
                    key={mc.label}
                    className="rounded-lg border border-warm-150 bg-warm-100 px-3 py-2 shadow-card transition-all hover:shadow-card-hover hover:border-primary-200"
                    title={mc.desc}
                  >
                    <div className="text-[10px] font-semibold text-warm-400 uppercase tracking-wider">
                      {mc.label}
                    </div>
                    <div className="text-sm font-bold text-warm-900 tabular-nums mt-0.5">
                      {mc.value}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-warm-400 italic mt-2">
                输入查询和 Golden Chunk ID 后点击"运行评测"
              </p>
            )}
          </div>
        </div>
      </div>

      {/* ── Results list ─────────────────────────────────────────────── */}
      {results.length > 0 && (
        <div className="card p-4">
          <h3 className="text-sm font-semibold text-warm-700 mb-3">
            检索结果
            <span className="ml-2 text-xs font-normal text-warm-400">
              共 {results.length} 条，其中 {results.filter((r) => r.relevant).length} 条相关
            </span>
          </h3>
          <div className="space-y-2 max-h-[500px] overflow-y-auto">
            {results.map((r, i) => (
              <div
                key={r.id}
                className={`rounded-lg border px-3 py-2.5 flex items-start gap-3 transition-colors ${
                  r.relevant
                    ? 'border-success-200 bg-success-50/50'
                    : 'border-warm-100 bg-warm-100'
                }`}
              >
                {/* Rank */}
                <span className="text-xs font-mono text-warm-400 mt-0.5 shrink-0 w-6 text-right">
                  #{i + 1}
                </span>

                {/* Relevance badge */}
                <span
                  className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold shrink-0 mt-0.5 ${
                    r.relevant
                      ? 'bg-success-100 text-success-700'
                      : 'bg-warm-100 text-warm-500'
                  }`}
                >
                  {r.relevant ? '✓ 相关' : '— 无关'}
                </span>

                {/* Content */}
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-warm-700 leading-relaxed line-clamp-2">
                    {r.content}
                  </p>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-[10px] text-warm-400 font-mono truncate">
                      {r.id}
                    </span>
                    <span className="text-[10px] text-warm-400">
                      score: {r.score.toFixed(4)}
                    </span>
                    <span className="text-[10px] text-warm-400">
                      src: {r.source_id}#{r.chunk_index}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
