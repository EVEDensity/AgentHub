import { useEffect, useMemo, useRef, useState, type JSX } from 'react';

interface HeatmapDay {
  date: string;
  sessions: number;
  messages: number;
  tokens: number;
}

interface HeatmapData {
  range: { start: string; end: string };
  today: { sessions: number; messages: number; tokens: number };
  yesterday: { sessions: number; messages: number; tokens: number };
  last30: { sessions: number; messages: number; tokens: number };
  days: HeatmapDay[];
  generatedAt: string;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(0)}M tokens`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K tokens`;
  return `${n} tokens`;
}

function formatDateLabel(dateStr: string): string {
  return dateStr.replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$1年$2月$3日');
}

function buildWeeks(days: HeatmapDay[]): (HeatmapDay | null)[][] {
  if (!days.length) return [];
  const firstDate = new Date(days[0].date);
  const dayOfWeek = (firstDate.getDay() + 6) % 7;
  const padded: (HeatmapDay | null)[] = Array(dayOfWeek).fill(null).concat(days);
  const weeks: (HeatmapDay | null)[][] = [];
  for (let i = 0; i < padded.length; i += 7) {
    weeks.push(padded.slice(i, i + 7));
  }
  return weeks;
}

function getMonthLabels(weeks: (HeatmapDay | null)[][]): { colIndex: number; label: string }[] {
  const labels: { colIndex: number; label: string }[] = [];
  const seen = new Set<string>();
  weeks.forEach((week, wi) => {
    for (const day of week) {
      if (!day) continue;
      const date = new Date(day.date);
      const key = `${date.getFullYear()}-${date.getMonth()}`;
      if (!seen.has(key)) {
        seen.add(key);
        labels.push({ colIndex: wi, label: `${date.getMonth() + 1}月` });
      }
    }
  });
  return labels;
}

function getHeatLevel(tokens: number, maxTokens: number): number {
  if (tokens === 0 || maxTokens === 0) return 0;
  const ratio = tokens / maxTokens;
  if (ratio <= 0.15) return 1;
  if (ratio <= 0.35) return 2;
  if (ratio <= 0.55) return 3;
  if (ratio <= 0.75) return 4;
  return 5;
}

const HEAT_COLORS = [
  'bg-[#F0EFEA]',
  'bg-[#F5E6DE]',
  'bg-[#EBC4B0]',
  'bg-[#D99A7A]',
  'bg-[#C0704A]',
  'bg-[#8B4A2A]',
];

const WEEKDAY_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const DISPLAY_WEEKDAY_INDEX = new Set([0, 2, 4, 6]);

export default function TokenUsageHeatmap(): JSX.Element {
  const [data, setData] = useState<HeatmapData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tooltip, setTooltip] = useState<{ x: number; y: number; day: HeatmapDay } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const token = localStorage.getItem('agenthub_token');
      const res = await fetch('/api/admin/token-usage-heatmap', {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.status === 401 || res.status === 403) {
        setError('登录已过期，请重新登录');
        setData(null);
        return;
      }
      if (!res.ok) {
        setError(`加载失败 (${res.status})`);
        setData(null);
        return;
      }
      setData((await res.json()) as HeatmapData);
    } catch (err) {
      setError('网络错误，请检查后端服务是否运行');
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), 30000);
    return () => clearInterval(id);
  }, []);

  const weeks = useMemo(() => (data ? buildWeeks(data.days) : []), [data]);
  const monthLabels = useMemo(() => getMonthLabels(weeks), [weeks]);
  const maxTokens = useMemo(() => {
    if (!data?.days.length) return 0;
    return Math.max(...data.days.map((d) => d.tokens));
  }, [data]);

  const rangeLabel = useMemo(() => {
    if (!data) return '';
    const start = data.range.start.slice(0, 7).replace('-', '.');
    const end = data.range.end.slice(0, 7).replace('-', '.');
    return `${start} - ${end}`;
  }, [data]);

  function handleCellEnter(e: React.MouseEvent, day: HeatmapDay) {
    setTooltip({ x: e.clientX, y: e.clientY, day });
  }

  function handleCellMove(e: React.MouseEvent) {
    setTooltip((prev) => (prev ? { ...prev, x: e.clientX, y: e.clientY } : null));
  }

  function handleCellLeave() {
    setTooltip(null);
  }

  if (loading && !data) {
    return (
      <div className="space-y-6">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h2 className="text-[22px] font-semibold tracking-tight text-warm-900">Token 用量</h2>
            <p className="mt-1 text-sm text-warm-500">加载中...</p>
          </div>
        </div>
        <div className="rounded-2xl border border-[#E7DECF] bg-[#F7F2E8] p-5">
          <div className="text-sm text-warm-500">正在加载数据...</div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-6">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h2 className="text-[22px] font-semibold tracking-tight text-warm-900">Token 用量</h2>
            <p className="mt-1 text-sm text-warm-500">基于本机 AgentHub 会话记录统计</p>
          </div>
        </div>
        <div className="rounded-2xl border border-red-200 bg-red-50 p-5">
          <div className="text-sm text-red-600">{error}</div>
          <button className="btn-secondary mt-3" onClick={() => void load()}>重试</button>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="space-y-6">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h2 className="text-[22px] font-semibold tracking-tight text-warm-900">Token 用量</h2>
            <p className="mt-1 text-sm text-warm-500">基于本机 AgentHub 会话记录统计</p>
          </div>
        </div>
        <div className="rounded-2xl border border-[#E7DECF] bg-[#F7F2E8] p-5">
          <div className="text-sm text-warm-500">暂无数据</div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header + Stats */}
      <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h2 className="text-[22px] font-semibold tracking-tight text-warm-900">Token 用量</h2>
          <p className="mt-1 text-sm text-warm-500">{rangeLabel}</p>
          <p className="text-xs text-warm-400">基于本机 AgentHub 会话记录统计</p>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <StatCard label="今天" tokens={data.today.tokens} sessions={data.today.sessions} messages={data.today.messages} />
          <StatCard label="昨天" tokens={data.yesterday.tokens} sessions={data.yesterday.sessions} messages={data.yesterday.messages} />
          <StatCard label="30 天" tokens={data.last30.tokens} sessions={data.last30.sessions} messages={data.last30.messages} />
        </div>
      </div>

      {/* Heatmap */}
      <div ref={containerRef} className="rounded-2xl border border-[#E7DECF] bg-[#F7F2E8] p-5">
        {/* Month labels */}
        <div className="mb-1 flex gap-[3px] pl-10">
          {weeks.map((_, wi) => {
            const label = monthLabels.find((m) => m.colIndex === wi)?.label;
            return (
              <div key={wi} className="w-3 flex-shrink-0 overflow-visible text-[10px] text-warm-500">
                {label || ''}
              </div>
            );
          })}
        </div>

        <div className="flex">
          {/* Weekday labels */}
          <div className="mr-2 flex flex-col gap-[3px]">
            {WEEKDAY_LABELS.map((wd, i) => (
              <div key={wd} className="flex h-3 items-center text-[10px] leading-3 text-warm-400">
                {DISPLAY_WEEKDAY_INDEX.has(i) ? wd : ''}
              </div>
            ))}
          </div>

          {/* Grid */}
          <div className="flex gap-[3px] overflow-x-auto pb-1">
            {weeks.map((week, wi) => (
              <div key={wi} className="flex flex-col gap-[3px]">
                {week.map((day, di) => {
                  if (!day) {
                    return <div key={di} className="h-3 w-3 rounded-sm bg-transparent" />;
                  }
                  const level = getHeatLevel(day.tokens, maxTokens);
                  return (
                    <div
                      key={day.date}
                      className={`h-3 w-3 rounded-sm ${HEAT_COLORS[level]} cursor-pointer transition-all hover:ring-1 hover:ring-warm-400`}
                      onMouseEnter={(e) => handleCellEnter(e, day)}
                      onMouseMove={handleCellMove}
                      onMouseLeave={handleCellLeave}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </div>

        {/* Legend */}
        <div className="mt-4 flex items-center justify-end gap-2">
          <span className="text-xs text-warm-400">少</span>
          {HEAT_COLORS.map((c, i) => (
            <div key={i} className={`h-3 w-3 rounded-sm ${c}`} />
          ))}
          <span className="text-xs text-warm-400">多</span>
        </div>
      </div>

      {/* Tooltip */}
      {tooltip && (
        <div
          className="fixed z-50 rounded-lg bg-[#3F342A] px-3 py-2 text-xs text-white"
          style={{
            left: tooltip.x + 12,
            top: tooltip.y + 12,
            pointerEvents: 'none',
          }}
        >
          <div className="font-medium">{formatDateLabel(tooltip.day.date)}</div>
          <div className="mt-0.5 text-[#EBC4B0]">
            {tooltip.day.messages} 条消息 · {tooltip.day.sessions} 个会话 · {formatTokens(tooltip.day.tokens)}
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, tokens, sessions, messages }: { label: string; tokens: number; sessions: number; messages: number }): JSX.Element {
  return (
    <div className="flex min-w-[170px] flex-col justify-center rounded-2xl border border-[#E7DECF] bg-[#F7F2E8] px-5 py-4">
      <div className="text-xs text-warm-500">{label}</div>
      <div className="mt-1 text-xl font-semibold text-warm-900">{formatTokens(tokens)}</div>
      <div className="mt-0.5 text-xs text-warm-400">{messages} 条消息 · {sessions} 个会话</div>
    </div>
  );
}
