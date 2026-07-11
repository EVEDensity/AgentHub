'use client';

import { useRef, useEffect, useCallback, type JSX, type ReactNode } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';

// ── VirtualList — Row-level virtual scrolling for admin data tables ──────
//
// Wraps @tanstack/react-virtual with sensible defaults for admin list views.
// Uses variable-size estimation by default (items can have different heights)
// and overscan=5 for smooth scrolling without excessive DOM nodes.

interface VirtualListProps<T> {
  /** The items to render. */
  items: T[];
  /** Render function for each item. Receives item, index, and virtual row. */
  renderItem: (item: T, index: number) => ReactNode;
  /** Estimated row height in px (used for initial scrollbar sizing). Default 48. */
  estimateSize?: number;
  /** Number of items to render outside the visible area. Default 5. */
  overscan?: number;
  /** Container height. Default '100%' (fills parent). */
  height?: string | number;
  /** Optional header row rendered above the virtual list. */
  header?: ReactNode;
  /** Optional footer rendered below the virtual list. */
  footer?: ReactNode;
  /** Extra className for the scroll container. */
  className?: string;
  /** Called when visible range changes. */
  onVisibleRangeChange?: (start: number, end: number) => void;
}

export default function VirtualList<T>({
  items,
  renderItem,
  estimateSize = 48,
  overscan = 5,
  height = '100%',
  header,
  footer,
  className = '',
  onVisibleRangeChange,
}: VirtualListProps<T>): JSX.Element {
  const parentRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: useCallback(() => estimateSize, [estimateSize]),
    overscan,
  });

  const visibleRange = virtualizer.range;
  useEffect(() => {
    if (onVisibleRangeChange && visibleRange) {
      onVisibleRangeChange(visibleRange.startIndex, visibleRange.endIndex);
    }
  }, [visibleRange?.startIndex, visibleRange?.endIndex, onVisibleRangeChange]);

  return (
    <div
      className={`virtual-list-container ${className}`}
      style={{
        height: typeof height === 'number' ? `${height}px` : height,
        overflow: 'auto',
        contain: 'strict',
      }}
    >
      {header && (
        <div className="virtual-list-header" style={{ position: 'sticky', top: 0, zIndex: 2 }}>
          {header}
        </div>
      )}

      <div
        ref={parentRef}
        className="virtual-list-scroll"
        style={{
          height: typeof height === 'number' ? `${height}px` : height,
          overflow: 'auto',
        }}
      >
        <div
          style={{
            height: `${virtualizer.getTotalSize()}px`,
            width: '100%',
            position: 'relative',
          }}
        >
          {virtualizer.getVirtualItems().map((virtualRow) => (
            <div
              key={virtualRow.key}
              data-index={virtualRow.index}
              ref={virtualizer.measureElement}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                transform: `translateY(${virtualRow.start}px)`,
              }}
            >
              {renderItem(items[virtualRow.index], virtualRow.index)}
            </div>
          ))}
        </div>
      </div>

      {footer && (
        <div className="virtual-list-footer" style={{ position: 'sticky', bottom: 0, zIndex: 2 }}>
          {footer}
        </div>
      )}

      {/* Empty state */}
      {items.length === 0 && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            height: '200px',
            color: '#999',
            fontSize: '14px',
          }}
        >
          暂无数据
        </div>
      )}
    </div>
  );
}

// ── FlatList — simpler fixed-height virtual list for homogeneous rows ────

interface FlatListProps<T> {
  items: T[];
  renderItem: (item: T, index: number) => ReactNode;
  rowHeight?: number;
  overscan?: number;
  height?: string | number;
  className?: string;
}

export function FlatList<T>({
  items,
  renderItem,
  rowHeight = 40,
  overscan = 10,
  height = '400px',
  className = '',
}: FlatListProps<T>): JSX.Element {
  const parentRef = useRef<HTMLDivElement>(null);

  const rowVirtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: useCallback(() => rowHeight, [rowHeight]),
    overscan,
  });

  return (
    <div
      ref={parentRef}
      className={`flat-list ${className}`}
      style={{
        height: typeof height === 'number' ? `${height}px` : height,
        overflow: 'auto',
        contain: 'strict',
      }}
    >
      <div style={{ height: `${rowVirtualizer.getTotalSize()}px`, width: '100%', position: 'relative' }}>
        {rowVirtualizer.getVirtualItems().map((virtualRow) => (
          <div
            key={virtualRow.key}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              height: `${virtualRow.size}px`,
              transform: `translateY(${virtualRow.start}px)`,
            }}
          >
            {renderItem(items[virtualRow.index], virtualRow.index)}
          </div>
        ))}
      </div>

      {items.length === 0 && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            height: '100%',
            color: '#999',
            fontSize: '14px',
          }}
        >
          暂无数据
        </div>
      )}
    </div>
  );
}
