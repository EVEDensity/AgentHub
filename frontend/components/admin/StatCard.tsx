import type { JSX } from 'react';

export function StatCard({ label, tokens, sessions, messages }: { label: string; tokens: number; sessions: number; messages: number }): JSX.Element {
  function fmt(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(0)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
    return `${n}`;
  }
  return (
    <div className="flex min-w-0 flex-col justify-center rounded-xl border border-warm-150 bg-white px-4 py-3.5 shadow-sm transition-all hover:shadow-md hover:border-teal-200">
      <div className="text-xs font-medium text-warm-400">{label}</div>
      <div className="mt-1 text-lg font-bold text-warm-900">{fmt(tokens)} tokens</div>
      <div className="mt-0.5 text-[11px] text-warm-400">{messages} 条消息 · {sessions} 个会话</div>
    </div>
  );
}

export function MiniStatBox({ icon, label, value }: { icon: string; label: string; value: string }): JSX.Element {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-warm-150 bg-white px-4 py-3 shadow-sm transition-all hover:shadow-md hover:border-teal-200">
      <span className="text-lg shrink-0">{icon}</span>
      <div className="min-w-0">
        <div className="text-[11px] text-warm-400 leading-tight">{label}</div>
        <div className="text-sm font-semibold text-warm-800 truncate">{value}</div>
      </div>
    </div>
  );
}
