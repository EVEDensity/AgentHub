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
      : 'bg-primary-500 text-white hover:bg-primary-600';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-danger-50">
            <svg viewBox="0 0 24 24" className="h-6 w-6 text-danger-500" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
          </div>
          <h3 className="text-lg font-semibold text-warm-800">{title}</h3>
        </div>
        <div className="mb-6 text-sm leading-6 text-warm-600">{message}</div>
        {details && (
          <div className="mb-6 rounded-lg border border-warm-150 bg-warm-50 p-3 text-xs leading-5 text-warm-600">
            {details}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button
            className="rounded-lg border border-warm-150 bg-white px-4 py-2 text-sm font-medium text-warm-600 hover:bg-warm-50"
            onClick={onCancel}
          >
            {cancelText}
          </button>
          <button
            className={`rounded-lg px-4 py-2 text-sm font-medium transition ${confirmClass}`}
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
