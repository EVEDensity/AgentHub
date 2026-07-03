'use client';

import { type JSX } from 'react';

// ── Types ─────────────────────────────────────────────────────────────

export type SkeletonVariant = 'text' | 'circle' | 'rect';

export interface SkeletonProps {
  variant?: SkeletonVariant;
  width?: string | number;
  height?: string | number;
  lines?: number;
  gap?: number; // px between lines
  className?: string;
}

// ── Component ─────────────────────────────────────────────────────────

/**
 * Skeleton loading placeholder.
 *
 * Usage:
 *   <Skeleton variant="text" lines={3} />          // 3 lines of text
 *   <Skeleton variant="circle" width={40} />        // avatar placeholder
 *   <Skeleton variant="rect" width="100%" height={200} /> // card placeholder
 */
export function Skeleton({
  variant = 'text',
  width,
  height,
  lines = 1,
  gap = 12,
  className = '',
}: SkeletonProps): JSX.Element {
  const base = 'animate-pulse bg-gradient-to-r from-warm-100 via-warm-50 to-warm-100 bg-[length:200%_100%] animate-shimmer';

  if (variant === 'text' && lines > 1) {
    const lineWidths = Array.from({ length: lines }, (_, i) =>
      i === lines - 1 ? '70%' : '100%'
    );
    return (
      <div className={`space-y-0 ${className}`} style={{ gap: `${gap}px` }}>
        {lineWidths.map((w, i) => (
          <div
            key={i}
            className={`${base} rounded`}
            style={{
              width: width || w,
              height: height || '12px',
            }}
          />
        ))}
      </div>
    );
  }

  const variantStyle = variant === 'circle' ? 'rounded-full' : 'rounded';

  return (
    <div
      className={`${base} ${variantStyle} ${className}`}
      style={{
        width: width || (variant === 'circle' ? '40px' : '100%'),
        height: height || (variant === 'circle' ? '40px' : variant === 'rect' ? '120px' : '14px'),
      }}
    />
  );
}

export default Skeleton;
