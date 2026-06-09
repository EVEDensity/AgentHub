'use client';

import { useState, useCallback, type JSX } from 'react';
import { Check, X } from 'lucide-react';
import { DiffViewer } from './DiffViewer';

interface DiffBubbleProps {
  value?: string;
  /** File path hint for language detection */
  filePath?: string;
  /** Old content for constructing proper diff */
  oldString?: string;
  /** New content for constructing proper diff */
  newString?: string;
  /** Called when user clicks "Accept" */
  onAccept?: () => void;
  /** Called when user clicks "Reject" */
  onReject?: () => void;
  /** Current decision state */
  decisionState?: 'pending' | 'accepted' | 'rejected';
}

export default function DiffBubble({
  value,
  filePath = 'changes.diff',
  oldString,
  newString,
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

  const effectiveDecision = localDecision || (decisionState !== 'pending' ? decisionState : null);
  const showButtons = onAccept && onReject && !submitted;

  // ── Resolve old/new content ────────────────────────────────────────
  // If oldString/newString are provided, use them directly for a proper
  // word-level diff.  Otherwise parse from the diff text value.
  let resolvedOld = oldString || '';
  let resolvedNew = newString || '';

  if (!oldString && !newString && value) {
    // Try extracting old/new from the unified diff text
    const lines = value.split('\n');
    const oldLines: string[] = [];
    const newLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith('-') && !line.startsWith('---')) {
        oldLines.push(line.slice(1));
      } else if (line.startsWith('+') && !line.startsWith('+++')) {
        newLines.push(line.slice(1));
      } else if (line.startsWith(' ') || line.startsWith('@@') || line.startsWith('diff') || line.startsWith('---') || line.startsWith('+++')) {
        // context lines go to both
      }
    }
    resolvedOld = oldLines.join('\n');
    resolvedNew = newLines.join('\n');
  }

  // Fallback: if we only have the diff text, show it in the new DiffViewer
  // by using empty old and full-diff new (handled by DiffViewer's parsing)

  return (
    <div className="mt-2 overflow-hidden rounded-xl border border-warm-150 shadow-card">
      {/* ── Header ────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between border-b border-warm-200 bg-warm-50/70 px-4 py-2.5">
        <div className="text-caption font-semibold tracking-wide text-warm-700">
          代码 / Diff 预览
        </div>
        <div className="flex items-center gap-2">
          {/* Accept / Reject buttons */}
          {showButtons && (
            <div className="flex items-center gap-1.5">
              <button
                onClick={handleAccept}
                className="inline-flex items-center gap-1 rounded-lg border border-emerald-500/40 bg-emerald-500/20 px-2.5 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-500/30 transition"
              >
                <Check className="h-3 w-3" />
                Accept
              </button>
              <button
                onClick={handleReject}
                className="inline-flex items-center gap-1 rounded-lg border border-red-500/40 bg-red-500/20 px-2.5 py-1 text-xs font-medium text-red-700 hover:bg-red-500/30 transition"
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
                  ? 'bg-emerald-500/20 text-emerald-700'
                  : 'bg-red-500/20 text-red-700'
              }`}
            >
              {effectiveDecision === 'accepted' ? 'Accepted ✓' : 'Rejected ✗'}
            </div>
          )}
        </div>
      </div>

      {/* ── Body: proper DiffViewer ───────────────────────────────── */}
      {(resolvedOld || resolvedNew) ? (
        <DiffViewer
          filePath={filePath}
          oldString={resolvedOld}
          newString={resolvedNew || resolvedOld}
          showHeader={false}
          maxHeight="360px"
        />
      ) : value ? (
        <DiffViewer
          filePath={filePath}
          oldString=""
          newString={value}
          showHeader={false}
          maxHeight="360px"
        />
      ) : (
        <div className="flex items-center justify-center py-8 text-sm text-warm-400">
          Diff 数据不可用
        </div>
      )}
    </div>
  );
}
