'use client';

import { useState, useRef, useEffect, type JSX, type ReactNode } from 'react';

export type TooltipPosition = 'top' | 'bottom' | 'left' | 'right';

export interface TooltipProps {
  content: ReactNode;
  position?: TooltipPosition;
  delay?: number;
  maxWidth?: number;
  children: ReactNode;
  className?: string;
}

const POSITION_STYLES: Record<TooltipPosition, string> = {
  top: 'bottom-full left-1/2 -translate-x-1/2 mb-2',
  bottom: 'top-full left-1/2 -translate-x-1/2 mt-2',
  left: 'right-full top-1/2 -translate-y-1/2 mr-2',
  right: 'left-full top-1/2 -translate-y-1/2 ml-2',
};

const ARROW_STYLES: Record<TooltipPosition, string> = {
  top: 'top-full left-1/2 -translate-x-1/2 border-l-transparent border-r-transparent border-b-transparent border-t-warm-800',
  bottom: 'bottom-full left-1/2 -translate-x-1/2 border-l-transparent border-r-transparent border-t-transparent border-b-warm-800',
  left: 'left-full top-1/2 -translate-y-1/2 border-t-transparent border-b-transparent border-r-transparent border-l-warm-800',
  right: 'right-full top-1/2 -translate-y-1/2 border-t-transparent border-b-transparent border-l-transparent border-r-warm-800',
};

export function Tooltip({
  content,
  position = 'top',
  delay = 300,
  maxWidth = 280,
  children,
  className = '',
}: TooltipProps): JSX.Element {
  const [visible, setVisible] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const show = () => {
    timerRef.current = setTimeout(() => setVisible(true), delay);
  };

  const hide = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setVisible(false);
  };

  return (
    <div className={`relative inline-flex ${className}`} onMouseEnter={show} onMouseLeave={hide}>
      {children}
      {visible && (
        <div
          className={`absolute z-50 pointer-events-none transition-opacity duration-150 ${visible ? 'opacity-100' : 'opacity-0'} ${POSITION_STYLES[position]}`}
          style={{ maxWidth: `${maxWidth}px` }}
        >
          <div className="bg-warm-800 text-warm-200 text-xs px-3 py-1.5 shadow-lg whitespace-pre-wrap break-words">
            {content}
          </div>
          <div className={`absolute w-0 h-0 border-4 ${ARROW_STYLES[position]}`} />
        </div>
      )}
    </div>
  );
}

export default Tooltip;
