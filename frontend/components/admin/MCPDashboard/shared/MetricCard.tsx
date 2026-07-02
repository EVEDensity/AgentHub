import type { JSX } from 'react';

interface MetricCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon?: string;
  trend?: { value: number; label: string };
  color?: 'green' | 'red' | 'amber' | 'blue' | 'slate';
  loading?: boolean;
  onClick?: () => void;
}

const COLOR_MAP: Record<string, { bg: string; text: string; iconBg: string }> = {
  green: { bg: 'bg-emerald-50 border-emerald-200', text: 'text-emerald-700', iconBg: 'bg-emerald-100 text-emerald-600' },
  red: { bg: 'bg-red-50 border-red-200', text: 'text-red-700', iconBg: 'bg-red-100 text-red-600' },
  amber: { bg: 'bg-amber-50 border-amber-200', text: 'text-amber-700', iconBg: 'bg-amber-100 text-amber-600' },
  blue: { bg: 'bg-blue-50 border-blue-200', text: 'text-blue-700', iconBg: 'bg-blue-100 text-blue-600' },
  slate: { bg: 'bg-slate-50 border-slate-200', text: 'text-slate-700', iconBg: 'bg-slate-100 text-slate-600' },
};

export default function MetricCard({
  title,
  value,
  subtitle,
  icon,
  trend,
  color = 'slate',
  loading = false,
  onClick,
}: MetricCardProps): JSX.Element {
  const c = COLOR_MAP[color] || COLOR_MAP.slate;

  return (
    <div
      className={`rounded-xl border ${c.bg} px-5 py-4 transition-shadow hover:shadow-md ${onClick ? 'cursor-pointer' : ''}`}
      onClick={onClick}
    >
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-wide text-warm-500">{title}</p>
          {loading ? (
            <div className="mt-1 h-8 w-20 animate-pulse rounded bg-warm-200" />
          ) : (
            <p className={`mt-1 text-2xl font-bold ${c.text}`}>{value}</p>
          )}
          {subtitle && <p className="mt-0.5 text-xs text-warm-400">{subtitle}</p>}
          {trend && (
            <p className={`mt-1 text-xs font-medium ${trend.value >= 0 ? 'text-green-600' : 'text-red-500'}`}>
              {trend.value >= 0 ? '↑' : '↓'} {Math.abs(trend.value)}% {trend.label}
            </p>
          )}
        </div>
        {icon && (
          <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${c.iconBg} text-lg`}>
            {icon}
          </span>
        )}
      </div>
    </div>
  );
}
