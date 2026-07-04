'use client';

import { useEffect, useRef, useCallback, useState, type JSX, type ReactNode } from 'react';
import { useFocusTrap } from '../../lib/accessibility/focusTrap';

// ── Types ─────────────────────────────────────────────────────────────

export type ModalSize = 'sm' | 'md' | 'lg' | 'xl' | 'full';

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  /** Unique ID for the modal title — used for aria-labelledby (WCAG 4.1.2) */
  titleId?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: ModalSize;
  closeOnBackdrop?: boolean;
  closeOnEsc?: boolean;
  className?: string;
}

// ── Size styles ───────────────────────────────────────────────────────

const SIZE_STYLES: Record<ModalSize, string> = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-xl',
  full: 'max-w-[90vw] max-h-[90vh]',
};

// ── Unique ID generator ────────────────────────────────────────────────

let idCounter = 0;
function useUniqueId(prefix: string): string {
  const [id] = useState(() => `${prefix}-${++idCounter}`);
  return id;
}

// ── Component ─────────────────────────────────────────────────────────

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

  // Focus trap for WCAG 2.4.3 (Focus Order)
  useFocusTrap(containerRef, open, closeOnEsc ? onClose : undefined);

  // Lock body scroll when modal is open (WCAG: prevent background scroll)
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
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm animate-fade-in"
      onClick={closeOnBackdrop ? onClose : undefined}
      // Role presentation on backdrop — the dialog itself carries the role
      role="presentation"
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        className={`w-full ${SIZE_STYLES[size]} mx-4 bg-white rounded-2xl shadow-2xl overflow-hidden animate-scale-in ${className}`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        {title && (
          <div className="flex items-center justify-between px-6 py-4 border-b border-warm-100">
            {typeof title === 'string' ? (
              <h3 id={titleId} className="text-base font-semibold text-warm-800">
                {title}
              </h3>
            ) : (
              <div id={titleId}>{title}</div>
            )}
            <button
              className="btn-icon text-warm-400 hover:text-warm-600 shrink-0"
              onClick={onClose}
              aria-label="Close modal"
            >
              <span className="material-symbols-outlined text-[20px]" aria-hidden="true">
                close
              </span>
            </button>
          </div>
        )}

        {/* Body */}
        <div className="px-6 py-4 overflow-y-auto max-h-[60vh]">{children}</div>

        {/* Footer */}
        {footer && (
          <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-warm-100 bg-warm-50/30">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

export default Modal;
