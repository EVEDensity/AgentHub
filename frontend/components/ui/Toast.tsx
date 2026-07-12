'use client';

import { createContext, useCallback, useContext, useEffect, useRef, useState, type JSX, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

export interface Toast {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  title: string;
  message?: string;
  duration?: number;
}

interface ToastContextValue {
  addToast: (toast: Omit<Toast, 'id'>) => string;
  removeToast: (id: string) => void;
  toasts: Toast[];
}

const TOAST_STYLES: Record<Toast['type'], string> = {
  success: 'border-success-100 bg-success-50 text-success-600',
  error: 'border-danger-100 bg-danger-50 text-danger-600',
  warning: 'border-warning-100 bg-warning-50 text-warning-600',
  info: 'border-warm-200 bg-warm-100 text-warm-600',
};

const TOAST_PROGRESS_STYLES: Record<Toast['type'], string> = {
  success: 'bg-success-500',
  error: 'bg-danger-500',
  warning: 'bg-warning-500',
  info: 'bg-warm-400',
};

const ToastContext = createContext<ToastContextValue>({
  addToast: () => '',
  removeToast: () => {},
  toasts: [],
});

export function useToast(): ToastContextValue {
  return useContext(ToastContext);
}

function ToastItem({ toast, onRemove }: { toast: Toast; onRemove: (id: string) => void }) {
  const [exiting, setExiting] = useState(false);
  const [progress, setProgress] = useState(100);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startRef = useRef(Date.now());
  const duration = toast.duration ?? 5000;

  useEffect(() => {
    if (duration <= 0) return;

    const tick = () => {
      const elapsed = Date.now() - startRef.current;
      const remaining = Math.max(0, 100 - (elapsed / duration) * 100);
      setProgress(remaining);
      if (remaining <= 0) {
        handleDismiss();
      } else {
        timerRef.current = setTimeout(tick, 50);
      }
    };
    timerRef.current = setTimeout(tick, 50);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [duration]);

  const handleDismiss = () => {
    setExiting(true);
    setTimeout(() => onRemove(toast.id), 300);
  };

  return (
    <div
      className={`pointer-events-auto border px-4 py-3 shadow-card transition-all duration-300 min-w-[320px] max-w-[420px] ${
        TOAST_STYLES[toast.type]
      } ${exiting ? 'opacity-0 translate-x-8 scale-95' : 'opacity-100 translate-x-0 scale-100'}`}
      role="alert"
    >
      <div className="flex items-start gap-2.5">
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-sm">{toast.title}</div>
          {toast.message && (
            <div className="text-xs mt-0.5 opacity-80 whitespace-pre-wrap break-words">
              {toast.message}
            </div>
          )}
        </div>
        <button
          onClick={handleDismiss}
          className="shrink-0 inline-flex h-5 w-5 items-center justify-center rounded-full hover:bg-black/20 transition-colors"
          aria-label="Close notification"
        >
          <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>

      {duration > 0 && (
        <div className="mt-2 h-0.5 w-full rounded-full bg-black/20 overflow-hidden">
          <div
            className={`h-full rounded-full transition-[width] duration-100 linear ${TOAST_PROGRESS_STYLES[toast.type]}`}
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
    </div>
  );
}

function ToastContainer() {
  const { toasts, removeToast } = useToast();
  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[9999] flex flex-col-reverse gap-2 pointer-events-none">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onRemove={removeToast} />
      ))}
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }): JSX.Element {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counterRef = useRef(0);

  const addToast = useCallback((t: Omit<Toast, 'id'>): string => {
    const id = `toast-${++counterRef.current}-${Date.now()}`;
    setToasts((prev) => {
      const next = prev.length >= 5 ? prev.slice(1) : prev;
      return [...next, { ...t, id }];
    });
    return id;
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const [mounted, setMounted] = useState(false);
  useEffect(() => { setMounted(true); }, []);

  const portalContent = mounted
    ? createPortal(<ToastContainer />, document.body)
    : null;

  return (
    <ToastContext.Provider value={{ addToast, removeToast, toasts }}>
      {children}
      {portalContent}
    </ToastContext.Provider>
  );
}

export function useAddToast(): Pick<ToastContextValue, 'addToast'> {
  const { addToast } = useContext(ToastContext);
  return { addToast };
}

export default ToastProvider;
