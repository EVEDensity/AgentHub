'use client';

import { useState, useCallback } from 'react';

const LEVEL_COLORS: Record<string, string> = {
  debug: 'text-gray-400 bg-gray-50 border-gray-200',
  info: 'text-blue-600 bg-blue-50 border-blue-200',
  warn: 'text-amber-600 bg-amber-50 border-amber-200',
  warning: 'text-amber-600 bg-amber-50 border-amber-200',
  error: 'text-red-600 bg-red-50 border-red-200',
};

const LEVEL_BG: Record<string, string> = {
  debug: '#f9fafb',
  info: '#eff6ff',
  warn: '#fffbeb',
  warning: '#fffbeb',
  error: '#fef2f2',
};

export default function LogsViewer({
  authHeaders,
  setNotice,
}: {
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
}): JSX.Element {
  const [service, setService] = useState('');
  const [level, setLevel] = useState('');
  const [query, setQuery] = useState('');
  const [limit] = useState(100);
  const [logs, setLogs] = useState<Array<Record<string, unknown>>>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [selectedLog, setSelectedLog] = useState<Record<string, unknown> | null>(null);

  const headers = authHeaders();

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (service) params.set('service', service);
      if (level) params.set('level', level);
      if (query) params.set('query', query);
      params.set('limit', String(limit));

      const res = await fetch(`/logs/query?${params.toString()}`, { headers });
      if (res.ok) {
        const data = await res.json();
        setLogs((data.logs as Array<Record<string, unknown>>) || []);
        setTotal((data.total as number) || 0);
      }
    } catch { setNotice('日志查询失败'); }
    setLoading(false);
  }, [service, level, query, limit, headers, setNotice]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    fetchLogs();
  };

  // Auto-refresh every 5s
  useState(() => {
    if (autoRefresh) {
      const interval = setInterval(fetchLogs, 5000);
      return () => clearInterval(interval);
    }
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-h4">📋 集中日志</h3>
          <p className="text-xs text-gray-400 mt-1">Loki 日志聚合 · 按 service / level / trace_id 检索</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-gray-500 cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="rounded"
            />
            自动刷新 (5s)
          </label>
          <span className="text-xs text-gray-400">
            {total > 0 ? `共 ${total} 条，显示 ${logs.length} 条` : ''}
          </span>
        </div>
      </div>

      {/* Search bar */}
      <form onSubmit={handleSubmit} className="card p-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div>
            <label className="block text-[10px] text-gray-400 mb-1 uppercase">服务名</label>
            <input
              type="text"
              value={service}
              onChange={(e) => setService(e.target.value)}
              placeholder="gateway-service"
              className="input-field text-sm w-full font-mono"
            />
          </div>
          <div>
            <label className="block text-[10px] text-gray-400 mb-1 uppercase">日志级别</label>
            <select value={level} onChange={(e) => setLevel(e.target.value)} className="input-field text-sm w-full">
              <option value="">全部</option>
              <option value="debug">DEBUG</option>
              <option value="info">INFO</option>
              <option value="warn">WARN</option>
              <option value="error">ERROR</option>
            </select>
          </div>
          <div>
            <label className="block text-[10px] text-gray-400 mb-1 uppercase">关键词 / Trace ID</label>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="trace_id=xxx 或关键词"
              className="input-field text-sm w-full font-mono"
            />
          </div>
          <div className="flex items-end">
            <button type="submit" className="btn-primary text-sm w-full" disabled={loading}>
              {loading ? '⏳ 查询中...' : '🔍 搜索'}
            </button>
          </div>
        </div>
      </form>

      {/* Log entries */}
      <div className="space-y-1 font-mono text-xs">
        {logs.map((entry, i) => {
          const lvl = (entry.level as string) || 'info';
          const svc = (entry.service as string) || '-';
          const msg = (entry.message as string) || '';
          const ts = (entry.timestamp as string) || '';
          const tid = (entry.trace_id as string) || '';
          const fields = (entry.fields as Record<string, unknown>) || {};

          return (
            <div
              key={i}
              className="border rounded px-3 py-2 cursor-pointer hover:shadow-sm transition-shadow flex items-start gap-3"
              style={{ backgroundColor: LEVEL_BG[lvl] || '#fff' }}
              onClick={() => setSelectedLog(selectedLog === entry ? null : entry)}
            >
              {/* Level badge */}
              <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium shrink-0 mt-0.5 ${LEVEL_COLORS[lvl] || ''}`}>
                {lvl.toUpperCase()}
              </span>

              {/* Timestamp */}
              <span className="text-gray-400 shrink-0 w-[140px] text-[11px]">
                {ts ? new Date(ts).toLocaleTimeString() + '.' + (new Date(ts).getMilliseconds().toString().padStart(3, '0')) : '-'}
              </span>

              {/* Service */}
              <span className="font-semibold text-gray-500 shrink-0 w-[140px] truncate">
                {svc}
              </span>

              {/* Trace ID */}
              {tid && (
                <span className="text-purple-500 shrink-0 text-[10px] font-mono truncate w-[120px]" title={tid}>
                  {tid.slice(0, 12)}...
                </span>
              )}

              {/* Message */}
              <span className="text-gray-700 flex-1 truncate">{msg}</span>

              {/* Expand indicator */}
              {Object.keys(fields).length > 0 && (
                <span className="text-gray-300 shrink-0">{selectedLog === entry ? '▲' : '▼'}</span>
              )}
            </div>
          );
        })}

        {logs.length === 0 && !loading && (
          <div className="text-center py-16 text-gray-400">
            <p className="text-5xl mb-4">📋</p>
            <p className="text-lg mb-1">暂无日志</p>
            <p className="text-sm">
              {query || service || level
                ? '没有匹配当前过滤条件的日志'
                : '点击"搜索"加载日志，或配置 Loki + Promtail 开始采集'}
            </p>
          </div>
        )}

        {loading && (
          <div className="flex justify-center py-8">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
          </div>
        )}
      </div>

      {/* Expanded log detail */}
      {selectedLog && (
        <div className="card p-4 bg-gray-50">
          <h4 className="text-xs font-semibold text-gray-500 mb-3 uppercase">日志详情</h4>
          <div className="grid grid-cols-2 gap-3 text-xs">
            {Object.entries(selectedLog).map(([key, value]) => (
              <div key={key} className="flex gap-2">
                <span className="font-semibold text-gray-500 shrink-0">{key}:</span>
                <span className="text-gray-700 break-all font-mono">
                  {typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
