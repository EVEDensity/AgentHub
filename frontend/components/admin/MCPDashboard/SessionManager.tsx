import { useEffect, useState, type JSX } from 'react';
import StatusBadge from './shared/StatusBadge';

interface SessionItem {
  id: string;
  name: string;
  type: string;
  participants: string[];
  active: number;
  isPinned: number;
  lastMessageAt: string;
  createdAt: string;
  ownerId: string;
  visibility: string;
  memberCount: number;
}

interface SessionDetail extends SessionItem {
  members: Array<{ userId: string; role: string; joinedAt: string; userName: string }>;
  messageCount: number;
  activeConnections: number;
}

function authHeaders(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  const token = localStorage.getItem('agenthub_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export default function SessionManager(): JSX.Element {
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(0);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [actionMsg, setActionMsg] = useState('');

  // Cleanup form state
  const [cleanupDays, setCleanupDays] = useState(30);
  const [cleanupOnlyInactive, setCleanupOnlyInactive] = useState(true);
  const [cleanupDryRun, setCleanupDryRun] = useState(true);
  const [cleanupResult, setCleanupResult] = useState<{ matchedSessions: number } | null>(null);
  const [cleanupLoading, setCleanupLoading] = useState(false);

  async function fetchSessions(): Promise<void> {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams({ page: String(page), pageSize: '30' });
      if (search) params.set('search', search);
      const res = await fetch(`/api/admin/mcp/sessions?${params.toString()}`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setSessions(data.items || []);
      setTotalPages(data.totalPages || 0);
      setTotal(data.total || 0);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed');
    } finally {
      setLoading(false);
    }
  }

  async function fetchDetail(sid: string): Promise<void> {
    setDetailLoading(true);
    try {
      const res = await fetch(`/api/admin/mcp/sessions/${encodeURIComponent(sid)}`, { headers: authHeaders() });
      if (res.ok) setDetail(await res.json() as SessionDetail);
    } catch { /* ignore */ }
    finally { setDetailLoading(false); }
  }

  async function closeSession(sid: string): Promise<void> {
    if (!confirm(`确定强制关闭会话 ${sid}？所有成员将被断开连接。`)) return;
    try {
      const res = await fetch(`/api/admin/mcp/sessions/${encodeURIComponent(sid)}`, {
        method: 'DELETE',
        headers: authHeaders(),
      });
      if (res.ok) {
        setActionMsg(`已关闭 ${sid}`);
        void fetchSessions();
        setSelectedId(null);
        setDetail(null);
      }
    } catch (e) {
      setActionMsg(`Failed: ${e instanceof Error ? e.message : 'error'}`);
    }
  }

  async function runCleanup(): Promise<void> {
    if (!cleanupDryRun && !confirm(`确认永久删除超过 ${cleanupDays} 天的${cleanupOnlyInactive ? '非活跃' : '所有'}会话？`)) return;
    setCleanupLoading(true);
    try {
      const res = await fetch('/api/admin/mcp/sessions/cleanup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          olderThanDays: cleanupDays,
          onlyInactive: cleanupOnlyInactive,
          dryRun: cleanupDryRun,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setCleanupResult(data);
        if (!cleanupDryRun) { void fetchSessions(); }
      }
    } catch (e) {
      setActionMsg(`Cleanup failed: ${e instanceof Error ? e.message : 'error'}`);
    } finally {
      setCleanupLoading(false);
    }
  }

  useEffect(() => { void fetchSessions(); }, [page, search]);

  return (
    <div className="space-y-4">
      {/* Search + cleanup */}
      <div className="flex items-center gap-3 flex-wrap">
        <input
          className="rounded-lg border border-warm-200 bg-warm-100 px-3 py-1.5 text-sm outline-none focus:border-primary-300 w-64"
          placeholder="搜索会话 ID 或名称..."
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
        />
        <button className="btn-secondary text-sm px-3 py-1.5" onClick={() => void fetchSessions()}>刷新</button>
        {actionMsg && <span className="text-xs text-primary-600">{actionMsg}</span>}
        <span className="text-xs text-warm-400 ml-auto">共 {total} 个会话</span>
      </div>

      {error && <div className="text-sm text-red-500">{error}</div>}

      {/* Session list + detail split */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_380px] gap-4">
        {/* Left: Session list */}
        <div className="space-y-2">
          {loading ? (
            <div className="text-sm text-warm-400 py-8 text-center">加载中...</div>
          ) : sessions.length === 0 ? (
            <div className="text-sm text-warm-400 py-8 text-center">无匹配会话</div>
          ) : (
            sessions.map((s) => (
              <div
                key={s.id}
                className={`rounded-xl border bg-warm-100 px-4 py-3 cursor-pointer transition-colors ${
                  selectedId === s.id ? 'border-primary-400 ring-1 ring-primary-200' : 'border-warm-200 hover:border-warm-300'
                }`}
                onClick={() => { setSelectedId(s.id); void fetchDetail(s.id); }}
              >
                <div className="flex items-center justify-between">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-warm-600 truncate">{s.id}</span>
                      <StatusBadge
                        variant={s.active ? 'online' : 'offline'}
                        size="sm"
                        label={s.active ? '活跃' : '关闭'}
                      />
                      {s.visibility === 'public' && (
                        <span className="text-[10px] bg-blue-100 text-blue-600 rounded px-1.5">公开</span>
                      )}
                    </div>
                    <div className="text-xs text-warm-500 mt-0.5">
                      {s.name} · {s.memberCount} 成员 · {s.lastMessageAt ? new Date(s.lastMessageAt).toLocaleString() : '无消息'}
                    </div>
                  </div>
                  <button
                    className="shrink-0 rounded bg-red-100 px-2 py-1 text-[11px] text-red-600 hover:bg-red-200"
                    onClick={(e) => { e.stopPropagation(); void closeSession(s.id); }}
                  >
                    关闭
                  </button>
                </div>
              </div>
            ))
          )}
          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 pt-2">
              <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="btn-ghost text-xs px-3 py-1">上一页</button>
              <span className="text-xs text-warm-500">{page} / {totalPages}</span>
              <button disabled={page >= totalPages} onClick={() => setPage(page + 1)} className="btn-ghost text-xs px-3 py-1">下一页</button>
            </div>
          )}
        </div>

        {/* Right: Session detail or cleanup tool */}
        <div className="space-y-4">
          {/* Session detail */}
          <div className="rounded-xl border border-warm-200 bg-warm-100 p-4 min-h-48">
            {!selectedId ? (
              <div className="flex flex-col items-center justify-center h-full text-center py-8">
                <span className="text-3xl mb-2">[left]</span>
                <p className="text-sm text-warm-400">选择会话查看详情</p>
              </div>
            ) : detailLoading ? (
              <div className="text-sm text-warm-400 py-8 text-center">加载中...</div>
            ) : detail ? (
              <div className="space-y-3">
                <h3 className="text-sm font-semibold text-warm-800 font-mono">{detail.id}</h3>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded bg-warm-50 px-2 py-1">
                    <span className="text-warm-400">名称</span>
                    <p className="text-warm-700">{detail.name}</p>
                  </div>
                  <div className="rounded bg-warm-50 px-2 py-1">
                    <span className="text-warm-400">消息数</span>
                    <p className="text-warm-700">{detail.messageCount}</p>
                  </div>
                  <div className="rounded bg-warm-50 px-2 py-1">
                    <span className="text-warm-400">活跃连接</span>
                    <p className="text-warm-700">{detail.activeConnections}</p>
                  </div>
                  <div className="rounded bg-warm-50 px-2 py-1">
                    <span className="text-warm-400">可见性</span>
                    <p className="text-warm-700">{detail.visibility}</p>
                  </div>
                </div>
                {detail.members.length > 0 && (
                  <div>
                    <p className="text-xs text-warm-500 mb-1">成员 ({detail.members.length})</p>
                    {detail.members.map((m) => (
                      <div key={m.userId} className="flex items-center gap-2 text-xs py-0.5">
                        <span className="text-warm-600">{m.userName || m.userId}</span>
                        <span className="text-warm-400">{m.role}</span>
                      </div>
                    ))}
                  </div>
                )}
                <button
                  className="w-full rounded bg-red-100 px-3 py-2 text-xs text-red-600 hover:bg-red-200"
                  onClick={() => void closeSession(detail.id)}
                >
                  强制关闭此会话
                </button>
              </div>
            ) : (
              <div className="text-sm text-warm-400 py-8 text-center">无法加载详情</div>
            )}
          </div>

          {/* Cleanup tool */}
          <div className="rounded-xl border border-amber-200 bg-amber-50/50 p-4">
            <h3 className="text-sm font-semibold text-amber-800 mb-3">[trash] 批量清理</h3>
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <label className="text-xs text-warm-600 w-24">超过天数</label>
                <input
                  type="number"
                  className="w-20 rounded border border-warm-200 px-2 py-1 text-xs"
                  value={cleanupDays}
                  onChange={(e) => setCleanupDays(Number(e.target.value))}
                  min={1}
                />
              </div>
              <label className="flex items-center gap-2 text-xs text-warm-600 cursor-pointer">
                <input type="checkbox" checked={cleanupOnlyInactive} onChange={(e) => setCleanupOnlyInactive(e.target.checked)} />
                仅清理非活跃会话
              </label>
              <label className="flex items-center gap-2 text-xs text-warm-600 cursor-pointer">
                <input type="checkbox" checked={cleanupDryRun} onChange={(e) => setCleanupDryRun(e.target.checked)} />
                仅分析（dry run）
              </label>
              <button
                className="w-full rounded bg-amber-200 px-3 py-2 text-xs text-amber-700 hover:bg-amber-300 disabled:opacity-50"
                disabled={cleanupLoading}
                onClick={() => void runCleanup()}
              >
                {cleanupLoading ? '处理中...' : cleanupDryRun ? '分析匹配会话' : '确认清理'}
              </button>
              {cleanupResult && (
                <div className="text-xs text-amber-700 mt-2">
                  匹配 {cleanupResult.matchedSessions} 个会话
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
