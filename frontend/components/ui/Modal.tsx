'use client';

import { useEffect, useRef, useCallback, useState, type JSX, type ReactNode } from 'react';
import { useFocusTrap } from '../../lib/accessibility/focusTrap';

export type ModalSize = 'sm' | 'md' | 'lg' | 'xl' | 'full';

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  titleId?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: ModalSize;
  closeOnBackdrop?: boolean;
  closeOnEsc?: boolean;
  className?: string;
}

const SIZE_STYLES: Record<ModalSize, string> = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-xl',
  full: 'max-w-[90vw] max-h-[90vh]',
};

let idCounter = 0;
function useUniqueId(prefix: string): string {
  const [id] = useState(() => `${prefix}-${++idCounter}`);
  return id;
}

export function Modal({
  open,
  onClose,
  title,
  titleId: externalTitleId,
  children,
  footer,
  size = 'md',
  closeOnBackdrop = true,
  closeOnEsc = true,
  className = '',
}: ModalProps): JSX.Element {
  const internalTitleId = useUniqueId('modal-title');
  const titleId = externalTitleId || internalTitleId;
  const containerRef = useRef<HTMLDivElement>(null);

  useFocusTrap(containerRef, open, closeOnEsc ? onClose : undefined);

  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [open]);

  if (!open) return <></>;

  return (
    <div
      data-modal-root
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 animate-fade-in"
      onClick={closeOnBackdrop ? onClose : undefined}
      role="presentation"
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        className={`w-full ${SIZE_STYLES[size]} mx-4 bg-warm-100 shadow-lg ${className}`}
        onClick={(e) => e.stopPropagation()}
      >
        {title && (
          <div className="flex items-center justify-between px-5 py-3 border-b border-warm-200">
            {typeof title === 'string' ? (
              <h3 id={titleId} className="text-sm font-semibold text-warm-800">
                {title}
              </h3>
            ) : (
              <div id={titleId}>{title}</div>
            )}
            <button
              className="text-warm-400 hover:text-warm-600 shrink-0"
              onClick={onClose}
              aria-label="Close modal"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        )}
        <div className="px-5 py-3 overflow-y-auto max-h-[60vh]">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-warm-200 bg-warm-100">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

export default Modal;
