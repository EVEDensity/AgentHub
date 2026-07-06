'use client';

import { type JSX, type HTMLAttributes, type ReactNode } from 'react';

export type CardPadding = 'none' | 'sm' | 'md' | 'lg';

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  padding?: CardPadding;
  hover?: boolean;
  bordered?: boolean;
  header?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}

const PADDING_STYLES: Record<CardPadding, string> = {
  none: 'p-0',
  sm: 'p-3',
  md: 'p-4',
  lg: 'p-6',
};

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
  const border = bordered ? 'border border-warm-200' : '';
  const hoverEffect = hover ? 'hover:bg-warm-150 cursor-pointer' : '';

  return (
    <div className={`bg-warm-100 ${border} ${hoverEffect} ${className}`} {...rest}>
      {header && (
        <div className="px-4 py-3 border-b border-warm-200 bg-warm-100">
          {typeof header === 'string' ? (
            <h3 className="text-sm font-semibold text-warm-800">{header}</h3>
          ) : (
            header
          )}
        </div>
      )}
      <div className={PADDING_STYLES[padding]}>{children}</div>
      {footer && (
        <div className="px-4 py-3 border-t border-warm-200 bg-warm-100">
          {footer}
        </div>
      )}
    </div>
  );
}

export default Card;
