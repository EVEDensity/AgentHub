'use client';

import { useState, type JSX } from 'react';
import { HelpCircle, Check, MessageSquare } from 'lucide-react';
import type { AgentQuestionEvent, AgentQuestionOption } from '../../types';

interface AgentQuestionBubbleProps {
  data: AgentQuestionEvent;
  isStreaming?: boolean;
  onSendEvent: (event: Record<string, unknown>) => void;
}

export default function AgentQuestionBubble({ data, isStreaming, onSendEvent }: AgentQuestionBubbleProps): JSX.Element {
  const [selected, setSelected] = useState<string | null>(null);
  const [customAnswer, setCustomAnswer] = useState('');
  const [showCustom, setShowCustom] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  function handleSelectOption(option: AgentQuestionOption) {
    if (submitted) return;
    setSelected(option.id);
    // Auto-submit if there's only one clear action path
    onSendEvent({
      event: 'agent_question_response',
      sessionId: data.sessionId,
      messageId: data.messageId,
      questionMessageId: data.messageId,
      selectedOptionId: option.id,
    });
    setSubmitted(true);
  }

  function handleCustomSubmit() {
    if (!customAnswer.trim() || submitted) return;
    onSendEvent({
      event: 'agent_question_response',
      sessionId: data.sessionId,
      messageId: data.messageId,
      questionMessageId: data.messageId,
      customAnswer: customAnswer.trim(),
    });
    setSubmitted(true);
  }

  return (
    <div className="mb-4 flex justify-start">
      <div className="max-w-[85%] rounded-2xl px-4 py-3 bg-gradient-to-br from-indigo-50 to-blue-50 border border-indigo-200 shadow-sm">
        {/* Header */}
        <div className="mb-2 flex items-center gap-2 text-xs opacity-80">
          <HelpCircle className="h-3.5 w-3.5 text-indigo-500" />
          <span className="font-semibold text-indigo-700">{data.agentId || 'PM'}</span>
          <span className="rounded px-2 py-0.5 bg-indigo-100 text-indigo-600 text-xs font-medium">
            需要确认
          </span>
          {isStreaming && <span className="inline-block h-3 w-0.5 animate-pulse bg-indigo-500" />}
        </div>

        {/* Question text */}
        <div className="mb-3 text-sm text-warm-800 leading-relaxed whitespace-pre-wrap">
          {data.question}
        </div>

        {/* Options */}
        <div className="flex flex-wrap gap-2">
          {data.options.map((opt) => (
            <button
              key={opt.id}
              type="button"
              disabled={submitted}
              onClick={() => handleSelectOption(opt)}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition-all ${
                submitted && selected === opt.id
                  ? 'bg-indigo-500 text-white shadow-sm'
                  : submitted
                    ? 'bg-warm-100 text-warm-400 cursor-not-allowed'
                    : 'bg-white border border-indigo-200 text-indigo-700 hover:bg-indigo-50 hover:border-indigo-400 active:scale-[0.97] shadow-sm'
              }`}
              title={opt.description}
            >
              {submitted && selected === opt.id && <Check className="h-3.5 w-3.5" />}
              {opt.label}
            </button>
          ))}

          {/* Custom answer toggle */}
          {data.allowCustomAnswer && !submitted && (
            <button
              type="button"
              onClick={() => setShowCustom(!showCustom)}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition-all ${
                showCustom
                  ? 'bg-warm-100 text-warm-600 border border-warm-200'
                  : 'bg-white border border-dashed border-warm-300 text-warm-500 hover:border-warm-400'
              }`}
            >
              <MessageSquare className="h-3.5 w-3.5" />
              自定义回答
            </button>
          )}
        </div>

        {/* Custom answer input */}
        {showCustom && !submitted && (
          <div className="mt-3 flex gap-2">
            <input
              type="text"
              value={customAnswer}
              onChange={(e) => setCustomAnswer(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleCustomSubmit(); }}
              placeholder="输入你的回答..."
              className="flex-1 rounded-lg border border-warm-200 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400/60"
              autoFocus
            />
            <button
              type="button"
              onClick={handleCustomSubmit}
              disabled={!customAnswer.trim()}
              className="rounded-lg bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              发送
            </button>
          </div>
        )}

        {/* Submitted confirmation */}
        {submitted && (
          <div className="mt-2 text-xs text-indigo-500 flex items-center gap-1">
            <Check className="h-3 w-3" />
            已回复
          </div>
        )}
      </div>
    </div>
  );
}
