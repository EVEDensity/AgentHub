import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react';
import type { AuditLog } from '../../types';

interface AuditLogListProps {
  authHeaders: () => Record<string, string>;
}

interface PaginatedResponse {
  items: AuditLog[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

/* ── RiskBadge 组件 ────────────────────────────────────────── */
function RiskBadge({ level }: { level: string }): JSX.Element {
  const config = {
    L1: { bg: 'bg-success-100', text: 'text-success-700', ring: 'ring-success-300', label: 'L1 低风险' },
    low: { bg: 'bg-success-100', text: 'text-success-700', ring: 'ring-success-300', label: '低风险' },
    L2: { bg: 'bg-warning-100', text: 'text-warning-700', ring: 'ring-warning-300', label: 'L2 中风险' },
    medium: { bg: 'bg-warning-100', text: 'text-warning-700', ring: 'ring-warning-300', label: '中风险' },
    L3: { bg: 'bg-warning-100', text: 'text-warning-700', ring: 'ring-warning-300', label: 'L3 高风险' },
    high: { bg: 'bg-warning-100', text: 'text-warning-700', ring: 'ring-warning-300', label: '高风险' },
    L4: { bg: 'bg-danger-100', text: 'text-danger-700', ring: 'ring-danger-300', label: 'L4 严重' },
  };
  const c = config[level as keyof typeof config] || { bg: 'bg-warm-100', text: 'text-warm-700', ring: 'ring-warm-300', label: level };
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${c.bg} ${c.text} ${c.ring}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${level === 'L4' || level === 'high' ? 'bg-danger-500 animate-pulse' : level === 'L3' ? 'bg-warning-500' : level === 'L2' || level === 'medium' ? 'bg-warning-500' : 'bg-success-500'}`} />
      {c.label}
    </span>
  );
}

/* ── DecisionBadge 组件 ────────────────────────────────────── */
function DecisionBadge({ decision }: { decision: string }): JSX.Element {
  const config = {
    approve: { bg: 'bg-success-100', text: 'text-success-700', icon: '✓', label: '批准' },
    deny: { bg: 'bg-danger-100', text: 'text-danger-700', icon: '✗', label: '拒绝' },
    reject: { bg: 'bg-danger-100', text: 'text-danger-700', icon: '✗', label: '拒绝' },
    auto: { bg: 'bg-primary-100', text: 'text-primary-700', icon: '⚡', label: '自动' },
    confirm: { bg: 'bg-warning-100', text: 'text-warning-700', icon: '⏳', label: '待确认' },
    pending: { bg: 'bg-warning-100', text: 'text-warning-700', icon: '⏳', label: '待确认' },
  };
  const c = config[decision as keyof typeof config] || { bg: 'bg-warm-100', text: 'text-warm-700', icon: '?', label: decision };
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium ${c.bg} ${c.text}`}>
      <span className="text-[12px]">{c.icon}</span>
      {c.label}
    </span>
  );
}

/* ── 统计卡片组件 ──────────────────────────────────────────── */
function StatCard({ label, value, bg, text, icon, subtitle }: { label: string; value: number | string; bg: string; text: string; icon: string; subtitle?: string }): JSX.Element {
  return (
    <div className={`rounded-xl border ${bg} ${text} px-5 py-4 flex items-center gap-4 shadow-sm`}>
      <span className="text-[28px]">{icon}</span>
      <div>
        <div className="text-[28px] font-bold leading-tight tracking-tight">{value}</div>
        <div className="text-xs font-medium opacity-80 mt-0.5">{label}</div>
        {subtitle && <div className="text-[10px] opacity-60 mt-0.5">{subtitle}</div>}
      </div>
    </div>
  );
}

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    return d.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return ts;
  }
}

function truncate(str: string, max: number): string {
  return str.length > max ? str.slice(0, max) + '…' : str;
}

/* ── JSON 语法高亮 ─────────────────────────────────────────── */
function JsonHighlight({ json }: { json: string }): JSX.Element {
  let parsed: unknown;
  try { parsed = JSON.parse(json); } catch { return <pre className="whitespace-pre-wrap text-xs font-mono">{json}</pre>; }
  const formatted = JSON.stringify(parsed, null, 2);
  const highlighted = formatted.replace(
    /("(?:\\.|[^"\\])*")\s*:|("(?:\\.|[^"\\])*")|(\btrue\b|\bfalse\b|\bnull\b)|(-?\d+\.?\d*)/g,
    (_, key, str, bool, num) => {
      if (key) return `<span class="text-primary-600">${key}</span>:`;
      if (str) return `<span class="text-success-700">${str}</span>`;
      if (bool) return `<span class="text-primary-600 font-medium">${bool}</span>`;
      if (num) return `<span class="text-warning-600">${num}</span>`;
      return _;
    },
  );
  return (
    <pre
      className="whitespace-pre-wrap text-xs font-mono leading-relaxed"
      dangerouslySetInnerHTML={{ __html: highlighted }}
    />
  );
}

export default function AuditLogList({ authHeaders }: AuditLogListProps): JSX.Element {
  // ── Stable ref for authHeaders to avoid useEffect dependency churn ──
  const authRef = useRef(authHeaders);
  authRef.current = authHeaders;

  // ── Data state ─────────────────────────────────────────────────────
  const [data, setData] = useState<PaginatedResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // ── Pagination state ───────────────────────────────────────────────
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);

  // ── Sort state ─────────────────────────────────────────────────────
  const [sortBy, setSortBy] = useState('timestamp');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  // ── Filter state ───────────────────────────────────────────────────
  const [filterAgentId, setFilterAgentId] = useState('');
  const [filterAction, setFilterAction] = useState('');
  const [filterRiskLevel, setFilterRiskLevel] = useState('');
  const [filterSearch, setFilterSearch] = useState('');

  // Debounced search for free-text input
  const [searchInput, setSearchInput] = useState('');

  // ── Detail modal state ─────────────────────────────────────────────
  const [detailLog, setDetailLog] = useState<AuditLog | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // ── Fetch data ─────────────────────────────────────────────────────
  const fetchLogs = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams();
      params.set('page', String(page));
      params.set('pageSize', String(pageSize));
      params.set('sortBy', sortBy);
      params.set('sortOrder', sortOrder);
      if (filterAgentId) params.set('agentId', filterAgentId);
      if (filterAction) params.set('action', filterAction);
      if (filterRiskLevel) params.set('riskLevel', filterRiskLevel);
      if (filterSearch) params.set('search', filterSearch);

      const res = await fetch(`/api/admin/audit/logs?${params.toString()}`, {
        headers: authRef.current(),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error((errData as { detail?: string }).detail || `HTTP ${res.status}`);
      }
      const result = (await res.json()) as PaginatedResponse;
      setData(result);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '加载审计日志失败');
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, sortBy, sortOrder, filterAgentId, filterAction, filterRiskLevel, filterSearch]);

  useEffect(() => {
    void fetchLogs();
  }, [fetchLogs]);

  // Debounce search input → filterSearch
  useEffect(() => {
    const timer = setTimeout(() => {
      setFilterSearch(searchInput);
      setPage(1);
    }, 350);
    return () => clearTimeout(timer);
  }, [searchInput]);

  // Reset page when filters change
  const applyFilter = useCallback(() => {
    setPage(1);
    // Trigger via state change → effect above
  }, []);

  // Apply immediate filters (dropdowns/selects)
  useEffect(() => {
    setPage(1);
  }, [filterAgentId, filterAction, filterRiskLevel]);

  // ── Open detail ────────────────────────────────────────────────────
  async function openDetail(log: AuditLog): Promise<void> {
    setDetailLog(log);
    // If we already have payload, show immediately; otherwise fetch
    if (!log.payload) {
      setDetailLoading(true);
      try {
        const res = await fetch(`/api/admin/audit/logs/${encodeURIComponent(String(log.id))}`, {
          headers: authRef.current(),
        });
        if (res.ok) {
          setDetailLog((await res.json()) as AuditLog);
        }
      } catch {
        // keep the current detailLog
      } finally {
        setDetailLoading(false);
      }
    }
  }

  function closeDetail(): void {
    setDetailLog(null);
  }

  // ── Sort handler ───────────────────────────────────────────────────
  function handleSort(column: string): void {
    if (sortBy === column) {
      setSortOrder((o) => (o === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(column);
      setSortOrder('desc');
    }
  }

  function sortIndicator(column: string): JSX.Element | null {
    if (sortBy !== column) {
      return (
        <span className="inline-block w-4 text-warm-300 text-[10px] ml-1">
          ▸
        </span>
      );
    }
    return (
      <span className="inline-block w-4 text-primary-500 text-[10px] ml-1">
        {sortOrder === 'asc' ? '▲' : '▼'}
      </span>
    );
  }

  // ── Active filter count ────────────────────────────────────────────
  const activeFilterCount = useMemo(() => {
    return [filterAgentId, filterAction, filterRiskLevel, filterSearch].filter(Boolean).length;
  }, [filterAgentId, filterAction, filterRiskLevel, filterSearch]);

  function clearFilters(): void {
    setFilterAgentId('');
    setFilterAction('');
    setFilterRiskLevel('');
    setSearchInput('');
    setFilterSearch('');
    setPage(1);
  }

  // ── Render ─────────────────────────────────────────────────────────
  return (
    <section className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-[34px] font-semibold leading-tight text-warm-900">审计日志</h2>
          <p className="mt-1 text-sm text-warm-500">
            仅显示您账号的操作记录。记录所有管理操作和敏感事件，支持追溯与审查。
          </p>
        </div>
        <button className="btn-secondary" onClick={() => fetchLogs()} disabled={loading}>
          {loading ? (
            <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-warm-400 border-t-transparent mr-1 align-text-bottom" />
          ) : (
            <span className="material-symbols-outlined text-[18px] align-text-bottom mr-1">refresh</span>
          )}
          刷新
        </button>
      </div>

      {/* Stats cards */}
      {data && (
        <div className="grid grid-cols-4 gap-4">
          <StatCard
            label="总审计记录"
            value={data.total.toLocaleString()}
            bg="bg-warm-100 border-warm-200"
            text="text-warm-800"
            icon="[clipboard]"
          />
          <StatCard
            label="高风险操作"
            value={data.items.filter(l => l.riskLevel === 'L3' || l.riskLevel === 'L4' || l.riskLevel === 'high').length}
            bg="bg-danger-50 border-danger-200"
            text="text-danger-700"
            icon="[red]"
            subtitle={`共 ${data.items.filter(l => l.riskLevel === 'L3' || l.riskLevel === 'L4' || l.riskLevel === 'high').length > 0 ? data.items.filter(l => l.riskLevel === 'L3' || l.riskLevel === 'L4' || l.riskLevel === 'high').length : 0} 条高风险`}
          />
          <StatCard
            label="Agent 执行"
            value={data.items.filter(l => l.action.includes('agent_execute')).length}
            bg="bg-primary-50 border-primary-200"
            text="text-primary-700"
            icon="[bot]"
          />
          <StatCard
            label="已批准操作"
            value={data.items.filter(l => l.decision === 'approve' || l.decision === 'auto').length}
            bg="bg-success-50 border-success-200"
            text="text-success-700"
            icon="[check]"
          />
        </div>
      )}

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-warm-200 bg-warm-100 px-4 py-3">
        {/* Free-text search */}
        <div className="flex items-center gap-1.5 rounded-lg border border-warm-200 bg-warm-50 px-3 py-2 flex-1 min-w-[200px] max-w-[320px] transition-colors focus-within:border-primary-300 focus-within:bg-warm-100">
          <span className="material-symbols-outlined text-[18px] text-warm-400 shrink-0">search</span>
          <input
            className="min-w-0 flex-1 bg-transparent text-sm text-warm-800 outline-none placeholder:text-warm-400"
            placeholder="搜索操作、用户、Agent…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
          {searchInput && (
            <button
              className="shrink-0 text-warm-400 hover:text-warm-600"
              onClick={() => { setSearchInput(''); setFilterSearch(''); }}
            >
              <span className="material-symbols-outlined text-[16px]">close</span>
            </button>
          )}
        </div>

        {/* Agent ID filter */}
        <input
          className="rounded-lg border border-warm-200 bg-warm-50 px-3 py-2 text-sm text-warm-700 outline-none placeholder:text-warm-400 w-[130px] transition-colors focus:border-primary-300 focus:bg-warm-100"
          placeholder="Agent ID"
          value={filterAgentId}
          onChange={(e) => setFilterAgentId(e.target.value)}
        />

        {/* Action filter */}
        <select
          className="rounded-lg border border-warm-200 bg-warm-50 px-3 py-2 text-sm text-warm-700 outline-none min-w-[110px] transition-colors focus:border-primary-300"
          value={filterAction}
          onChange={(e) => setFilterAction(e.target.value)}
        >
          <option value="">全部操作</option>
          <option value="agent_create">创建 Agent</option>
          <option value="workflow_create">创建工作流</option>
          <option value="workflow_update">更新工作流</option>
          <option value="workflow_delete">删除工作流</option>
          <option value="confirm">确认操作</option>
          <option value="set_default_chat_agent">设置默认 Agent</option>
          <option value="git_commit">Git 提交</option>
        </select>

        {/* Risk level filter */}
        <select
          className="rounded-lg border border-warm-200 bg-warm-50 px-3 py-2 text-sm text-warm-700 outline-none min-w-[110px] transition-colors focus:border-primary-300"
          value={filterRiskLevel}
          onChange={(e) => setFilterRiskLevel(e.target.value)}
        >
          <option value="">全部级别</option>
          <option value="L1">L1 · 低风险</option>
          <option value="L2">L2 · 中风险</option>
          <option value="L3">L3 · 高风险</option>
          <option value="L4">L4 · 严重</option>
        </select>

        {/* Active filter badge */}
        {activeFilterCount > 0 && (
          <button
            className="btn-ghost px-2 py-1.5 text-xs text-warm-500 hover:text-warm-700 shrink-0"
            onClick={clearFilters}
          >
            <span className="material-symbols-outlined text-[14px] mr-1 align-text-bottom">filter_alt_off</span>
            重置筛选
          </button>
        )}
      </div>

      {/* Loading state */}
      {loading && !data && (
        <div className="flex justify-center py-16 rounded-2xl border border-warm-200 bg-warm-100">
          <div className="flex flex-col items-center gap-3">
            <div className="h-8 w-8 animate-spin rounded-full border-[3px] border-warm-300 border-t-primary-500" />
            <span className="text-sm text-warm-500">加载审计日志...</span>
          </div>
        </div>
      )}

      {/* Error state */}
      {error && !data && (
        <div className="rounded-2xl border border-danger-200 bg-danger-50 px-6 py-10 text-center">
          <span className="material-symbols-outlined text-[40px] text-danger-400 mb-3 block">error_outline</span>
          <p className="text-sm text-danger-600 mb-4">{error}</p>
          <button className="btn-primary" onClick={() => fetchLogs()}>
            重试
          </button>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && data && data.items.length === 0 && (
        <div className="rounded-2xl border border-dashed border-warm-200 bg-warm-100 px-8 py-16 text-center">
          <span className="material-symbols-outlined text-[56px] text-warm-300 mb-4 block">receipt_long</span>
          <h3 className="text-base font-semibold text-warm-700 mb-1">暂无审计日志</h3>
          <p className="text-sm text-warm-500 max-w-md mx-auto">
            {activeFilterCount > 0
              ? '当前筛选条件下未找到匹配的审计记录，请尝试调整筛选条件。'
              : '系统尚未记录任何管理操作或敏感事件。当管理员执行操作时，日志将自动生成。'}
          </p>
          {activeFilterCount > 0 && (
            <button className="btn-secondary mt-4" onClick={clearFilters}>
              清除所有筛选
            </button>
          )}
        </div>
      )}

      {/* Data table */}
      {data && data.items.length > 0 && (
        <>
          <div className="overflow-hidden rounded-2xl border border-warm-200 bg-warm-100">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-warm-150 bg-warm-50">
                    <th className="px-4 py-3 text-xs font-semibold text-warm-500 uppercase tracking-wider w-[180px]">
                      <button
                        className="inline-flex items-center hover:text-warm-700"
                        onClick={() => handleSort('timestamp')}
                      >
                        时间{sortIndicator('timestamp')}
                      </button>
                    </th>
                    <th className="px-4 py-3 text-xs font-semibold text-warm-500 uppercase tracking-wider w-[120px]">
                      <button
                        className="inline-flex items-center hover:text-warm-700"
                        onClick={() => handleSort('userId')}
                      >
                        用户{sortIndicator('userId')}
                      </button>
                    </th>
                    <th className="px-4 py-3 text-xs font-semibold text-warm-500 uppercase tracking-wider w-[140px]">
                      <button
                        className="inline-flex items-center hover:text-warm-700"
                        onClick={() => handleSort('agentId')}
                      >
                        Agent{sortIndicator('agentId')}
                      </button>
                    </th>
                    <th className="px-4 py-3 text-xs font-semibold text-warm-500 uppercase tracking-wider w-[160px]">
                      <button
                        className="inline-flex items-center hover:text-warm-700"
                        onClick={() => handleSort('action')}
                      >
                        操作{sortIndicator('action')}
                      </button>
                    </th>
                    <th className="px-4 py-3 text-xs font-semibold text-warm-500 uppercase tracking-wider w-[100px]">
                      <button
                        className="inline-flex items-center hover:text-warm-700"
                        onClick={() => handleSort('riskLevel')}
                      >
                        风险{sortIndicator('riskLevel')}
                      </button>
                    </th>
                    <th className="px-4 py-3 text-xs font-semibold text-warm-500 uppercase tracking-wider w-[100px]">
                      <button
                        className="inline-flex items-center hover:text-warm-700"
                        onClick={() => handleSort('decision')}
                      >
                        决策{sortIndicator('decision')}
                      </button>
                    </th>
                    <th className="px-4 py-3 text-xs font-semibold text-warm-500 uppercase tracking-wider">
                      操作
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-warm-100">
                  {data.items.map((log) => (
                    <tr
                      key={log.id}
                      className="group hover:bg-primary-50/40 cursor-pointer transition-colors"
                      onClick={() => openDetail(log)}
                    >
                      <td className="px-4 py-2.5 text-xs text-warm-600 font-mono whitespace-nowrap">
                        {formatTimestamp(log.timestamp)}
                      </td>
                      <td className="px-4 py-2.5 text-sm text-warm-800 font-medium max-w-[140px] truncate">
                        {log.userId}
                      </td>
                      <td className="px-4 py-2.5 text-sm text-warm-700 max-w-[160px] truncate">
                        {log.agentId}
                      </td>
                      <td className="px-4 py-2.5">
                        <code className="text-xs bg-warm-50 rounded px-1.5 py-0.5 text-warm-700 font-mono max-w-[160px] truncate inline-block">
                          {truncate(log.action, 38)}
                        </code>
                      </td>
                      <td className="px-4 py-2.5">
                        <RiskBadge level={log.riskLevel} />
                      </td>
                      <td className="px-4 py-2.5">
                        <DecisionBadge decision={log.decision} />
                      </td>
                      <td className="px-4 py-2.5">
                        <span className="material-symbols-outlined text-[16px] text-warm-400 opacity-0 group-hover:opacity-100 transition-opacity">
                          visibility
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between rounded-2xl border border-warm-200 bg-warm-100 px-5 py-3">
            {/* Page size selector */}
            <div className="flex items-center gap-2 text-sm text-warm-500">
              <span>每页</span>
              <select
                className="rounded-lg border border-warm-200 bg-warm-50 px-2 py-1 text-sm text-warm-700 outline-none"
                value={pageSize}
                onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
              >
                {PAGE_SIZE_OPTIONS.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
              <span>条</span>
              <span className="ml-3 text-warm-400">
                第 {data.page} / {data.totalPages} 页，共 {data.total.toLocaleString()} 条
              </span>
            </div>

            {/* Page nav */}
            <div className="flex items-center gap-1">
              <button
                className="btn-ghost px-3 py-1.5 text-sm disabled:opacity-30"
                disabled={page <= 1}
                onClick={() => setPage(1)}
                title="第一页"
              >
                <span className="material-symbols-outlined text-[16px]">first_page</span>
              </button>
              <button
                className="btn-ghost px-3 py-1.5 text-sm disabled:opacity-30"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                title="上一页"
              >
                <span className="material-symbols-outlined text-[16px]">chevron_left</span>
              </button>

              {/* Page number pills */}
              {(() => {
                const totalPages = data.totalPages;
                if (totalPages <= 7) {
                  return Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
                    <button
                      key={p}
                      className={`inline-flex h-8 w-8 items-center justify-center rounded-lg text-sm font-medium transition-colors ${
                        p === page
                          ? 'bg-primary-500 text-white shadow-sm'
                          : 'text-warm-600 hover:bg-warm-100'
                      }`}
                      onClick={() => setPage(p)}
                    >
                      {p}
                    </button>
                  ));
                }
                const pills: (number | string)[] = [];
                if (page > 3) { pills.push(1, '…'); }
                for (let p = Math.max(2, page - 1); p <= Math.min(totalPages - 1, page + 1); p++) {
                  pills.push(p);
                }
                if (page < totalPages - 2) { pills.push('…', totalPages); }
                // ensure first/last always present
                if (pills[0] !== 1) pills.unshift(1);
                if (pills[pills.length - 1] !== totalPages) pills.push(totalPages);
                // deduplicate sequential ellipses
                const deduped: (number | string)[] = [];
                for (const item of pills) {
                  if (item === '…' && deduped[deduped.length - 1] === '…') continue;
                  deduped.push(item);
                }
                return deduped.map((p, idx) =>
                  p === '…' ? (
                    <span key={`ellipsis-${idx}`} className="px-1 text-warm-400 select-none">…</span>
                  ) : (
                    <button
                      key={p}
                      className={`inline-flex h-8 w-8 items-center justify-center rounded-lg text-sm font-medium transition-colors ${
                        p === page
                          ? 'bg-primary-500 text-white shadow-sm'
                          : 'text-warm-600 hover:bg-warm-100'
                      }`}
                      onClick={() => setPage(p as number)}
                    >
                      {p}
                    </button>
                  )
                );
              })()}

              <button
                className="btn-ghost px-3 py-1.5 text-sm disabled:opacity-30"
                disabled={page >= data.totalPages}
                onClick={() => setPage((p) => Math.min(data.totalPages, p + 1))}
                title="下一页"
              >
                <span className="material-symbols-outlined text-[16px]">chevron_right</span>
              </button>
              <button
                className="btn-ghost px-3 py-1.5 text-sm disabled:opacity-30"
                disabled={page >= data.totalPages}
                onClick={() => setPage(data.totalPages)}
                title="最后一页"
              >
                <span className="material-symbols-outlined text-[16px]">last_page</span>
              </button>
            </div>
          </div>
        </>
      )}

      {/* ── Detail modal ──────────────────────────────────────────────────── */}
      {detailLog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4 animate-in fade-in duration-150"
          onClick={closeDetail}
        >
          <div
            className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-warm-100 shadow-modal"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal header */}
            <div className="sticky top-0 z-10 flex items-center justify-between border-b border-warm-150 bg-warm-100 px-6 py-4 rounded-t-2xl">
              <div>
                <h3 className="text-lg font-semibold text-warm-900">审计日志详情</h3>
                <p className="text-xs text-warm-500 mt-0.5 font-mono">ID: {detailLog.id}</p>
              </div>
              <button className="btn-ghost p-2 rounded-lg" onClick={closeDetail}>
                <span className="material-symbols-outlined text-[20px]">close</span>
              </button>
            </div>

            {detailLoading ? (
              <div className="flex justify-center py-12">
                <div className="h-6 w-6 animate-spin rounded-full border-[3px] border-warm-300 border-t-primary-500" />
              </div>
            ) : (
              <div className="px-6 py-5 space-y-5">
                {/* Key-value grid */}
                <dl className="grid grid-cols-2 gap-x-6 gap-y-4 text-sm">
                  <div>
                    <dt className="text-xs text-warm-400 mb-1">时间戳</dt>
                    <dd className="text-warm-800 font-mono text-xs">{formatTimestamp(detailLog.timestamp)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-warm-400 mb-1">用户 ID</dt>
                    <dd className="text-warm-800 font-medium">{detailLog.userId || '—'}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-warm-400 mb-1">Agent ID</dt>
                    <dd className="text-warm-700">{detailLog.agentId || '—'}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-warm-400 mb-1">操作</dt>
                    <dd>
                      <code className="text-xs bg-warm-50 rounded px-1.5 py-0.5 text-warm-700 font-mono">
                        {detailLog.action}
                      </code>
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-warm-400 mb-1">风险级别</dt>
                    <dd>
                      <RiskBadge level={detailLog.riskLevel} />
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-warm-400 mb-1">决策</dt>
                    <dd>
                      <DecisionBadge decision={detailLog.decision} />
                    </dd>
                  </div>
                  {detailLog.contentHash && (
                    <div className="col-span-2">
                      <dt className="text-xs text-warm-400 mb-1">内容哈希 (SHA-256)</dt>
                      <dd className="text-warm-600 font-mono text-[11px] break-all">{detailLog.contentHash}</dd>
                    </div>
                  )}
                </dl>

                {/* Payload section */}
                {detailLog.payload && (
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="text-xs font-semibold text-warm-500 uppercase tracking-wider">Payload 数据</h4>
                      <button
                        className="text-xs text-warm-500 hover:text-warm-700 underline underline-offset-2"
                        onClick={() => {
                          void navigator.clipboard.writeText(detailLog.payload || '');
                        }}
                      >
                        复制
                      </button>
                    </div>
                    <JsonHighlight json={detailLog.payload} />
                  </div>
                )}
              </div>
            )}

            {/* Modal footer */}
            <div className="sticky bottom-0 border-t border-warm-150 bg-warm-50 px-6 py-3 rounded-b-2xl flex justify-end">
              <button className="btn-secondary" onClick={closeDetail}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
