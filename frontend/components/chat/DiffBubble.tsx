'use client';

import { useState, useCallback, type JSX } from 'react';
import MonacoEditor from '@monaco-editor/react';
import { Check, X } from 'lucide-react';

interface DiffBubbleProps {
  value?: string;
  /** Called when user clicks "Accept" */
  onAccept?: () => void;
  /** Called when user clicks "Reject" */
  onReject?: () => void;
  /** Current decision state */
  decisionState?: 'pending' | 'accepted' | 'rejected';
}

export default function DiffBubble({
  value,
  onAccept,
  onReject,
  decisionState = 'pending',
}: DiffBubbleProps): JSX.Element {
  const [submitted, setSubmitted] = useState(false);
  const [localDecision, setLocalDecision] = useState<'accepted' | 'rejected' | null>(null);

  const handleAccept = useCallback(() => {
    if (submitted) return;
    setSubmitted(true);
    setLocalDecision('accepted');
    onAccept?.();
  }, [submitted, onAccept]);

  const handleReject = useCallback(() => {
    if (submitted) return;
    setSubmitted(true);
    setLocalDecision('rejected');
    onReject?.();
  }, [submitted, onReject]);

  const language = value?.startsWith('diff') ? 'diff' : 'python';
  const effectiveDecision = localDecision || (decisionState !== 'pending' ? decisionState : null);
  const showButtons = onAccept && onReject && !submitted;

  return (
    <div className="mt-2 overflow-hidden rounded-xl border border-warm-150 bg-warm-900 shadow-card">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-warm-700/40 bg-warm-800/70 px-4 py-2.5">
        <div className="text-caption font-semibold tracking-wide text-warm-100">
          代码 / Diff 预览
        </div>
        <div className="flex items-center gap-2">
          {/* Accept / Reject buttons */}
          {showButtons && (
            <div className="flex items-center gap-1.5">
              <button
                onClick={handleAccept}
                className="inline-flex items-center gap-1 rounded-lg border border-emerald-500/40 bg-emerald-500/20 px-2.5 py-1 text-xs font-medium text-emerald-400 hover:bg-emerald-500/30 transition"
              >
                <Check className="h-3 w-3" />
                Accept
              </button>
              <button
                onClick={handleReject}
                className="inline-flex items-center gap-1 rounded-lg border border-red-500/40 bg-red-500/20 px-2.5 py-1 text-xs font-medium text-red-400 hover:bg-red-500/30 transition"
              >
                <X className="h-3 w-3" />
                Reject
              </button>
            </div>
          )}

          {/* Decision state badge */}
          {effectiveDecision && (
            <div
              className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                effectiveDecision === 'accepted'
                  ? 'bg-emerald-500/20 text-emerald-400'
                  : 'bg-red-500/20 text-red-400'
              }`}
            >
              {effectiveDecision === 'accepted' ? 'Accepted ✓' : 'Rejected ✗'}
            </div>
          )}

          {/* Language badge */}
          <div className="rounded-full border border-warm-600/60 bg-warm-700/40 px-2 py-0.5 text-[11px] text-warm-200">
            {language.toUpperCase()}
          </div>
        </div>
      </div>

      {/* Monaco Editor */}
      <MonacoEditor
        height="360px"
        language={language}
        theme="vs-dark"
        value={value || ''}
        options={{
          readOnly: true,
          minimap: { enabled: false },
          fontSize: 14,
          lineHeight: 22,
          scrollBeyondLastLine: false,
          wordWrap: 'on',
          padding: { top: 14, bottom: 14 },
          smoothScrolling: true,
        }}
      />
    </div>
  );
}
