'use client';

import { memo, useState, type JSX } from 'react';
import { AlertTriangle, ShieldAlert, ArrowRight, X } from 'lucide-react';
import type { RiskWarningEvent, RiskWarningAction } from '../../types';

interface RiskAlertBubbleProps {
  data: RiskWarningEvent;
  isStreaming?: boolean;
  onSendEvent: (event: Record<string, unknown>) => void;
}

const RISK_COLORS: Record<string, { border: string; bg: string; text: string; badge: string; icon: string }> = {
  critical: {
    border: 'border-red-300',
    bg: 'bg-gradient-to-br from-red-50 to-orange-50',
    text: 'text-red-800',
    badge: 'bg-red-200 text-red-700',
    icon: 'text-red-500',
  },
  high: {
    border: 'border-orange-300',
    bg: 'bg-gradient-to-br from-orange-50 to-amber-50',
    text: 'text-orange-800',
    badge: 'bg-orange-200 text-orange-700',
    icon: 'text-orange-500',
  },
  medium: {
    border: 'border-yellow-300',
    bg: 'bg-gradient-to-br from-yellow-50 to-amber-50',
    text: 'text-yellow-800',
    badge: 'bg-yellow-200 text-yellow-700',
    icon: 'text-yellow-500',
  },
  low: {
    border: 'border-blue-200',
    bg: 'bg-gradient-to-br from-blue-50 to-slate-50',
    text: 'text-blue-800',
    badge: 'bg-blue-100 text-blue-600',
    icon: 'text-blue-400',
  },
};

const RISK_LABELS: Record<string, string> = {
  critical: '严重风险',
  high: '高风险',
  medium: '中风险',
  low: '低风险',
};

const RiskAlertBubble = memo(function RiskAlertBubble({ data, isStreaming, onSendEvent }: RiskAlertBubbleProps): JSX.Element {
  const [selected, setSelected] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const colors = RISK_COLORS[data.riskLevel] || RISK_COLORS.medium;

  function handleAction(action: RiskWarningAction) {
    if (submitted) return;
    setSelected(action.id);
    onSendEvent({
      event: 'risk_warning_response',
      sessionId: data.sessionId,
      warningMessageId: data.messageId,
      selectedActionId: action.id,
    });
    setSubmitted(true);
  }

  const IntentIcon = data.riskLevel === 'critical' ? ShieldAlert : AlertTriangle;

  return (
    <div className="mb-4 flex justify-start">
      <div className={`max-w-[85%] rounded-2xl px-4 py-3 border shadow-sm ${colors.border} ${colors.bg}`}>
        {/* Header */}
        <div className="mb-2 flex items-center gap-2 text-xs">
          <IntentIcon className={`h-4 w-4 ${colors.icon}`} />
          <span className={`font-semibold ${colors.text}`}>{data.agentId || 'PM'}</span>
          <span className={`rounded px-2 py-0.5 text-xs font-bold ${colors.badge}`}>
            {RISK_LABELS[data.riskLevel] || data.riskLevel}
          </span>
          {isStreaming && <span className="inline-block h-3 w-0.5 animate-pulse bg-red-500" />}
        </div>

        {/* Title */}
        <div className={`mb-1.5 text-sm font-semibold ${colors.text}`}>
          {data.title}
        </div>

        {/* Description */}
        <div className="mb-3 text-sm text-warm-600 leading-relaxed whitespace-pre-wrap">
          {data.description}
        </div>

        {/* Action buttons */}
        <div className="flex flex-wrap gap-2">
          {data.actions.map((action) => (
            <button
              key={action.id}
              type="button"
              disabled={submitted}
              onClick={() => handleAction(action)}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition-all ${
                submitted && selected === action.id
                  ? data.riskLevel === 'critical' || data.riskLevel === 'high'
                    ? 'bg-red-500 text-white shadow-sm'
                    : 'bg-orange-500 text-white shadow-sm'
                  : submitted
                    ? 'bg-warm-100 text-warm-400 cursor-not-allowed'
                    : action.intent === 'cancel'
                      ? 'bg-white border border-red-300 text-red-600 hover:bg-red-50 active:scale-[0.97]'
                      : action.intent === 'mitigate'
                        ? 'bg-white border border-amber-300 text-amber-700 hover:bg-amber-50 active:scale-[0.97]'
                        : 'bg-white border border-warm-200 text-warm-700 hover:bg-warm-50 active:scale-[0.97]'
              }`}
              title={action.description}
            >
              <ArrowRight className="h-3.5 w-3.5" />
              {action.label}
            </button>
          ))}
        </div>

        {/* Submitted state */}
        {submitted && (
          <div className="mt-2 text-xs text-warm-500 flex items-center gap-1">
            <X className="h-3 w-3" />
            已选择应对方案
          </div>
        )}
      </div>
    </div>
  );
});

export default RiskAlertBubble;
