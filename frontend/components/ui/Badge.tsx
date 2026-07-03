'use client';

import { type JSX, type HTMLAttributes } from 'react';

// ── Types ─────────────────────────────────────────────────────────────

export type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info' | 'primary' | 'outline';
export type BadgeSize = 'xs' | 'sm' | 'md';

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  size?: BadgeSize;
  dot?: boolean;
  removable?: boolean;
  onRemove?: () => void;
}

// ── Style maps ────────────────────────────────────────────────────────

const VARIANT_STYLES: Record<BadgeVariant, string> = {
  default:  'bg-warm-100 text-warm-600',
  success:  'bg-success-50 text-success-600',
  warning:  'bg-warning-50 text-warning-600',
  danger:   'bg-danger-50 text-danger-600',
  info:     'bg-blue-50 text-blue-600',
  primary:  'bg-primary-50 text-primary-600',
  outline:  'bg-transparent text-warm-500 border border-warm-200',
};

const DOT_COLORS: Record<BadgeVariant, string> = {
  default:  'bg-warm-400',
  success:  'bg-success-500',
  warning:  'bg-warning-500',
  danger:   'bg-danger-500',
  info:     'bg-blue-500',
  primary:  'bg-primary-500',
  outline:  'bg-warm-400',
};

const SIZE_STYLES: Record<BadgeSize, string> = {
  xs: 'text-[10px] px-1.5 py-0.5 rounded gap-1',
  sm: 'text-[11px] px-2 py-0.5 rounded-md gap-1',
  md: 'text-xs px-2.5 py-1 rounded-lg gap-1.5',
};

// ── Component ─────────────────────────────────────────────────────────

export function Badge({
  variant = 'default',
  size = 'sm',
  dot = false,
  removable = false,
  onRemove,
  children,
  className = '',
  ...rest
}: BadgeProps): JSX.Element {
  return (
    <span
      className={`inline-flex items-center font-medium ${VARIANT_STYLES[variant]} ${SIZE_STYLES[size]} ${className}`}
      {...rest}
    >
      {dot && (
        <span className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${DOT_COLORS[variant]}`} />
      )}
      {children}
      {removable && (
        <button
          type="button"
          className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full hover:bg-black/10 transition-colors shrink-0"
          onClick={(e) => {
            e.stopPropagation();
            onRemove?.();
          }}
        >
          <svg className="w-2.5 h-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      )}
    </span>
  );
}

export default Badge;
