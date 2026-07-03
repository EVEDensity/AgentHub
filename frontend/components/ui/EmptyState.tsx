'use client';

import { type JSX, type ReactNode } from 'react';

// ── Types ─────────────────────────────────────────────────────────────

export interface EmptyStateProps {
  icon?: string;
  title: string;
  description?: string;
  action?: {
    label: string;
    icon?: string;
    onClick: () => void;
  };
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

// ── Size styles ───────────────────────────────────────────────────────

const SIZE_STYLES = {
  sm: { icon: 'text-3xl', title: 'text-sm', desc: 'text-xs', padding: 'py-8' },
  md: { icon: 'text-4xl', title: 'text-base', desc: 'text-sm', padding: 'py-12' },
  lg: { icon: 'text-5xl', title: 'text-lg', desc: 'text-base', padding: 'py-16' },
};

// ── Component ─────────────────────────────────────────────────────────

export function EmptyState({
  icon = 'inbox',
  title,
  description,
  action,
  size = 'md',
  className = '',
}: EmptyStateProps): JSX.Element {
  const s = SIZE_STYLES[size];

  return (
    <div className={`flex flex-col items-center justify-center text-center ${s.padding} ${className}`}>
      <span className={`material-symbols-outlined ${s.icon} text-warm-200 mb-4 block`}>
        {icon}
      </span>
      <h4 className={`${s.title} font-semibold text-warm-500 mb-1`}>{title}</h4>
      {description && (
        <p className={`${s.desc} text-warm-400 max-w-sm`}>{description}</p>
      )}
      {action && (
        <button
          className="btn-primary mt-4 inline-flex items-center gap-1.5 text-sm"
          onClick={action.onClick}
        >
          {action.icon && (
            <span className="material-symbols-outlined text-[14px]">{action.icon}</span>
          )}
          {action.label}
        </button>
      )}
    </div>
  );
}

export default EmptyState;
