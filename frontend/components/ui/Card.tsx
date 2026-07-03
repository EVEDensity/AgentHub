'use client';

import { type JSX, type HTMLAttributes, type ReactNode } from 'react';

// ── Types ─────────────────────────────────────────────────────────────

export type CardPadding = 'none' | 'sm' | 'md' | 'lg';

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  padding?: CardPadding;
  hover?: boolean;
  bordered?: boolean;
  header?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}

// ── Style maps ────────────────────────────────────────────────────────

const PADDING_STYLES: Record<CardPadding, string> = {
  none: 'p-0',
  sm: 'p-3',
  md: 'p-4',
  lg: 'p-6',
};

// ── Component ─────────────────────────────────────────────────────────

export function Card({
  padding = 'md',
  hover = false,
  bordered = true,
  header,
  footer,
  children,
  className = '',
  ...rest
}: CardProps): JSX.Element {
  const base = 'bg-white rounded-xl overflow-hidden';
  const border = bordered ? 'border border-warm-150' : '';
  const hoverEffect = hover ? 'hover:shadow-card-elevated hover:border-warm-200 transition-shadow duration-200 cursor-pointer' : 'shadow-sm';

  return (
    <div className={`${base} ${border} ${hoverEffect} ${className}`} {...rest}>
      {header && (
        <div className="px-4 py-3 border-b border-warm-100 bg-warm-50/50 rounded-t-xl">
          {typeof header === 'string' ? (
            <h3 className="text-sm font-semibold text-warm-700">{header}</h3>
          ) : (
            header
          )}
        </div>
      )}
      <div className={PADDING_STYLES[padding]}>{children}</div>
      {footer && (
        <div className="px-4 py-3 border-t border-warm-100 bg-warm-50/30 rounded-b-xl">
          {footer}
        </div>
      )}
    </div>
  );
}

export default Card;
