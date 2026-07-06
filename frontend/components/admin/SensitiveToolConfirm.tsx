'use client';

import { useState } from 'react';

interface SensitiveToolConfirmProps {
  toolName: string;
  riskLevel: 'high' | 'critical';
  command?: string;
  agentId?: string;
  sessionId?: string;
  onConfirm: (reason: string) => void;
  onDeny: () => void;
  onClose: () => void;
}

const RISK_CONFIG = {
  high: {
    bg: 'bg-danger-50',
    border: 'border-orange-300',
    icon: '[warn]',
    iconBg: 'bg-orange-100',
    label: '高风险操作',
    desc: '此工具可能修改系统状态或访问外部资源，需要您确认后执行。',
    btnBg: 'bg-orange-500 hover:bg-orange-600',
    badge: 'bg-orange-100 text-orange-700 ring-orange-300',
  },
  critical: {
    bg: 'from-red-50 to-rose-100',
    border: 'border-red-400',
    icon: '[forbidden]',
    iconBg: 'bg-red-100',
    label: '严重风险操作',
    desc: '此工具具有破坏性，可能导致数据丢失或系统不可用。请仔细评估后再确认。',
    btnBg: 'bg-red-600 hover:bg-red-700',
    badge: 'bg-red-100 text-red-700 ring-red-400',
  },
};

export default function SensitiveToolConfirm({
  toolName,
  riskLevel,
  command,
  agentId,
  sessionId,
  onConfirm,
  onDeny,
  onClose,
}: SensitiveToolConfirmProps): JSX.Element {
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const config = RISK_CONFIG[riskLevel];

  const handleConfirm = async () => {
    if (!confirmed || submitting) return;
    setSubmitting(true);
    // Record the confirmation as an audit entry
    try {
      await fetch('/audit/sensitive-confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tool_name: toolName,
          risk_level: riskLevel,
          user_id: 'current', // The gateway extracts this from the JWT
          agent_id: agentId || '',
          session_id: sessionId || '',
          confirmed: true,
          reason,
        }),
      });
    } catch {
      // Non-blocking — confirmation proceeds even if audit fails
    }
    onConfirm(reason);
  };

  const handleDeny = async () => {
    setSubmitting(true);
    try {
      await fetch('/audit/sensitive-confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tool_name: toolName,
          risk_level: riskLevel,
          user_id: 'current',
          agent_id: agentId || '',
          session_id: sessionId || '',
          confirmed: false,
          reason: 'User denied confirmation',
        }),
      });
    } catch {
      // Non-blocking
    }
    onDeny();
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 px-4 animate-in fade-in duration-150"
      onClick={onClose}
    >
      <div
        className={`w-full max-w-lg overflow-hidden rounded-2xl border-2 ${config.border} bg-gradient-to-b ${config.bg} shadow-2xl`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-4 px-6 pt-6 pb-4">
          <div className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl ${config.iconBg} text-3xl shadow-sm`}>
            {config.icon}
          </div>
          <div className="min-w-0">
            <h2 className="text-xl font-bold text-gray-900">{config.label}</h2>
            <p className="mt-0.5 text-sm text-gray-600">{config.desc}</p>
          </div>
          <button
            className="btn-ghost ml-auto shrink-0 rounded-lg p-2 text-gray-400 hover:text-gray-600"
            onClick={onClose}
          >
            <span className="material-symbols-outlined text-[20px]">close</span>
          </button>
        </div>

        {/* Body */}
        <div className="space-y-4 px-6 pb-2">
          {/* Tool info */}
          <div className="rounded-xl bg-white/80 p-4 shadow-sm ring-1 ring-black/5">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-semibold text-gray-700">工具详情</h3>
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${config.badge}`}
              >
                <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />
                {riskLevel === 'critical' ? '严重' : '高风险'}
              </span>
            </div>

            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-400 w-16 shrink-0">工具名称</span>
                <code className="flex-1 rounded bg-gray-100 px-2.5 py-1.5 text-sm font-mono text-gray-800 truncate">
                  {toolName}
                </code>
              </div>

              {command && (
                <div className="flex items-start gap-2">
                  <span className="text-xs text-gray-400 w-16 shrink-0 mt-1">命令</span>
                  <code className="flex-1 rounded bg-gray-900 px-3 py-2 text-xs font-mono text-green-400 break-all">
                    {command}
                  </code>
                </div>
              )}

              {agentId && (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-400 w-16 shrink-0">Agent</span>
                  <span className="text-sm text-gray-700 font-medium">{agentId}</span>
                </div>
              )}

              {sessionId && (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-400 w-16 shrink-0">会话</span>
                  <span className="text-xs text-gray-500 font-mono">{sessionId}</span>
                </div>
              )}
            </div>
          </div>

          {/* Safety checklist */}
          <div className="rounded-xl bg-white/80 p-4 shadow-sm ring-1 ring-black/5">
            <h3 className="text-sm font-semibold text-gray-700 mb-3">安全检查清单</h3>
            <div className="space-y-2">
              <label className="flex items-start gap-3 cursor-pointer group">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(e) => setConfirmed(e.target.checked)}
                  className="mt-0.5 h-4 w-4 rounded border-gray-300 text-primary-500 focus:ring-primary-400"
                />
                <span className="text-sm text-gray-600 group-hover:text-gray-800 transition-colors">
                  我了解此操作的{riskLevel === 'critical' ? '破坏性' : '风险'}，并确认允许 Agent 执行此工具
                </span>
              </label>
            </div>
          </div>

          {/* Reason input */}
          <div className="rounded-xl bg-white/80 p-4 shadow-sm ring-1 ring-black/5">
            <h3 className="text-sm font-semibold text-gray-700 mb-2">
              确认原因 <span className="text-gray-400 font-normal">（可选）</span>
            </h3>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="简述确认此操作的原因，便于审计追溯..."
              rows={3}
              className="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-800 placeholder:text-gray-400 outline-none focus:border-primary-300 focus:ring-2 focus:ring-primary-100 resize-none transition-all"
            />
            <p className="mt-1 text-[11px] text-gray-400">
              此信息将记录在审计日志中，用于后续安全审查。
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-5 border-t border-black/5 mt-2">
          <button
            className="btn-secondary text-sm"
            onClick={handleDeny}
            disabled={submitting}
          >
            ✗ 拒绝
          </button>
          <button
            className={`inline-flex items-center gap-2 rounded-xl px-6 py-2.5 text-sm font-semibold text-white shadow-sm transition-all ${
              confirmed && !submitting
                ? `${config.btnBg} shadow-md`
                : 'bg-gray-300 cursor-not-allowed'
            }`}
            onClick={handleConfirm}
            disabled={!confirmed || submitting}
          >
            {submitting ? (
              <>
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                处理中...
              </>
            ) : (
              <>
                ✓ 确认执行
              </>
            )}
          </button>
        </div>
      </div>

      <style jsx>{`
        @keyframes fade-in {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        .animate-in {
          animation: fade-in 0.15s ease-out;
        }
      `}</style>
    </div>
  );
}
