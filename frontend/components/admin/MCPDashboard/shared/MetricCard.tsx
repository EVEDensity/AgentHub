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
  green: { bg: 'bg-success-50 border-success-200', text: 'text-success-700', iconBg: 'bg-success-100 text-success-600' },
  red: { bg: 'bg-danger-50 border-danger-200', text: 'text-danger-700', iconBg: 'bg-danger-100 text-danger-600' },
  amber: { bg: 'bg-warning-50 border-warning-200', text: 'text-warning-700', iconBg: 'bg-warning-100 text-warning-600' },
  blue: { bg: 'bg-primary-50 border-primary-200', text: 'text-primary-700', iconBg: 'bg-primary-100 text-primary-600' },
  slate: { bg: 'bg-warm-50 border-warm-200', text: 'text-warm-700', iconBg: 'bg-warm-100 text-warm-600' },
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
            <p className={`mt-1 text-xs font-medium ${trend.value >= 0 ? 'text-success-600' : 'text-danger-500'}`}>
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
