'use client';

import { type JSX, type ReactNode } from 'react';

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

const SIZE_STYLES = {
  sm: { icon: 'text-2xl', title: 'text-sm', desc: 'text-xs', padding: 'py-8' },
  md: { icon: 'text-3xl', title: 'text-base', desc: 'text-sm', padding: 'py-12' },
  lg: { icon: 'text-4xl', title: 'text-lg', desc: 'text-base', padding: 'py-16' },
};

export function EmptyState({
  icon = '◇',
  title,
  description,
  action,
  size = 'md',
  className = '',
}: EmptyStateProps): JSX.Element {
  const s = SIZE_STYLES[size];

  return (
    <div className={`flex flex-col items-center justify-center text-center ${s.padding} ${className}`}>
      <span className={`${s.icon} text-warm-400 mb-4 block`}>
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
            <span className="text-sm">{action.icon}</span>
          )}
          {action.label}
        </button>
      )}
    </div>
  );
}

export default EmptyState;
