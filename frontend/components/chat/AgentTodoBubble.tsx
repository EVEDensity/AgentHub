'use client';

import { memo, useState, type JSX } from 'react';
import { ClipboardList, Check, X, Edit3 } from 'lucide-react';
import type { AgentTodoEvent, AgentTodoAction } from '../../types';

interface AgentTodoBubbleProps {
  data: AgentTodoEvent;
  isStreaming?: boolean;
  onSendEvent: (event: Record<string, unknown>) => void;
}

const PRIORITY_CONFIG: Record<string, { badge: string; border: string }> = {
  high: { badge: 'bg-red-100 text-red-600', border: 'border-l-red-400' },
  medium: { badge: 'bg-yellow-100 text-yellow-600', border: 'border-l-yellow-400' },
  low: { badge: 'bg-blue-100 text-blue-500', border: 'border-l-blue-300' },
};

const PRIORITY_LABELS: Record<string, string> = {
  high: '高优先级',
  medium: '中优先级',
  low: '低优先级',
};

const AgentTodoBubble = memo(function AgentTodoBubble({ data, isStreaming, onSendEvent }: AgentTodoBubbleProps): JSX.Element {
  const [selected, setSelected] = useState<string | null>(null);
  const [submittedLocal, setSubmittedLocal] = useState(false);
  const [comment, setComment] = useState('');
  const [showComment, setShowComment] = useState(false);
  // Shared state: derived from interaction_already_resolved broadcast or local click
  const submitted = submittedLocal || !!data.resolvedBy;
  const resolvedByName = data.resolvedByName || '';
  const pc = PRIORITY_CONFIG[data.priority] || PRIORITY_CONFIG.medium;

  function handleAction(action: AgentTodoAction) {
    if (submitted) return;
    setSelected(action.id);
    if (action.intent === 'modify' && !showComment) {
      setShowComment(true);
      return;
    }
    setSubmittedLocal(true);
    onSendEvent({
      event: 'agent_todo_response',
      sessionId: data.sessionId,
      todoMessageId: data.messageId,
      selectedActionId: action.id,
      comment: comment.trim() || undefined,
    });
  }

  function handleSubmitWithComment() {
    if (submitted) return;
    setSubmittedLocal(true);
    onSendEvent({
      event: 'agent_todo_response',
      sessionId: data.sessionId,
      todoMessageId: data.messageId,
      selectedActionId: selected || '',
      comment: comment.trim() || undefined,
    });
  }

  return (
    <div className="mb-4 flex justify-start">
      <div className={`max-w-[85%] rounded-2xl px-4 py-3 bg-warm-100 border shadow-sm border-l-4 ${pc.border}`}>
        {/* Header */}
        <div className="mb-2 flex items-center gap-2 text-xs opacity-90">
          <ClipboardList className="h-3.5 w-3.5 text-warm-500" />
          <span className="font-semibold text-warm-700">{data.agentId || 'PM'}</span>
          <span className="rounded px-2 py-0.5 text-xs font-medium bg-purple-100 text-purple-600">
            待你决策
          </span>
          <span className={`rounded px-2 py-0.5 text-xs font-medium ${pc.badge}`}>
            {PRIORITY_LABELS[data.priority] || data.priority}
          </span>
          {isStreaming && <span className="inline-block h-3 w-0.5 animate-pulse bg-purple-500" />}
        </div>

        {/* Title */}
        <div className="mb-1 text-sm font-semibold text-warm-800">
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
              disabled={submitted && selected !== action.id}
              onClick={() => handleAction(action)}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition-all ${
                submitted && selected === action.id
                  ? action.intent === 'approve'
                    ? 'bg-emerald-500 text-white shadow-sm'
                    : action.intent === 'reject'
                      ? 'bg-red-500 text-white shadow-sm'
                      : 'bg-amber-500 text-white shadow-sm'
                  : submitted
                    ? 'bg-warm-100 text-warm-400 cursor-not-allowed'
                    : action.intent === 'approve'
                      ? 'bg-emerald-50 border border-emerald-300 text-emerald-700 hover:bg-emerald-100 active:scale-[0.97]'
                      : action.intent === 'reject'
                        ? 'bg-red-50 border border-red-200 text-red-600 hover:bg-red-100 active:scale-[0.97]'
                        : 'bg-amber-50 border border-amber-200 text-amber-700 hover:bg-amber-100 active:scale-[0.97]'
              }`}
            >
              {action.intent === 'approve' && <Check className="h-3.5 w-3.5" />}
              {action.intent === 'reject' && <X className="h-3.5 w-3.5" />}
              {action.intent === 'modify' && <Edit3 className="h-3.5 w-3.5" />}
              {action.label}
            </button>
          ))}
        </div>

        {/* Comment input for modify */}
        {showComment && !submitted && (
          <div className="mt-3 flex gap-2">
            <input
              type="text"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleSubmitWithComment(); }}
              placeholder="添加备注（可选）..."
              className="flex-1 rounded-lg border border-warm-200 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-purple-400/60"
              autoFocus
            />
            <button
              type="button"
              onClick={handleSubmitWithComment}
              className="rounded-lg bg-purple-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-purple-600 transition-colors"
            >
              确认
            </button>
          </div>
        )}

        {/* Confirmed state — shared across users */}
        {submitted && (
          <div className={`mt-2 text-xs flex items-center gap-1 ${resolvedByName ? 'text-amber-600' : 'text-warm-500'}`}>
            <Check className="h-3 w-3" />
            {resolvedByName ? `已由 ${resolvedByName} 处理` : '已处理'}
          </div>
        )}
      </div>
    </div>
  );
});

export default AgentTodoBubble;
