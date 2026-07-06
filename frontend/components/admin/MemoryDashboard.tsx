'use client';

import { useState, useEffect, useCallback, type JSX, type FormEvent } from 'react';

// ── Types ─────────────────────────────────────────────────────────

interface MemoryStats {
  memory_layers: {
    L0_working: { status: string; backend: string; ttl: string };
    L1_episodic: { segments: number; total_tokens: number; types: Record<string, number>; backend: string };
    L2_semantic: { status: string; backend: string; note: string };
    L3_procedural: { entities: number; relations: number; entity_types: Record<string, number>; relation_predicates: Record<string, number>; backend: string };
  };
  decisions: {
    total: number; add_count: number; update_count: number; delete_count: number; noop_count: number; dedup_rate: string;
  };
  compression: {
    total_runs: number; avg_token_saving_pct: number; last_run: CompressionRun | null;
  };
}

interface ContextSegment {
  id: string; tenant_id: string; session_id: string; segment_type: string;
  title: string; content: string; token_count: number;
  source_sequence_start: number; source_sequence_end: number; source_message_count: number;
  entities: string[]; metadata: Record<string, unknown>;
  compressed_at?: string; created_at: string;
}

interface Entity {
  id: string; tenant_id: string; entity_type: string; name: string;
  description: string; properties: Record<string, unknown>;
  source: string; confidence: number;
  last_seen_at?: string; created_at: string; updated_at: string;
}

interface Relation {
  id: string; subject_id: string; predicate: string; object_id: string;
  weight: number; evidence: string; created_at: string;
}

interface CompressionRun {
  id: string; tenant_id: string; started_at: string; completed_at?: string;
  status: string; sessions_scanned: number; sessions_compressed: number;
  messages_processed: number; tokens_before: number; tokens_after: number;
  entities_extracted: number; error_message?: string;
}

interface MemoryDecision {
  id: string; entity_id?: string; decision: string; existing_memory: string;
  new_information: string; reasoning: string; similarity_score?: number;
  conflict_detected: boolean; decided_at: string;
}

interface SearchResult {
  segments: ContextSegment[]; entities: Entity[];
  total_hits: number; sources: string[]; took_ms: number;
}

interface EntityGraph {
  entity_id: string; nodes: Entity[]; edges: Relation[];
}

// ── Color/icon constants ───────────────────────────────────────────

const LAYER_COLORS: Record<string, string> = {
  L0: '#f59e0b',
  L1: '#3b82f6',
  L2: '#8b5cf6',
  L3: '#10b981',
};

const DECISION_COLORS: Record<string, string> = {
  ADD: '#10b981',
  UPDATE: '#f59e0b',
  DELETE: '#ef4444',
  NOOP: '#6b7280',
};

const SEGMENT_TYPE_LABELS: Record<string, string> = {
  summary: '摘要',
  checkpoint: '检查点',
  entity_extract: '实体提取',
};

// ── Main Component ─────────────────────────────────────────────────

export default function MemoryDashboard({
  authHeaders,
  setNotice,
}: {
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
}): JSX.Element {
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [activeTab, setActiveTab] = useState<'overview' | 'search' | 'entities' | 'compression' | 'decisions'>('overview');
  const [loading, setLoading] = useState(false);

  // Search state
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult | null>(null);
  const [searching, setSearching] = useState(false);

  // Entity state
  const [entities, setEntities] = useState<Entity[]>([]);
  const [entityGraph, setEntityGraph] = useState<EntityGraph | null>(null);
  const [selectedEntity, setSelectedEntity] = useState<Entity | null>(null);
  const [showNewEntity, setShowNewEntity] = useState(false);

  // Compression state
  const [compRuns, setCompRuns] = useState<CompressionRun[]>([]);
  const [compressing, setCompressing] = useState(false);

  // Decisions state
  const [decisions, setDecisions] = useState<MemoryDecision[]>([]);
  const [decisionFilter, setDecisionFilter] = useState('');

  const headers = authHeaders();

  // ── Data loading ────────────────────────────────────────────────

  const loadStats = useCallback(async () => {
    try {
      const res = await fetch('/context/stats', { headers });
      if (res.ok) setStats(await res.json());
    } catch { /* ignore */ }
  }, [headers]);

  const loadEntities = useCallback(async () => {
    try {
      const res = await fetch('/context/entity', { headers });
      if (res.ok) {
        const data = await res.json();
        setEntities(data.entities || []);
      }
    } catch { /* ignore */ }
  }, [headers]);

  const loadCompressionRuns = useCallback(async () => {
    try {
      const res = await fetch('/context/compression-runs', { headers });
      if (res.ok) {
        const data = await res.json();
        setCompRuns(data.runs || []);
      }
    } catch { /* ignore */ }
  }, [headers]);

  const loadDecisions = useCallback(async () => {
    const url = decisionFilter
      ? `/context/decisions?decision=${decisionFilter}`
      : '/context/decisions';
    try {
      const res = await fetch(url, { headers });
      if (res.ok) {
        const data = await res.json();
        setDecisions(data.decisions || []);
      }
    } catch { /* ignore */ }
  }, [headers, decisionFilter]);

  useEffect(() => {
    loadStats();
    loadEntities();
    loadCompressionRuns();
    loadDecisions();
  }, [loadStats, loadEntities, loadCompressionRuns, loadDecisions]);

  // ── Handlers ────────────────────────────────────────────────────

  const handleSearch = async (e: FormEvent) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    setSearching(true);
    try {
      const res = await fetch(`/context/search?q=${encodeURIComponent(searchQuery)}`, { headers });
      if (res.ok) setSearchResults(await res.json());
    } catch { /* ignore */ }
    setSearching(false);
  };

  const handleTriggerCompression = async () => {
    setCompressing(true);
    try {
      const res = await fetch('/context/compress', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...headers },
        body: JSON.stringify({ tenant_id: '' }),
      });
      if (res.ok) {
        setNotice('睡眠压缩已触发');
        setTimeout(() => { loadStats(); loadCompressionRuns(); }, 1500);
      }
    } catch { setNotice('压缩触发失败'); }
    setCompressing(false);
  };

  const handleEntityClick = async (entity: Entity) => {
    setSelectedEntity(entity);
    try {
      const res = await fetch(`/context/entity/${entity.id}/graph`, { headers });
      if (res.ok) setEntityGraph(await res.json());
    } catch { /* ignore */ }
  };

  const handleCreateEntity = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    const data = {
      name: (form.elements.namedItem('name') as HTMLInputElement).value,
      entity_type: (form.elements.namedItem('type') as HTMLSelectElement).value,
      description: (form.elements.namedItem('desc') as HTMLTextAreaElement).value,
    };
    try {
      await fetch('/context/entity', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...headers },
        body: JSON.stringify(data),
      });
      setShowNewEntity(false);
      setNotice('实体创建成功');
      loadEntities();
      loadStats();
    } catch { setNotice('创建失败'); }
  };

  // ── Render tabs ─────────────────────────────────────────────────

  const tabs = [
    { key: 'overview' as const, label: '[chart] 总览', icon: 'dashboard' },
    { key: 'search' as const, label: '[search] 检索', icon: 'search' },
    { key: 'entities' as const, label: '[web] 实体图谱', icon: 'hub' },
    { key: 'compression' as const, label: '[moon] 压缩历史', icon: 'nights_stay' },
    { key: 'decisions' as const, label: '[brain] 记忆策略', icon: 'psychology' },
  ];

  return (
    <section className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-[34px] font-semibold leading-tight text-warm-900">ContextOS 上下文引擎</h2>
          <p className="mt-1 text-sm text-warm-500">
            统一记忆查询、实体关系图、睡眠压缩、LLM 记忆策略 — 4层分级记忆 (L0-L3)
          </p>
        </div>
        <button
          className="btn-primary"
          onClick={handleTriggerCompression}
          disabled={compressing}
        >
          {compressing ? '[hourglass] 压缩中...' : '[moon] 触发睡眠压缩'}
        </button>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-warm-150 pb-0">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            className={`px-4 py-2 text-sm rounded-t-lg transition-colors ${
              activeTab === tab.key
                ? 'bg-warm-100 border border-b-white text-primary-600 font-medium -mb-[1px] relative z-10'
                : 'text-warm-500 hover:text-warm-700 hover:bg-warm-50'
            }`}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Overview Tab ──────────────────────────────────────────── */}
      {activeTab === 'overview' && stats && (
        <div className="space-y-4">
          {/* Memory layer cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
            {Object.entries(stats.memory_layers).map(([key, layer]) => (
              <div key={key} className="card" style={{ borderTop: `3px solid ${LAYER_COLORS[key]}` }}>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-warm-500">{key.replace('_', ' ')}</span>
                  <span className="text-[10px] text-green-600 bg-green-50 px-1.5 py-0.5 rounded">● Active</span>
                </div>
                {key === 'L0_working' && (
                  <>
                    <p className="text-2xl font-bold text-warm-900">{(layer as { status: string; backend: string; ttl: string }).backend}</p>
                    <p className="text-xs text-warm-400 mt-1">TTL: {(layer as { ttl: string }).ttl}</p>
                  </>
                )}
                {key === 'L1_episodic' && (
                  <>
                    <p className="text-2xl font-bold text-warm-900">{(layer as { segments: number }).segments}</p>
                    <p className="text-xs text-warm-400 mt-1">
                      片段 · {(layer as { total_tokens: number }).total_tokens.toLocaleString()} tokens · {(layer as { backend: string }).backend}
                    </p>
                  </>
                )}
                {key === 'L2_semantic' && (
                  <>
                    <p className="text-2xl font-bold text-warm-900">{(layer as { backend: string }).backend}</p>
                    <p className="text-xs text-warm-400 mt-1">{(layer as { note: string }).note}</p>
                  </>
                )}
                {key === 'L3_procedural' && (
                  <>
                    <p className="text-2xl font-bold text-warm-900">{(layer as { entities: number }).entities} 实体</p>
                    <p className="text-xs text-warm-400 mt-1">
                      {(layer as { relations: number }).relations} 关系 · {(layer as { backend: string }).backend}
                    </p>
                  </>
                )}
              </div>
            ))}
          </div>

          {/* Decisions summary */}
          <div className="card">
            <h3 className="text-sm font-semibold text-warm-700 mb-3">[brain] LLM 记忆策略统计</h3>
            <div className="grid grid-cols-5 gap-3 text-center">
              {(['ADD', 'UPDATE', 'DELETE', 'NOOP', 'total'] as const).map((k) => {
                const key = k === 'total' ? 'total' : `${k.toLowerCase()}_count`;
                const val = (stats.decisions as Record<string, unknown>)[key] as number;
                return (
                  <div key={k} className="rounded-lg bg-warm-50 p-3">
                    <p className="text-2xl font-bold" style={{ color: DECISION_COLORS[k] || '#374151' }}>{val}</p>
                    <p className="text-xs text-warm-500 mt-0.5">
                      {k === 'ADD' ? '新增' : k === 'UPDATE' ? '更新' : k === 'DELETE' ? '删除' : k === 'NOOP' ? '跳过' : '总计'}
                    </p>
                  </div>
                );
              })}
            </div>
            <p className="text-xs text-warm-400 mt-2">去重率：{stats.decisions.dedup_rate}（NOOP / 总计）</p>
          </div>

          {/* Compression summary */}
          <div className="card">
            <h3 className="text-sm font-semibold text-warm-700 mb-3">[moon] 睡眠压缩摘要</h3>
            <div className="flex items-center gap-6">
              <div className="text-center">
                <p className="text-2xl font-bold text-warm-900">{stats.compression.total_runs}</p>
                <p className="text-xs text-warm-500">总运行次数</p>
              </div>
              <div className="text-center">
                <p className="text-2xl font-bold text-green-600">{stats.compression.avg_token_saving_pct}%</p>
                <p className="text-xs text-warm-500">平均 Token 节省率</p>
              </div>
              {stats.compression.last_run && (
                <div className="flex-1 text-xs text-warm-500">
                  <p>最近运行：{new Date(stats.compression.last_run.started_at).toLocaleString('zh-CN')}</p>
                  <p>
                    扫描 {stats.compression.last_run.sessions_scanned} 会话 ·
                    压缩 {stats.compression.last_run.sessions_compressed} 个 ·
                    Token {stats.compression.last_run.tokens_before.toLocaleString()} → {stats.compression.last_run.tokens_after.toLocaleString()}
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Search Tab ────────────────────────────────────────────── */}
      {activeTab === 'search' && (
        <div className="space-y-4">
          <form className="flex gap-2" onSubmit={handleSearch}>
            <input
              className="input-field flex-1"
              placeholder="搜索记忆：如 '上周讨论过的数据库方案'、'微服务架构'..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            <button type="submit" className="btn-primary" disabled={searching}>
              {searching ? '搜索中...' : '搜索'}
            </button>
          </form>

          {searchResults && (
            <div className="space-y-3">
              <div className="flex items-center gap-3 text-xs text-warm-500">
                <span>共 {searchResults.total_hits} 条结果</span>
                <span>·</span>
                <span>耗时 {searchResults.took_ms.toFixed(1)}ms</span>
                <span>·</span>
                <span>来源：{searchResults.sources.join(', ') || '无'}</span>
              </div>

              {searchResults.segments.length > 0 && (
                <div>
                    <h4 className="text-sm font-medium text-warm-700 mb-2">
                      [memo] L1 情节记忆片段 ({searchResults.segments.length})
                  </h4>
                  <div className="space-y-2">
                    {searchResults.segments.map((seg) => (
                      <div key={seg.id} className="card">
                        <div className="flex items-start justify-between mb-1">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-medium text-primary-600">
                              {SEGMENT_TYPE_LABELS[seg.segment_type] || seg.segment_type}
                            </span>
                            {seg.title && <span className="text-sm font-medium text-warm-800">{seg.title}</span>}
                          </div>
                          <span className="text-[10px] text-warm-400">{seg.token_count} tokens</span>
                        </div>
                        <p className="text-xs text-warm-600 line-clamp-3 whitespace-pre-wrap">{seg.content}</p>
                        <div className="flex gap-3 mt-1.5 text-[10px] text-warm-400">
                          <span>Session: {seg.session_id}</span>
                          <span>·</span>
                          <span>Messages {seg.source_sequence_start}-{seg.source_sequence_end}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {searchResults.entities.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium text-warm-700 mb-2">
                    [web] L3 实体 ({searchResults.entities.length})
                  </h4>
                  <div className="flex flex-wrap gap-2">
                    {searchResults.entities.map((ent) => (
                      <button
                        key={ent.id}
                        className="card px-3 py-2 cursor-pointer hover:shadow-md transition-shadow text-left"
                        onClick={() => {
                          handleEntityClick(ent);
                          setActiveTab('entities');
                        }}
                      >
                        <span className="text-xs font-medium text-warm-800">{ent.name}</span>
                        <span className="text-[10px] text-warm-400 ml-2">{ent.entity_type}</span>
                        {ent.description && (
                          <p className="text-[10px] text-warm-500 mt-0.5 line-clamp-1">{ent.description}</p>
                        )}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {searchResults.total_hits === 0 && (
                <div className="text-center py-12 text-warm-400 text-sm">未找到相关记忆</div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Entities Tab ───────────────────────────────────────────── */}
      {activeTab === 'entities' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Entity list */}
          <div className="lg:col-span-1 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-warm-700">实体列表 ({entities.length})</h3>
              <button className="btn-ghost text-xs px-2 py-1" onClick={() => setShowNewEntity(true)}>+ 新建</button>
            </div>
            <div className="space-y-1 max-h-[500px] overflow-y-auto">
              {entities.map((ent) => (
                <button
                  key={ent.id}
                  className={`w-full text-left card px-3 py-2.5 cursor-pointer hover:shadow-sm transition-shadow ${
                    selectedEntity?.id === ent.id ? 'ring-2 ring-primary-300 border-primary-300' : ''
                  }`}
                  onClick={() => handleEntityClick(ent)}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-warm-800 truncate">{ent.name}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-warm-100 text-warm-500 shrink-0 ml-2">
                      {ent.entity_type}
                    </span>
                  </div>
                  {ent.description && (
                    <p className="text-[10px] text-warm-500 mt-0.5 line-clamp-2">{ent.description}</p>
                  )}
                </button>
              ))}
            </div>

            {/* New entity modal */}
            {showNewEntity && (
              <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 px-4 pt-[10vh]" onClick={() => setShowNewEntity(false)}>
                <form className="w-full max-w-md rounded-2xl bg-warm-100 shadow-2xl p-6 space-y-4" onClick={(e) => e.stopPropagation()} onSubmit={handleCreateEntity}>
                  <h4 className="text-lg font-semibold">新建实体</h4>
                  <input name="name" className="input-field w-full" placeholder="实体名称" required />
                  <select name="type" className="input-field w-full" defaultValue="concept">
                    <option value="user">User</option>
                    <option value="agent">Agent</option>
                    <option value="session">Session</option>
                    <option value="tool">Tool</option>
                    <option value="document">Document</option>
                    <option value="concept">Concept</option>
                    <option value="project">Project</option>
                  </select>
                  <textarea name="desc" className="input-field w-full" rows={3} placeholder="描述" />
                  <div className="flex justify-end gap-2">
                    <button type="button" className="btn-secondary" onClick={() => setShowNewEntity(false)}>取消</button>
                    <button type="submit" className="btn-primary">创建</button>
                  </div>
                </form>
              </div>
            )}
          </div>

          {/* Entity detail + graph */}
          <div className="lg:col-span-2">
            {selectedEntity ? (
              <div className="space-y-3">
                <div className="card">
                  <h3 className="text-lg font-semibold text-warm-900">{selectedEntity.name}</h3>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-xs px-2 py-0.5 rounded bg-primary-50 text-primary-600">{selectedEntity.entity_type}</span>
                    <span className="text-xs text-warm-400">置信度: {(selectedEntity.confidence * 100).toFixed(0)}%</span>
                  </div>
                  <p className="text-sm text-warm-600 mt-3">{selectedEntity.description || '无描述'}</p>
                  {Object.keys(selectedEntity.properties).length > 0 && (
                    <div className="mt-3">
                      <p className="text-xs font-medium text-warm-500 mb-1">属性</p>
                      <pre className="text-xs bg-warm-50 rounded-lg p-3 overflow-x-auto">
                        {JSON.stringify(selectedEntity.properties, null, 2)}
                      </pre>
                    </div>
                  )}
                  <p className="text-[10px] text-warm-400 mt-3">
                    创建: {new Date(selectedEntity.created_at).toLocaleString('zh-CN')} ·
                    更新: {new Date(selectedEntity.updated_at).toLocaleString('zh-CN')}
                  </p>
                </div>

                {/* Relation graph visualization */}
                {entityGraph && (
                  <div className="card">
                    <h4 className="text-sm font-semibold text-warm-700 mb-3">
                      关系图谱 ({entityGraph.nodes.length} 节点 · {entityGraph.edges.length} 边)
                    </h4>
                    <div className="space-y-2">
                      {/* Nodes */}
                      <div className="flex flex-wrap gap-2">
                        {entityGraph.nodes.map((node) => (
                          <div
                            key={node.id}
                            className={`text-xs px-3 py-1.5 rounded-full border-2 ${
                              node.id === selectedEntity.id
                                ? 'border-primary-400 bg-primary-50 text-primary-700 font-semibold'
                                : 'border-warm-200 bg-warm-100 text-warm-600'
                            }`}
                          >
                            {node.name}
                            <span className="text-[10px] text-warm-400 ml-1">({node.entity_type})</span>
                          </div>
                        ))}
                      </div>
                      {/* Edges */}
                      {entityGraph.edges.length > 0 && (
                        <div className="space-y-1 mt-3">
                          {entityGraph.edges.map((edge) => (
                            <div key={edge.id} className="flex items-center gap-2 text-xs">
                              <span className="font-medium text-warm-700">
                                {entityGraph.nodes.find((n) => n.id === edge.subject_id)?.name || edge.subject_id}
                              </span>
                              <span
                                className="px-2 py-0.5 rounded text-[10px] font-medium"
                                style={{
                                  backgroundColor: DECISION_COLORS.ADD + '18',
                                  color: DECISION_COLORS.ADD,
                                }}
                              >
                                {edge.predicate}
                              </span>
                              <span className="font-medium text-warm-700">
                                {entityGraph.nodes.find((n) => n.id === edge.object_id)?.name || edge.object_id}
                              </span>
                              {edge.evidence && (
                                <span className="text-[10px] text-warm-400 truncate max-w-[200px]" title={edge.evidence}>
                                  — {edge.evidence}
                                </span>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="card text-center py-12 text-warm-400">
                <p className="text-4xl mb-2">[web]</p>
                <p>选择一个实体查看关系图谱</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Compression Tab ────────────────────────────────────────── */}
      {activeTab === 'compression' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-warm-700">压缩运行历史 ({compRuns.length})</h3>
            <button className="btn-secondary text-xs" onClick={handleTriggerCompression} disabled={compressing}>
              {compressing ? '执行中...' : '[moon] 手动触发'}
            </button>
          </div>
          {compRuns.length === 0 ? (
            <div className="card text-center py-12 text-warm-400">
              <p>暂无压缩记录</p>
              <p className="text-xs mt-1">点击"手动触发"执行首次睡眠压缩</p>
            </div>
          ) : (
            <div className="space-y-2">
              {compRuns.map((run) => {
                const saving = run.tokens_before > 0
                  ? ((run.tokens_before - run.tokens_after) / run.tokens_before * 100).toFixed(1)
                  : '0';
                return (
                  <div key={run.id} className="card">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className={`w-2 h-2 rounded-full ${
                          run.status === 'completed' ? 'bg-green-500' : run.status === 'running' ? 'bg-yellow-500 animate-pulse' : 'bg-red-500'
                        }`} />
                        <span className="text-sm font-medium text-warm-800">
                          {new Date(run.started_at).toLocaleString('zh-CN')}
                        </span>
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                          run.status === 'completed' ? 'bg-green-50 text-green-600' : 'bg-yellow-50 text-yellow-600'
                        }`}>
                          {run.status === 'completed' ? '完成' : run.status === 'running' ? '进行中' : '失败'}
                        </span>
                      </div>
                      <div className="text-xs text-warm-500">
                        耗时: {run.completed_at
                          ? (new Date(run.completed_at).getTime() - new Date(run.started_at).getTime())
                          : '...'} ms
                      </div>
                    </div>
                    <div className="grid grid-cols-4 gap-3 text-center text-xs">
                      <div>
                        <p className="font-semibold text-warm-700">{run.sessions_scanned}</p>
                        <p className="text-warm-400">扫描会话</p>
                      </div>
                      <div>
                        <p className="font-semibold text-warm-700">{run.sessions_compressed}</p>
                        <p className="text-warm-400">已压缩</p>
                      </div>
                      <div>
                        <p className="font-semibold text-warm-700">{run.tokens_before.toLocaleString()}</p>
                        <p className="text-warm-400">压缩前 Tokens</p>
                      </div>
                      <div>
                        <p className="font-semibold text-green-600">{saving}%</p>
                        <p className="text-warm-400">Token 节省率</p>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ── Decisions Tab ──────────────────────────────────────────── */}
      {activeTab === 'decisions' && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-warm-700">记忆策略决策日志</h3>
            <select
              className="input-field text-xs w-auto"
              value={decisionFilter}
              onChange={(e) => setDecisionFilter(e.target.value)}
            >
              <option value="">全部</option>
              <option value="ADD">ADD</option>
              <option value="UPDATE">UPDATE</option>
              <option value="DELETE">DELETE</option>
              <option value="NOOP">NOOP</option>
            </select>
            <span className="text-xs text-warm-400">{decisions.length} 条记录</span>
          </div>

          {decisions.length === 0 ? (
            <div className="card text-center py-12 text-warm-400">
              <p>暂无决策记录</p>
              <p className="text-xs mt-1">当 LLM 处理记忆更新时，策略决策将在此显示</p>
            </div>
          ) : (
            <div className="space-y-2 max-h-[600px] overflow-y-auto">
              {decisions.map((dec) => (
                <div key={dec.id} className="card">
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span
                        className="text-xs font-bold px-2 py-0.5 rounded"
                        style={{ backgroundColor: DECISION_COLORS[dec.decision] + '18', color: DECISION_COLORS[dec.decision] }}
                      >
                        {dec.decision}
                      </span>
                      {dec.entity_id && (
                        <span className="text-[10px] text-warm-400">实体: {dec.entity_id}</span>
                      )}
                      {dec.conflict_detected && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-50 text-red-600">冲突</span>
                      )}
                    </div>
                    <span className="text-[10px] text-warm-400">
                      {new Date(dec.decided_at).toLocaleString('zh-CN')}
                    </span>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
                    <div className="bg-warm-50 rounded-lg p-2">
                      <p className="text-warm-400 mb-0.5">现有记忆:</p>
                      <p className="text-warm-700 line-clamp-3">{dec.existing_memory || '(空)'}</p>
                    </div>
                    <div className="bg-warm-50 rounded-lg p-2">
                      <p className="text-warm-400 mb-0.5">新信息:</p>
                      <p className="text-warm-700 line-clamp-3">{dec.new_information}</p>
                    </div>
                  </div>
                  {dec.reasoning && (
                    <p className="text-xs text-warm-500 mt-2 italic">[bulb] {dec.reasoning}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Loading state ──────────────────────────────────────────── */}
      {!stats && (
        <div className="card text-center py-12">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500 mx-auto" />
          <p className="text-sm text-warm-400 mt-3">加载 ContextOS 状态...</p>
        </div>
      )}
    </section>
  );
}
