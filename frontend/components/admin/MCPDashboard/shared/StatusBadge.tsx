import type { JSX } from 'react';

type BadgeVariant = 'online' | 'offline' | 'sleeping' | 'warning' | 'critical' | 'info' | 'success' | 'pending' | 'running';

interface StatusBadgeProps {
  variant: BadgeVariant;
  label?: string;
  size?: 'sm' | 'md';
  pulse?: boolean;
}

const VARIANT_MAP: Record<BadgeVariant, { dot: string; text: string; bg: string }> = {
  online: { dot: 'bg-success-500', text: 'text-success-700', bg: 'bg-success-100' },
  offline: { dot: 'bg-danger-500', text: 'text-danger-700', bg: 'bg-danger-100' },
  sleeping: { dot: 'bg-warning-400', text: 'text-warning-700', bg: 'bg-warning-100' },
  warning: { dot: 'bg-warning-500', text: 'text-warning-700', bg: 'bg-warning-100' },
  critical: { dot: 'bg-danger-600', text: 'text-danger-800', bg: 'bg-danger-100' },
  info: { dot: 'bg-primary-500', text: 'text-primary-700', bg: 'bg-primary-100' },
  success: { dot: 'bg-success-500', text: 'text-success-700', bg: 'bg-success-100' },
  pending: { dot: 'bg-warm-400', text: 'text-warm-600', bg: 'bg-warm-100' },
  running: { dot: 'bg-primary-400', text: 'text-primary-700', bg: 'bg-primary-100' },
};

const LABEL_MAP: Record<BadgeVariant, string> = {
  online: '在线', offline: '离线', sleeping: '休眠',
  warning: '警告', critical: '严重', info: '信息',
  success: '成功', pending: '等待中', running: '运行中',
};

export default function StatusBadge({
  variant,
  label,
  size = 'md',
  pulse = false,
}: StatusBadgeProps): JSX.Element {
  const v = VARIANT_MAP[variant];
  const displayLabel = label || LABEL_MAP[variant];
  const sizeCls = size === 'sm' ? 'text-[10px] px-2 py-0' : 'text-xs px-2.5 py-0.5';

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full ${v.bg} ${sizeCls} font-medium ${v.text}`}>
      <span className={`relative flex h-2 w-2`}>
        <span className={`absolute inline-flex h-full w-full rounded-full ${v.dot}`} />
        {pulse && variant === 'online' && (
          <span className={`absolute inline-flex h-full w-full animate-ping rounded-full ${v.dot} opacity-75`} />
        )}
      </span>
      {displayLabel}
    </span>
  );
}
