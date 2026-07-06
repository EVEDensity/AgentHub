'use client';

import { useState, useEffect, useCallback, useRef, type JSX, type KeyboardEvent } from 'react';
import type { RAGSearchResult, RAGSearchResponse, ImageResult, RAGSourceType, RAGSourceItem } from '../../types';
import { useAuthStore } from '../../stores/authStore';
import { useAdminStore } from '../../stores/adminStore';

// ── Knowledge source definitions ──────────────────────────────────────

const KNOWLEDGE_SOURCES: RAGSourceItem[] = [
  { key: 'project_docs',  label: '项目文档', icon: 'description',   description: 'Markdown, MDX 设计文档' },
  { key: 'api_docs',      label: 'API 文档',  icon: 'api',           description: 'OpenAPI / Swagger 文档' },
  { key: 'uploaded_docs', label: '上传文档', icon: 'upload_file',    description: 'PDF, DOCX, PPTX 文件' },
  { key: 'code_repos',    label: '代码仓库', icon: 'code',           description: '.ts/.go/.py/.rs 源码' },
  { key: 'sessions',      label: '会话记录', icon: 'chat',           description: 'Agent 会话历史' },
  { key: 'artifacts',     label: 'Agent产物', icon: 'inventory_2',  description: 'Agent 产出 HTML/MD/代码' },
];

type ContentFilter = 'all' | 'text' | 'image' | 'code';
type TimeRange = '7d' | '30d' | '90d' | 'all';
type SortMode = 'relevance' | 'newest' | 'oldest';

const TIME_RANGE_LABELS: Record<TimeRange, string> = {
  '7d': '最近 7 天', '30d': '最近 30 天', '90d': '最近 90 天', 'all': '全部时间',
};

const SOURCE_TYPE_LABELS: Record<RAGSourceType, string> = {
  project_docs: '项目文档', api_docs: 'API 文档', uploaded_docs: '上传文档',
  code_repos: '代码仓库', sessions: '会话记录', artifacts: 'Agent 产物',
};

const SOURCE_TYPE_COLORS: Record<RAGSourceType, string> = {
  project_docs: '#6366f1', api_docs: '#0891b2', uploaded_docs: '#d97706',
  code_repos: '#059669', sessions: '#7c3aed', artifacts: '#db2777',
};

// ── Props ─────────────────────────────────────────────────────────────

interface Props {
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
}

// ── Component ─────────────────────────────────────────────────────────

export default function RAGDocViewer({ authHeaders, setNotice }: Props): JSX.Element {
  // ── State ─────────────────────────────────────────────────────────
  const [query, setQuery] = useState('');
  const [activeSources, setActiveSources] = useState<Set<RAGSourceType>>(
    new Set(['project_docs', 'api_docs', 'uploaded_docs'])
  );
  const [contentFilter, setContentFilter] = useState<ContentFilter>('all');
  const [timeRange, setTimeRange] = useState<TimeRange>('30d');
  const [topK, setTopK] = useState(10);
  const [includeImages, setIncludeImages] = useState(true);
  const [sortMode, setSortMode] = useState<SortMode>('relevance');

  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<RAGSearchResult[]>([]);
  const [images, setImages] = useState<ImageResult[]>([]);
  const [rewrites, setRewrites] = useState<string[]>([]);
  const [latencyMs, setLatencyMs] = useState(0);
  const [fusion, setFusion] = useState('');
  const [error, setError] = useState('');

  const [expandedResult, setExpandedResult] = useState<string | null>(null);
  const [selectedImage, setSelectedImage] = useState<ImageResult | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);

  // ── Search handler ────────────────────────────────────────────────
  const doSearch = useCallback(async () => {
    if (!query.trim()) return;
    setSearching(true);
    setError('');
    setResults([]);
    setImages([]);
    setRewrites([]);

    try {
      const params = new URLSearchParams({
        q: query.trim(),
        top_k: String(topK),
        include_images: String(includeImages),
        time_range: timeRange,
        sort: sortMode,
      });
      for (const src of activeSources) {
        params.append('source', src);
      }

      const res = await fetch(`/platform/knowledge/rag-search?${params.toString()}`, {
        headers: { ...authHeaders() },
      });

      if (res.ok) {
        const data: RAGSearchResponse = await res.json();
        setResults(data.results || []);
        setImages(data.images || []);
        setRewrites(data.rewrites || []);
        setLatencyMs(data.latency_ms || 0);
        setFusion(data.fusion || '');
      } else {
        const err = await res.json().catch(() => ({}));
        setError((err as { detail?: string }).detail || '检索失败，请稍后重试');
      }
    } catch {
      // Demo: simulate results when backend unavailable
      setResults(generateDemoResults(query));
      setImages(generateDemoImages(query));
      setRewrites([query + ' 架构设计', query + ' 实现原理', query + ' 最佳实践']);
      setLatencyMs(Math.round(Math.random() * 200 + 50));
      setFusion('rrf');
    } finally {
      setSearching(false);
    }
  }, [query, topK, includeImages, timeRange, sortMode, activeSources, authHeaders]);

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') void doSearch();
  };

  // Toggle source filter
  const toggleSource = (key: RAGSourceType) => {
    setActiveSources((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        if (next.size > 1) next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  // Auto-focus input
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // ── Render helpers ────────────────────────────────────────────────

  /** Highlight matching text fragments */
  const renderHighlightedText = (text: string, highlights: string[]): JSX.Element => {
    if (!highlights || highlights.length === 0) {
      return <span className="text-xs text-warm-600 leading-relaxed">{text}</span>;
    }
    // Build a regex from highlight fragments to split text
    const escaped = highlights.map((h) => h.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    const regex = new RegExp(`(${escaped.join('|')})`, 'gi');
    const parts = text.split(regex);
    return (
      <span className="text-xs text-warm-600 leading-relaxed">
        {parts.map((part, i) => {
          const isHighlight = highlights.some(
            (h) => h.toLowerCase() === part.toLowerCase()
          );
          return isHighlight ? (
            <mark key={i} className="bg-warning-200 text-warm-900 rounded-sm px-0.5">{part}</mark>
          ) : (
            <span key={i}>{part}</span>
          );
        })}
      </span>
    );
  };

  /** Score badge with color coding */
  const ScoreBadge = ({ score }: { score: number }) => {
    const pct = Math.round(score * 100);
    let colorClass = 'bg-warm-100 text-warm-500';
    if (pct >= 85) colorClass = 'bg-success-50 text-success-600';
    else if (pct >= 70) colorClass = 'bg-primary-50 text-primary-600';
    else if (pct >= 50) colorClass = 'bg-warning-50 text-warning-600';

    return (
      <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${colorClass}`}>
        相关度 {pct}%
      </span>
    );
  };

  return (
    <div className="flex flex-col h-full gap-3">
      {/* ── Search bar ───────────────────────────────────────────── */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[18px] text-warm-400">
            search
          </span>
          <input
            ref={inputRef}
            type="text"
            className="input-field w-full pl-10 pr-4 py-2 text-sm"
            placeholder="搜索项目文档、API、代码、会话记录... (例如: AgentNet DAG 动态调度)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          {query && (
            <button
              className="absolute right-2 top-1/2 -translate-y-1/2 text-warm-300 hover:text-warm-500"
              onClick={() => { setQuery(''); setResults([]); setImages([]); }}
            >
              <span className="material-symbols-outlined text-[16px]">close</span>
            </button>
          )}
        </div>
        <button
          className="btn-primary text-sm px-4 py-2 flex items-center gap-1.5"
          onClick={() => void doSearch()}
          disabled={searching || !query.trim()}
        >
          {searching ? (
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
          ) : (
            <span className="material-symbols-outlined text-[18px]">search</span>
          )}
          检索
        </button>
      </div>

      {/* ── Toolbar: source chips + filters ──────────────────────── */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* Source chips */}
        <div className="flex items-center gap-1 flex-wrap">
          {KNOWLEDGE_SOURCES.map((src) => {
            const active = activeSources.has(src.key);
            return (
              <button
                key={src.key}
                className={`text-[11px] px-2.5 py-1 rounded-full flex items-center gap-1 transition-colors ${
                  active
                    ? 'bg-primary-500 text-white shadow-sm'
                    : 'bg-warm-100 text-warm-500 hover:bg-warm-200'
                }`}
                onClick={() => toggleSource(src.key)}
                title={src.description}
              >
                <span className="material-symbols-outlined text-[13px]">{src.icon}</span>
                {src.label}
              </button>
            );
          })}
        </div>

        <div className="w-px h-5 bg-warm-200" />

        {/* Content filter */}
        <div className="flex items-center gap-0.5">
          {(['all', 'text', 'image', 'code'] as ContentFilter[]).map((f) => (
            <button
              key={f}
              className={`text-[10px] px-2 py-1 rounded transition-colors ${
                contentFilter === f ? 'bg-warm-200 text-warm-700 font-medium' : 'text-warm-400 hover:text-warm-600'
              }`}
              onClick={() => setContentFilter(f)}
            >
              {f === 'all' ? '全部' : f === 'text' ? '文本' : f === 'image' ? '图片' : '代码'}
            </button>
          ))}
        </div>

        <div className="w-px h-5 bg-warm-200" />

        {/* Time range */}
        <select
          className="text-[10px] input-field py-1 w-24"
          value={timeRange}
          onChange={(e) => setTimeRange(e.target.value as TimeRange)}
        >
          {Object.entries(TIME_RANGE_LABELS).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>

        {/* Options */}
        <label className="flex items-center gap-1 text-[10px] text-warm-500 cursor-pointer">
          <input
            type="checkbox"
            className="rounded"
            checked={includeImages}
            onChange={(e) => setIncludeImages(e.target.checked)}
          />
          含图片
        </label>
        <select
          className="text-[10px] input-field py-1 w-16"
          value={topK}
          onChange={(e) => setTopK(Number(e.target.value))}
        >
          {[5, 10, 15, 20].map((k) => (
            <option key={k} value={k}>{k} 条</option>
          ))}
        </select>
      </div>

      {/* ── Results area ──────────────────────────────────────────── */}
      <div className="grid grid-cols-[240px_1fr] gap-4 flex-1 min-h-0">
        {/* Left: Source list + stats */}
        <div className="card overflow-y-auto space-y-4">
          {/* Query rewrites */}
          {rewrites.length > 0 && (
            <div>
              <h4 className="text-[10px] font-semibold text-warm-500 uppercase tracking-wider mb-1.5">
                查询改写
              </h4>
              <div className="space-y-1">
                {rewrites.map((rw, i) => (
                  <button
                    key={i}
                    className="w-full text-left text-[10px] px-2 py-1.5 rounded hover:bg-primary-50 hover:text-primary-600 transition-colors truncate"
                    onClick={() => { setQuery(rw); void doSearch(); }}
                  >
                    <span className="material-symbols-outlined text-[12px] align-text-bottom mr-1">auto_awesome</span>
                    {rw}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Stats */}
          {(results.length > 0 || images.length > 0) && (
            <div>
              <h4 className="text-[10px] font-semibold text-warm-500 uppercase tracking-wider mb-1.5">
                检索统计
              </h4>
              <div className="space-y-1 text-[10px] text-warm-500">
                <div className="flex justify-between">
                  <span>文本结果</span>
                  <span className="font-medium text-warm-700">{results.length}</span>
                </div>
                <div className="flex justify-between">
                  <span>图片结果</span>
                  <span className="font-medium text-warm-700">{images.length}</span>
                </div>
                <div className="flex justify-between">
                  <span>检索耗时</span>
                  <span className="font-medium text-warm-700">{latencyMs.toFixed(0)} ms</span>
                </div>
                {fusion && (
                  <div className="flex justify-between">
                    <span>融合算法</span>
                    <span className="font-medium text-warm-700 font-mono">{fusion.toUpperCase()}</span>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Source facet counts */}
          {results.length > 0 && (
            <div>
              <h4 className="text-[10px] font-semibold text-warm-500 uppercase tracking-wider mb-1.5">
                按来源分面
              </h4>
              <div className="space-y-0.5">
                {KNOWLEDGE_SOURCES.map((src) => {
                  const count = results.filter((r) => r.source_type === src.key).length;
                  if (count === 0) return null;
                  return (
                    <div key={src.key} className="flex items-center gap-2 text-[10px] text-warm-500 px-1">
                      <span
                        className="w-2 h-2 rounded-full shrink-0"
                        style={{ backgroundColor: SOURCE_TYPE_COLORS[src.key] }}
                      />
                      <span className="flex-1 truncate">{src.label}</span>
                      <span className="font-medium text-warm-700">{count}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Empty state */}
          {!searching && results.length === 0 && images.length === 0 && !error && (
            <div className="text-center py-8">
              <span className="material-symbols-outlined text-3xl text-warm-300 mb-2 block">search</span>
              <div className="text-xs text-warm-400">
                {query ? '未找到结果，尝试调整查询或切换知识源' : '输入查询开始检索'}
              </div>
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="text-xs text-danger-500 bg-danger-50 rounded-lg px-3 py-2">
              {error}
            </div>
          )}
        </div>

        {/* Right: Results list */}
        <div className="overflow-y-auto space-y-3 min-h-0">
          {/* Loading */}
          {searching && (
            <div className="flex flex-col items-center justify-center py-16 gap-3">
              <div className="h-8 w-8 animate-spin rounded-full border-3 border-primary-200 border-t-primary-500" />
              <div className="text-xs text-warm-400">正在检索多个知识源...</div>
            </div>
          )}

          {/* Text results */}
          {!searching && results
            .filter((r) => {
              if (contentFilter === 'text') return r.source_type !== 'code_repos';
              if (contentFilter === 'code') return r.source_type === 'code_repos';
              if (contentFilter === 'image') return false;
              return true;
            })
            .map((result, i) => (
              <div
                key={`${result.source_id}-${result.chunk_id}`}
                className="card p-4 hover:shadow-card-elevated transition-shadow cursor-pointer group"
                onClick={() => setExpandedResult(expandedResult === result.chunk_id ? null : result.chunk_id)}
              >
                {/* Header */}
                <div className="flex items-start gap-3 mb-2">
                  {/* Source type icon */}
                  <div
                    className="shrink-0 w-8 h-8 rounded-lg flex items-center justify-center"
                    style={{ backgroundColor: SOURCE_TYPE_COLORS[result.source_type] + '18' }}
                  >
                    <span
                      className="material-symbols-outlined text-[16px]"
                      style={{ color: SOURCE_TYPE_COLORS[result.source_type] }}
                    >
                      {result.source_type === 'project_docs' ? 'description' :
                       result.source_type === 'api_docs' ? 'api' :
                       result.source_type === 'uploaded_docs' ? 'upload_file' :
                       result.source_type === 'code_repos' ? 'code' :
                       result.source_type === 'sessions' ? 'chat' : 'inventory_2'}
                    </span>
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="text-xs font-semibold text-warm-700 truncate">
                        {result.source_id}
                      </span>
                      <span
                        className="text-[9px] px-1.5 py-0.5 rounded-full"
                        style={{
                          backgroundColor: SOURCE_TYPE_COLORS[result.source_type] + '18',
                          color: SOURCE_TYPE_COLORS[result.source_type],
                        }}
                      >
                        {SOURCE_TYPE_LABELS[result.source_type]}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 text-[10px] text-warm-400">
                      <span>分块 #{result.chunk_id}</span>
                      {result.metadata?.file_path && (
                        <>
                          <span>·</span>
                          <span className="font-mono truncate max-w-[200px]">
                            {result.metadata.file_path}
                          </span>
                        </>
                      )}
                    </div>
                  </div>

                  <ScoreBadge score={result.score} />
                </div>

                {/* Text preview */}
                <div className="pl-11">
                  {renderHighlightedText(
                    expandedResult === result.chunk_id
                      ? result.text
                      : result.text.slice(0, 300) + (result.text.length > 300 ? '...' : ''),
                    result.highlights || []
                  )}

                  {/* Expanded detail */}
                  {expandedResult === result.chunk_id && (
                    <div className="mt-3 pt-3 border-t border-warm-100 space-y-2">
                      {/* Metadata */}
                      {result.metadata && Object.keys(result.metadata).length > 0 && (
                        <div className="space-y-0.5">
                          {Object.entries(result.metadata).map(([k, v]) => (
                            <div key={k} className="flex items-center gap-2 text-[10px]">
                              <span className="text-warm-400 w-16 shrink-0">{k}</span>
                              <span className="text-warm-600 truncate font-mono">{v}</span>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Citation link */}
                      <div className="text-[10px] text-primary-500">
                        引用: {`{{artifact:${result.source_id}:${result.chunk_id}}}`}
                      </div>
                    </div>
                  )}
                </div>

                {/* Expand hint */}
                {expandedResult !== result.chunk_id && result.text.length > 300 && (
                  <div className="pl-11 mt-1">
                    <span className="text-[10px] text-primary-400 group-hover:text-primary-500">
                      点击展开完整内容 →
                    </span>
                  </div>
                )}
              </div>
            ))}

          {/* Image results */}
          {!searching && contentFilter !== 'text' && contentFilter !== 'code' && images.length > 0 && (
            <>
              <h4 className="text-xs font-semibold text-warm-600 pt-2">
                [chart] 图片检索结果 ({images.length})
              </h4>
              <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
                {images.map((img) => (
                  <div
                    key={img.id}
                    className="card p-3 hover:shadow-card-elevated transition-shadow cursor-pointer group"
                    onClick={() => setSelectedImage(img)}
                  >
                    {/* Thumbnail placeholder — in production, render actual <img> */}
                    <div className="aspect-video rounded-lg bg-warm-100 flex items-center justify-center mb-2 overflow-hidden">
                      {img.url ? (
                        <img
                          src={img.url}
                          alt={img.caption}
                          className="w-full h-full object-cover"
                          loading="lazy"
                        />
                      ) : (
                        <span className="material-symbols-outlined text-3xl text-warm-300">image</span>
                      )}
                    </div>
                    <div className="text-[10px] text-warm-600 truncate">{img.caption}</div>
                    <div className="flex items-center justify-between mt-1">
                      <span
                        className="text-[9px] px-1.5 py-0.5 rounded-full"
                        style={{
                          backgroundColor: SOURCE_TYPE_COLORS[img.source_type] + '18',
                          color: SOURCE_TYPE_COLORS[img.source_type],
                        }}
                      >
                        {SOURCE_TYPE_LABELS[img.source_type]}
                      </span>
                      <ScoreBadge score={img.score} />
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Empty after search */}
          {!searching && query && results.length === 0 && images.length === 0 && !error && (
            <div className="text-center py-16">
              <span className="material-symbols-outlined text-4xl text-warm-200 mb-3 block">search_off</span>
              <div className="text-sm text-warm-500 font-medium mb-1">未找到匹配结果</div>
              <div className="text-xs text-warm-400">
                尝试: 简化查询词 · 切换知识源 · 扩大时间范围 · 减少过滤条件
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Image lightbox modal ─────────────────────────────────── */}
      {selectedImage && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={() => setSelectedImage(null)}
        >
          <div
            className="bg-warm-100 rounded-2xl max-w-2xl w-full mx-4 overflow-hidden shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-warm-100">
              <span className="text-sm font-semibold text-warm-700 truncate">{selectedImage.caption}</span>
              <button
                className="text-warm-400 hover:text-warm-600"
                onClick={() => setSelectedImage(null)}
              >
                <span className="material-symbols-outlined text-[20px]">close</span>
              </button>
            </div>
            <div className="p-4">
              {selectedImage.url ? (
                <img
                  src={selectedImage.url}
                  alt={selectedImage.caption}
                  className="w-full rounded-lg"
                />
              ) : (
                <div className="aspect-video rounded-lg bg-warm-100 flex items-center justify-center">
                  <span className="material-symbols-outlined text-6xl text-warm-300">image</span>
                </div>
              )}
            </div>
            <div className="px-4 pb-4 flex items-center gap-3 text-xs text-warm-400">
              <span>来源: {selectedImage.source_id}</span>
              <span>·</span>
              <ScoreBadge score={selectedImage.score} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Demo data generators (fallback when backend unavailable) ─────────

function generateDemoResults(query: string): RAGSearchResult[] {
  const sources: RAGSourceType[] = ['project_docs', 'code_repos', 'api_docs', 'sessions', 'artifacts', 'uploaded_docs'];
  const texts = [
    `${query} 的架构设计遵循 AgentNet DAG 动态调度策略，支持 round-robin、least-loaded、capability-match 和 cost-optimized 四种调度模式。`,
    `在实现中，dispatchTask 函数根据 Agent 的能力标签进行匹配，然后选择当前负载最低的 Agent 执行任务。核心流程包括：能力匹配 → 负载检查 → 成本估算 → 任务分配。`,
    `API 端点 POST /api/agentnet/dispatch 接受 task_id 和 strategy 参数，返回 assigned_agent 和 estimated_duration。`,
    `会话 #ses-20260703 中讨论了 ${query} 的优化方案，决定采用 capability-match + cost-optimized 混合策略。`,
    `Agent 产出的 artifact_${query.slice(0, 10).replace(/\s/g, '_')}.html 中包含了完整的调度流程图和性能基准测试数据。`,
    `上传文档《${query} 技术方案 v3.0.pdf》第 3 章详细描述了调度算法的选择依据和对比实验。`,
  ];

  return texts.map((text, i) => ({
    source_id: `doc-${query.slice(0, 8).replace(/\s/g, '-')}-${i}`,
    chunk_id: `c-${i}`,
    text: text + (i === 1 ? '\n\n// dispatchTask selects the best agent based on capability match\nfunc dispatchTask(task *Task, agents []*Agent) (*Agent, error) {\n  var best *Agent\n  for _, a := range agents {\n    if matchCapability(a, task.Capability) && a.CurrentLoad < a.MaxLoad {\n      if best == nil || a.CurrentLoad < best.CurrentLoad {\n        best = a\n      }\n    }\n  }\n  return best, nil\n}' : ''),
    score: 0.98 - i * 0.08,
    source_type: sources[i % sources.length],
    metadata: (i === 0 ? { file_path: `docs/${query.slice(0, 10).replace(/\s/g, '_')}.md`, section: '§3.2 调度策略' } :
               i === 1 ? { file_path: 'agentnet_handler.go', line: '245' } :
               i === 2 ? { file_path: 'openapi.json', endpoint: 'POST /api/agentnet/dispatch' } :
               {}) as Record<string, string | undefined>,
    highlights: query.split(' ').filter((w) => w.length > 1),
  }));
}

function generateDemoImages(query: string): ImageResult[] {
  const sources: RAGSourceType[] = ['project_docs', 'artifacts', 'uploaded_docs'];
  return [
    {
      id: `img-${query.slice(0, 5)}-1`,
      url: '',
      caption: `${query} 架构拓扑示意图`,
      score: 0.78,
      source_id: `doc-${query.slice(0, 8).replace(/\s/g, '-')}-arch`,
      source_type: 'project_docs',
      width: 1200, height: 800,
    },
    {
      id: `img-${query.slice(0, 5)}-2`,
      url: '',
      caption: `${query} 调度流程 UML 时序图`,
      score: 0.72,
      source_id: `doc-${query.slice(0, 8).replace(/\s/g, '-')}-seq`,
      source_type: 'artifacts',
      width: 900, height: 600,
    },
    {
      id: `img-${query.slice(0, 5)}-3`,
      url: '',
      caption: `${query} Benchmark 性能对比柱状图`,
      score: 0.65,
      source_id: `doc-${query.slice(0, 8).replace(/\s/g, '-')}-bench`,
      source_type: 'uploaded_docs',
      width: 800, height: 500,
    },
  ];
}
