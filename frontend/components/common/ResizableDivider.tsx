import { useCallback, useEffect, useRef, useState, type JSX } from 'react';
import { clamp } from '../../hooks/useResizableSize';

interface ResizableDividerProps {
  /** 拖动方向：'horizontal' = 垂直分隔条，左右拖动；'vertical' = 水平分隔条，上下拖动 */
  orientation: 'horizontal' | 'vertical';
  /** 当前尺寸 */
  size: number;
  /** 拖动过程中实时回调（不持久化） */
  onPreview: (next: number) => void;
  /** 拖动结束 / 数值确定时回调（持久化） */
  onCommit: (next: number) => void;
  /** 最小值 */
  min: number;
  /** 最大值 */
  max: number;
  /** 默认值（双击 / 弹窗重置） */
  defaultValue: number;
  /** 父容器引用（百分比模式下用其 width/height 换算） */
  containerRef?: React.RefObject<HTMLElement>;
  /** 百分比模式（size 单位 %） */
  isPercentage?: boolean;
  /** 重置回调 */
  onReset: () => void;
  /** 提示文本（hover tooltip） */
  title?: string;
  /** a11y 标签 */
  ariaLabel?: string;
  /**
   * 拖动方向是否反向。
   *
   * 默认 false：拖动方向与 size 的增减方向一致（向右拖 size 增大）。
   * 设为 true：拖动方向与 size 的增减方向相反（向右拖 size 减小）。
   *
   * 用法：分隔条在两个面板之间时，如果 size 表达的是「右侧面板」宽度，
   * 用户的直觉是「把分隔条向右推 = 聊天区在推分隔条变宽 = 右侧面板变窄」，
   * 此时将 reversed 设为 true 可让交互方向与直觉一致。
   */
  reversed?: boolean;
  /**
   * 拖拽时尺寸气泡出现的位置。
   * - horizontal 分隔条：'left' | 'right'
   * - vertical 分隔条：'top' | 'bottom'
   *
   * 默认根据 orientation 自动选择 'right' / 'bottom'（让气泡出现在「被调整面板」那一侧）。
   * 显式传入可强制指定。
   */
  bubbleSide?: 'left' | 'right' | 'top' | 'bottom';
}

/**
 * ResizableDivider
 * ────────────────
 * 可拖拽 + 数值输入 + 双击重置 的分隔条。
 *
 * 交互：
 *  - 鼠标 / 触摸拖动：松手才持久化（onCommit），过程中只触发 onPreview
 *  - 双击：重置为默认值
 *  - 右键 / 长按 600ms：弹出数值输入面板（可输入具体值或拖动 slider）
 *  - 键盘：聚焦后 ←/→/↑/↓ 调整（Shift 加速），Home/End 跳到极值
 */
export default function ResizableDivider({
  orientation,
  size,
  onPreview,
  onCommit,
  min,
  max,
  defaultValue,
  containerRef,
  isPercentage = false,
  onReset,
  title,
  ariaLabel,
  reversed = false,
  bubbleSide,
}: ResizableDividerProps): JSX.Element {
  const dragState = useRef<{ startX: number; startY: number; startSize: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [popoverValue, setPopoverValue] = useState<string>('');
  const [popoverPos, setPopoverPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const popoverInputRef = useRef<HTMLInputElement>(null);
  const longPressTimer = useRef<number | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  // 根据当前事件计算 next 尺寸
  const computeNext = useCallback(
    (clientX: number, clientY: number, startSize: number): number => {
      if (!dragState.current) return startSize;
      const rawDelta =
        orientation === 'horizontal' ? clientX - dragState.current.startX : clientY - dragState.current.startY;
      // reversed 模式：拖动方向与 size 增减方向相反
      const signedDelta = reversed ? -rawDelta : rawDelta;
      if (isPercentage && containerRef?.current) {
        const rect = containerRef.current.getBoundingClientRect();
        const total = orientation === 'horizontal' ? rect.width : rect.height;
        if (total <= 0) return startSize;
        return clamp(startSize + (signedDelta / total) * 100, min, max);
      }
      return clamp(startSize + signedDelta, min, max);
    },
    [orientation, isPercentage, containerRef, min, max, reversed],
  );

  // ── Mouse drag ────────────────────────────────────────
  const beginDrag = useCallback(
    (x: number, y: number) => {
      dragState.current = { startX: x, startY: y, startSize: size };
      setDragging(true);
    },
    [size],
  );

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button !== 0) return;
      e.preventDefault();
      beginDrag(e.clientX, e.clientY);
      // 长按 600ms 弹数值输入
      longPressTimer.current = window.setTimeout(() => {
        if (buttonRef.current) {
          const rect = buttonRef.current.getBoundingClientRect();
          openPopover(rect.left + rect.width / 2, rect.bottom + 4);
        }
      }, 600);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [beginDrag],
  );

  const clearLongPress = useCallback(() => {
    if (longPressTimer.current != null) {
      clearTimeout(longPressTimer.current);
      longPressTimer.current = null;
    }
  }, []);

  const openPopover = useCallback(
    (x: number, y: number) => {
      clearLongPress();
      setPopoverValue(String(Math.round(size)));
      setPopoverPos({ x, y });
      setPopoverOpen(true);
      setTimeout(() => popoverInputRef.current?.select(), 0);
    },
    [size, clearLongPress],
  );

  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      openPopover(e.clientX, e.clientY);
    },
    [openPopover],
  );

  // 拖动 effect（鼠标）
  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: MouseEvent) => {
      if (!dragState.current) return;
      onPreview(computeNext(e.clientX, e.clientY, dragState.current.startSize));
    };
    const onUp = (e: MouseEvent) => {
      if (!dragState.current) return;
      const final = computeNext(e.clientX, e.clientY, dragState.current.startSize);
      onCommit(final);
      dragState.current = null;
      setDragging(false);
      clearLongPress();
      window.getSelection()?.removeAllRanges();
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    return () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
  }, [dragging, computeNext, onPreview, onCommit, clearLongPress]);

  // ── Touch drag ────────────────────────────────────────
  const handleTouchStart = useCallback(
    (e: React.TouchEvent) => {
      const t = e.touches[0];
      if (!t) return;
      beginDrag(t.clientX, t.clientY);
    },
    [beginDrag],
  );

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: TouchEvent) => {
      if (!dragState.current) return;
      const t = e.touches[0];
      if (!t) return;
      e.preventDefault();
      onPreview(computeNext(t.clientX, t.clientY, dragState.current.startSize));
    };
    const onEnd = (e: TouchEvent) => {
      if (!dragState.current) return;
      const t = e.changedTouches[0];
      if (!t) return;
      const final = computeNext(t.clientX, t.clientY, dragState.current.startSize);
      onCommit(final);
      dragState.current = null;
      setDragging(false);
    };
    document.addEventListener('touchmove', onMove, { passive: false });
    document.addEventListener('touchend', onEnd);
    document.addEventListener('touchcancel', onEnd);
    return () => {
      document.removeEventListener('touchmove', onMove);
      document.removeEventListener('touchend', onEnd);
      document.removeEventListener('touchcancel', onEnd);
    };
  }, [dragging, computeNext, onPreview, onCommit]);

  // ── Keyboard accessibility ────────────────────────────
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const step = e.shiftKey ? 32 : 8;
      let delta = 0;
      if (orientation === 'horizontal') {
        if (e.key === 'ArrowLeft') delta = -step;
        else if (e.key === 'ArrowRight') delta = step;
        else if (e.key === 'Home') {
          e.preventDefault();
          onCommit(min);
          return;
        } else if (e.key === 'End') {
          e.preventDefault();
          onCommit(max);
          return;
        } else return;
      } else {
        if (e.key === 'ArrowUp') delta = -step;
        else if (e.key === 'ArrowDown') delta = step;
        else if (e.key === 'Home') {
          e.preventDefault();
          onCommit(min);
          return;
        } else if (e.key === 'End') {
          e.preventDefault();
          onCommit(max);
          return;
        } else return;
      }
      e.preventDefault();
      onCommit(clamp(size + delta, min, max));
    },
    [orientation, size, min, max, onCommit],
  );

  // ── Double-click → reset ──────────────────────────────
  const handleDoubleClick = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      onReset();
    },
    [onReset],
  );

  // ── Popover commit ────────────────────────────────────
  const commitPopover = useCallback(() => {
    const n = Number.parseFloat(popoverValue);
    if (Number.isFinite(n)) onCommit(clamp(n, min, max));
    setPopoverOpen(false);
  }, [popoverValue, min, max, onCommit]);

  // ── Visual classes ────────────────────────────────────
  const isH = orientation === 'horizontal';
  const wrapperClasses = isH
    ? 'group relative w-1.5 h-full cursor-col-resize'
    : 'group relative h-1.5 w-full cursor-row-resize';

  // 气泡位置：默认让气泡出现在「被调整面板」那一侧
  // - horizontal 分隔条：默认 right（在分隔条右侧）
  // - vertical   分隔条：默认 bottom（在分隔条下方）
  const effectiveBubbleSide: 'left' | 'right' | 'top' | 'bottom' =
    bubbleSide ?? (isH ? 'right' : 'bottom');
  const bubblePositionClass = (() => {
    if (isH) {
      return effectiveBubbleSide === 'right'
        ? 'top-1/2 -translate-y-1/2 right-3'
        : 'top-1/2 -translate-y-1/2 left-3';
    }
    return effectiveBubbleSide === 'bottom'
      ? 'left-1/2 -translate-x-1/2 bottom-3'
      : 'left-1/2 -translate-x-1/2 top-3';
  })();

  const barClasses = isH
    ? `absolute top-0 left-1/2 -translate-x-1/2 h-full w-px transition-colors ${
        dragging ? 'bg-primary-500' : 'bg-transparent group-hover:bg-primary-300'
      }`
    : `absolute left-0 top-1/2 -translate-y-1/2 w-full h-px transition-colors ${
        dragging ? 'bg-primary-500' : 'bg-transparent group-hover:bg-primary-300'
      }`;

  const hitAreaClasses = isH ? 'absolute inset-y-0 -left-1 -right-1' : 'absolute inset-x-0 -top-1 -bottom-1';

  return (
    <>
      <div
        className={wrapperClasses}
        title={title}
        onContextMenu={handleContextMenu}
        style={{ flexShrink: 0, zIndex: dragging ? 50 : 5 }}
      >
        <button
          ref={buttonRef}
          type="button"
          className="absolute inset-0 cursor-inherit"
          aria-label={ariaLabel || (isH ? '左右拖动调整宽度' : '上下拖动调整高度')}
          aria-valuenow={Math.round(size)}
          aria-valuemin={min}
          aria-valuemax={max}
          onMouseDown={handleMouseDown}
          onMouseUp={clearLongPress}
          onMouseLeave={clearLongPress}
          onTouchStart={handleTouchStart}
          onDoubleClick={handleDoubleClick}
          onKeyDown={handleKeyDown}
          tabIndex={0}
        >
          <span className={hitAreaClasses} />
          <span className={barClasses} />
          {/* hover 时显示的中央 grip 装饰 */}
          <span
            className={`pointer-events-none absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 ${
              isH ? 'h-8 w-1' : 'h-1 w-8'
            } rounded-full transition-opacity ${
              dragging ? 'bg-primary-500 opacity-100' : 'bg-warm-300 opacity-0 group-hover:opacity-100'
            }`}
          />
          {/* 拖拽中显示尺寸气泡 */}
          {dragging && (
            <span
              className={`pointer-events-none absolute z-50 whitespace-nowrap rounded-md bg-warm-800 px-2 py-1 text-xs font-medium text-white shadow-lg ${bubblePositionClass}`}
            >
              {isPercentage ? `${size.toFixed(1)}%` : `${Math.round(size)}px`}
            </span>
          )}
        </button>
      </div>

      {/* 拖动时的全屏捕获层，防止选中文本 */}
      {dragging && (
        <div
          className="fixed inset-0 z-40"
          style={{ cursor: isH ? 'col-resize' : 'row-resize', userSelect: 'none' }}
          aria-hidden
        />
      )}

      {/* 数值输入弹窗 */}
      {popoverOpen && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setPopoverOpen(false)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setPopoverOpen(false);
            }}
            role="presentation"
          />
          <div
            className="fixed z-50 w-60 rounded-lg border border-warm-200 bg-warm-100 p-3 shadow-modal"
            style={{
              left: Math.min(Math.max(popoverPos.x - 120, 8), window.innerWidth - 248),
              top: Math.min(Math.max(popoverPos.y, 8), window.innerHeight - 180),
            }}
          >
            <div className="mb-2 flex items-center justify-between text-xs font-semibold text-warm-700">
              <span>{ariaLabel || '调整尺寸'}</span>
              <span className="text-warm-400">
                {isPercentage ? `${size.toFixed(1)}%` : `${Math.round(size)}px`}
              </span>
            </div>
            <div className="mb-2 flex items-center gap-2">
              <input
                ref={popoverInputRef}
                type="number"
                value={popoverValue}
                onChange={(e) => setPopoverValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') commitPopover();
                  else if (e.key === 'Escape') setPopoverOpen(false);
                }}
                step={isPercentage ? '0.5' : '1'}
                min={min}
                max={max}
                className="flex-1 rounded border border-warm-200 px-2 py-1.5 text-sm focus:border-primary-400 focus:outline-none"
                autoFocus
              />
              <span className="text-xs text-warm-500">{isPercentage ? '%' : 'px'}</span>
            </div>
            <input
              type="range"
              value={Number.parseFloat(popoverValue) || size}
              min={min}
              max={max}
              step={isPercentage ? 0.5 : 1}
              onChange={(e) => setPopoverValue(e.target.value)}
              className="mb-1 w-full accent-primary-500"
            />
            <div className="flex justify-between text-[11px] text-warm-400">
              <span>最小 {isPercentage ? `${min}%` : `${min}px`}</span>
              <span>最大 {isPercentage ? `${max}%` : `${max}px`}</span>
            </div>
            <div className="mt-3 flex items-center justify-between border-t border-warm-100 pt-2">
              <button
                type="button"
                onClick={() => {
                  onReset();
                  setPopoverOpen(false);
                }}
                className="text-xs text-warm-500 hover:text-primary-500"
              >
                [reset] 重置（{Math.round(defaultValue)}
                {isPercentage ? '%' : 'px'}）
              </button>
              <div className="flex gap-1.5">
                <button
                  type="button"
                  onClick={() => setPopoverOpen(false)}
                  className="rounded px-2.5 py-1 text-xs text-warm-600 hover:bg-warm-100"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={commitPopover}
                  className="rounded bg-primary-500 px-2.5 py-1 text-xs text-white hover:bg-primary-600"
                >
                  确定
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </>
  );
}
