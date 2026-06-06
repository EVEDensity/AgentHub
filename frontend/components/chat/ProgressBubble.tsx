'use client';

import { memo, type JSX } from 'react';
import { TrendingUp, Clock } from 'lucide-react';
import type { ProgressUpdateEvent } from '../../types';

interface ProgressBubbleProps {
  data: ProgressUpdateEvent;
  isStreaming?: boolean;
}

function formatEta(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} 分钟`;
  return `${Math.round(seconds / 3600)} 小时`;
}

const ProgressBubble = memo(function ProgressBubble({ data, isStreaming }: ProgressBubbleProps): JSX.Element {
  const pct = data.totalSteps > 0 ? Math.round((data.completedSteps / data.totalSteps) * 100) : 0;
  const isDone = data.completedSteps >= data.totalSteps;

  return (
    <div className="mb-3 flex justify-start">
      <div className="max-w-[85%] rounded-2xl px-4 py-3 bg-gradient-to-br from-emerald-50 to-teal-50 border border-emerald-200 shadow-sm">
        {/* Header */}
        <div className="mb-2 flex items-center gap-2 text-xs opacity-80">
          <TrendingUp className="h-3.5 w-3.5 text-emerald-500" />
          <span className="font-semibold text-emerald-700">{data.agentId || 'PM'}</span>
          <span className={`rounded px-2 py-0.5 text-xs font-medium ${
            isDone ? 'bg-emerald-200 text-emerald-700' : 'bg-emerald-100 text-emerald-600'
          }`}>
            {isDone ? '已完成' : '进度汇报'}
          </span>
          {isStreaming && !isDone && (
            <span className="inline-block h-3 w-0.5 animate-pulse bg-emerald-500" />
          )}
        </div>

        {/* Current step description */}
        <div className="mb-2 text-sm text-warm-700">
          {data.currentStep}
        </div>

        {/* Progress bar */}
        <div className="mb-1.5 flex items-center gap-2">
          <div className="flex-1 h-2 rounded-full bg-emerald-100 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-700 ease-out ${
                pct >= 100
                  ? 'bg-emerald-500'
                  : pct > 50
                    ? 'bg-gradient-to-r from-emerald-400 to-teal-400'
                    : 'bg-emerald-400'
              }`}
              style={{ width: `${Math.min(pct, 100)}%` }}
            />
          </div>
          <span className="text-xs font-semibold text-emerald-700 min-w-[3rem] text-right tabular-nums">
            {data.completedSteps}/{data.totalSteps}
          </span>
        </div>

        {/* ETA */}
        {!isDone && data.estimatedRemainingSeconds != null && data.estimatedRemainingSeconds > 0 && (
          <div className="flex items-center gap-1 text-xs text-warm-500">
            <Clock className="h-3 w-3" />
            <span>预计还需 {formatEta(data.estimatedRemainingSeconds)}</span>
          </div>
        )}

        {/* Done state */}
        {isDone && (
          <div className="mt-1 text-xs text-emerald-600 font-medium">
            ✅ 全部步骤已完成
          </div>
        )}
      </div>
    </div>
  );
});

export default ProgressBubble;
