import React from 'react';

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string | React.ReactNode;
  details?: string | React.ReactNode;
  confirmText?: string;
  cancelText?: string;
  variant?: 'danger' | 'primary';
  onConfirm: () => void;
  onCancel: () => void;
}

const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  open,
  title,
  message,
  details,
  confirmText = '确认',
  cancelText = '取消',
  variant = 'danger',
  onConfirm,
  onCancel,
}) => {
  if (!open) return null;

  const confirmClass =
    variant === 'danger'
      ? 'bg-danger-500 text-white hover:bg-danger-600'
      : 'bg-primary-500 text-warm-50 hover:bg-primary-600';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md bg-warm-100 p-5 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center bg-danger-50">
            <svg viewBox="0 0 24 24" className="h-5 w-5 text-danger-500" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
          </div>
          <h3 className="text-base font-semibold text-warm-800">{title}</h3>
        </div>
        <div className="mb-5 text-sm leading-6 text-warm-600">{message}</div>
        {details && (
          <div className="mb-5 border border-warm-200 bg-warm-100 p-3 text-xs leading-5 text-warm-500">
            {details}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button
            className="border border-warm-300 bg-transparent px-4 py-2 text-sm font-medium text-warm-600 hover:bg-warm-100"
            onClick={onCancel}
          >
            {cancelText}
          </button>
          <button
            className={`px-4 py-2 text-sm font-medium transition-colors ${confirmClass}`}
            onClick={onConfirm}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
};

export default ConfirmDialog;
