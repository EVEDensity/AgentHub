import { memo, useState, useCallback } from 'react';

interface ToolPermissionDialogProps {
  requestId: string;
  toolName: string;
  arguments: Record<string, unknown>;
  riskLevel: string;
  reason: string;
  onDecision: (requestId: string, decision: 'allow' | 'deny') => void;
  onClose: () => void;
}

const RISK_LABELS: Record<string, { label: string; color: string; bg: string }> = {
  L1: { label: '低风险', color: 'text-green-700', bg: 'bg-green-100' },
  L2: { label: '中风险', color: 'text-yellow-700', bg: 'bg-yellow-100' },
  L3: { label: '高风险', color: 'text-red-700', bg: 'bg-red-100' },
};

const ToolPermissionDialog = memo(function ToolPermissionDialog({
  requestId,
  toolName,
  arguments: args,
  riskLevel,
  reason,
  onDecision,
  onClose,
}: ToolPermissionDialogProps) {
  const [expanded, setExpanded] = useState(false);
  const [deciding, setDeciding] = useState(false);

  const risk = RISK_LABELS[riskLevel] || RISK_LABELS.L1;

  const handleDecision = useCallback(
    (decision: 'allow' | 'deny') => {
      setDeciding(true);
      onDecision(requestId, decision);
      onClose();
    },
    [requestId, onDecision, onClose]
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="mx-4 w-full max-w-md rounded-2xl border border-warm-200 bg-white shadow-2xl">
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-warm-100 px-5 py-4">
          <span className="text-xl">🔐</span>
          <div className="flex-1">
            <h3 className="text-base font-semibold text-warm-900">工具调用确认</h3>
            <p className="text-xs text-warm-500">Agent 需要您的授权才能继续执行</p>
          </div>
          <span
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${risk.color} ${risk.bg}`}
          >
            {risk.label}
          </span>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-3">
          {/* Tool name */}
          <div>
            <span className="text-xs font-medium text-warm-400 uppercase tracking-wide">
              工具名称
            </span>
            <p className="mt-0.5 font-mono text-sm text-warm-800">{toolName}</p>
          </div>

          {/* Reason */}
          {reason && (
            <div>
              <span className="text-xs font-medium text-warm-400 uppercase tracking-wide">
                原因
              </span>
              <p className="mt-0.5 text-sm text-warm-700">{reason}</p>
            </div>
          )}

          {/* Arguments (expandable) */}
          <div>
            <button
              className="text-xs font-medium text-blue-600 hover:text-blue-700 transition"
              onClick={() => setExpanded(!expanded)}
            >
              {expanded ? '▲ 隐藏参数' : '▼ 查看参数'}
            </button>
            {expanded && (
              <pre className="mt-2 max-h-32 overflow-y-auto rounded-lg bg-warm-50 px-3 py-2 font-mono text-xs text-warm-700">
                {JSON.stringify(args, null, 2)}
              </pre>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 border-t border-warm-100 px-5 py-4">
          <button
            className="rounded-lg border border-warm-200 px-4 py-2 text-sm font-medium text-warm-600 hover:bg-warm-50 transition disabled:opacity-50"
            onClick={() => handleDecision('deny')}
            disabled={deciding}
          >
            ❌ 拒绝
          </button>
          <button
            className="rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700 transition disabled:opacity-50"
            onClick={() => handleDecision('allow')}
            disabled={deciding}
          >
            ✅ 允许
          </button>
        </div>
      </div>
    </div>
  );
});

export default ToolPermissionDialog;
