'use client';

import { useState, type JSX } from 'react';
import { ListTodo, Check, X, Edit3, Clock, ArrowRight } from 'lucide-react';
import type { TaskPreviewEvent, TaskPreviewItem } from '../../types';

interface TaskPreviewCardProps {
  data: TaskPreviewEvent;
  isStreaming?: boolean;
  onSendEvent: (event: Record<string, unknown>) => void;
}

function formatEta(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} 分钟`;
  return `${Math.round(seconds / 3600)} 小时`;
}

export default function TaskPreviewCard({ data, isStreaming, onSendEvent }: TaskPreviewCardProps): JSX.Element {
  const [submitted, setSubmitted] = useState(false);
  const [modifications, setModifications] = useState('');
  const [showModify, setShowModify] = useState(false);

  function handleConfirm() {
    if (submitted) return;
    setSubmitted(true);
    onSendEvent({
      event: 'task_preview_response',
      sessionId: data.sessionId,
      previewMessageId: data.messageId,
      decision: 'confirm',
    });
  }

  function handleModify() {
    if (submitted) return;
    setShowModify(true);
  }

  function handleSubmitModify() {
    if (submitted) return;
    setSubmitted(true);
    onSendEvent({
      event: 'task_preview_response',
      sessionId: data.sessionId,
      previewMessageId: data.messageId,
      decision: 'modify',
      modifications: modifications.trim() || '请调整任务计划',
    });
  }

  function handleCancel() {
    if (submitted) return;
    setSubmitted(true);
    onSendEvent({
      event: 'task_preview_response',
      sessionId: data.sessionId,
      previewMessageId: data.messageId,
      decision: 'cancel',
    });
  }

  function renderTaskCard(task: TaskPreviewItem, index: number) {
    const deps = task.dependencies || [];
    return (
      <div
        key={task.id}
        className="flex items-start gap-3 rounded-lg border border-warm-150 bg-warm-50/70 px-3 py-2.5"
      >
        {/* Step number */}
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary-100 text-xs font-bold text-primary-700">
          {index + 1}
        </span>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-warm-800">{task.description}</span>
            <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-600">
              @{task.agent}
            </span>
            {task.estimatedSeconds != null && task.estimatedSeconds > 0 && (
              <span className="inline-flex items-center gap-0.5 text-[10px] text-warm-400">
                <Clock className="h-3 w-3" />
                {formatEta(task.estimatedSeconds)}
              </span>
            )}
          </div>

          {/* Dependencies */}
          {deps.length > 0 && (
            <div className="mt-1 flex items-center gap-1 text-[10px] text-warm-400">
              <ArrowRight className="h-3 w-3" />
              <span>依赖: {deps.map((d, i) => (
                <span key={d}>
                  {i > 0 && ', '}
                  <span className="font-mono text-purple-500">{d}</span>
                </span>
              ))}</span>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="mb-4 flex justify-start">
      <div className="max-w-[85%] rounded-2xl px-4 py-3 bg-white border-2 border-primary-200 shadow-md">
        {/* Header */}
        <div className="mb-3 flex items-center gap-2 text-xs">
          <ListTodo className="h-4 w-4 text-primary-500" />
          <span className="font-semibold text-primary-700">PM 任务计划预览</span>
          <span className="rounded bg-primary-100 px-2 py-0.5 text-[10px] font-bold text-primary-600">
            需确认
          </span>
          {isStreaming && <span className="inline-block h-3 w-0.5 animate-pulse bg-primary-500" />}
        </div>

        {/* Summary */}
        <div className="mb-3 text-sm text-warm-600">
          共 {data.tasks.length} 个子任务
          {data.estimatedTotalSeconds != null && data.estimatedTotalSeconds > 0 && (
            <span className="ml-1 inline-flex items-center gap-1 text-warm-500">
              · 预计 {formatEta(data.estimatedTotalSeconds)}
            </span>
          )}
        </div>

        {/* Task list */}
        <div className="mb-3 space-y-2 max-h-64 overflow-y-auto">
          {data.tasks.map((task, i) => renderTaskCard(task, i))}
        </div>

        {/* Actions */}
        {!submitted && (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={handleConfirm}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary-500 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-600 active:scale-[0.97] transition-all shadow-sm"
            >
              <Check className="h-4 w-4" />
              确认执行
            </button>
            <button
              type="button"
              onClick={handleModify}
              className="inline-flex items-center gap-1.5 rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm font-medium text-amber-700 hover:bg-amber-100 active:scale-[0.97] transition-all"
            >
              <Edit3 className="h-4 w-4" />
              修改计划
            </button>
            <button
              type="button"
              onClick={handleCancel}
              className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-100 active:scale-[0.97] transition-all"
            >
              <X className="h-4 w-4" />
              取消
            </button>
          </div>
        )}

        {/* Modify input */}
        {showModify && !submitted && (
          <div className="mt-3 flex gap-2">
            <input
              type="text"
              value={modifications}
              onChange={(e) => setModifications(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleSubmitModify(); }}
              placeholder="描述你的修改意见..."
              className="flex-1 rounded-lg border border-warm-200 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400/60"
              autoFocus
            />
            <button
              type="button"
              onClick={handleSubmitModify}
              className="rounded-lg bg-amber-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-600 transition-colors"
            >
              提交
            </button>
          </div>
        )}

        {/* Submitted state */}
        {submitted && (
          <div className="mt-2 text-xs text-warm-500 flex items-center gap-1">
            <Check className="h-3 w-3" />
            已确认
          </div>
        )}
      </div>
    </div>
  );
}
