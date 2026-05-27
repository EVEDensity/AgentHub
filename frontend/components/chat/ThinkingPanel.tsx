import { useEffect, useRef, useState, type JSX } from 'react';

interface ThinkingPanelProps {
  /** The raw inner content between <think> and </think> tags */
  content: string;
  /** Whether the overall agent response is still streaming */
  isStreaming: boolean;
  /** Whether the closing </think> tag has been received */
  isComplete: boolean;
}

export default function ThinkingPanel({ content, isStreaming, isComplete }: ThinkingPanelProps): JSX.Element {
  const [expanded, setExpanded] = useState(true);
  const prevComplete = useRef(false);

  useEffect(() => {
    // Auto-collapse when </think> first arrives (transition from incomplete → complete)
    if (isComplete && !prevComplete.current) {
      setExpanded(false);
    }
    prevComplete.current = isComplete;
  }, [isComplete]);

  // During active thinking with no closing tag, keep it open
  useEffect(() => {
    if (isStreaming && !isComplete) {
      setExpanded(true);
    }
  }, [isStreaming, isComplete]);

  const charCount = content.length;

  return (
    <div className="thinking-panel mb-3 overflow-hidden rounded-lg border border-warm-200/70 bg-warm-50/80">
      <button
        className="flex w-full items-center gap-1.5 px-3 py-1.5 text-left text-xs transition-colors hover:bg-warm-100/60"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        {(isStreaming && !isComplete) ? (
          <span className="flex items-center gap-1.5 text-warm-400">
            <span aria-hidden="true">💭</span>
            <span className="flex h-2 w-2 items-center justify-center">
              <span className="absolute h-2 w-2 animate-ping rounded-full bg-primary-400/60" />
              <span className="relative h-1.5 w-1.5 rounded-full bg-primary-400" />
            </span>
            <span>【思考分析】</span>
          </span>
        ) : (
          <span className="flex items-center gap-1.5 text-warm-400">
            <span aria-hidden="true">💭</span>
            <span>【思考分析】已完成 ({charCount}字)</span>
          </span>
        )}
        <svg
          className={`ml-auto h-3 w-3 text-warm-300 transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {expanded && (
        <div className="border-t border-warm-100 px-3 py-2">
          <div className="max-h-60 overflow-y-auto text-xs leading-relaxed text-warm-500 whitespace-pre-wrap font-mono opacity-80">
            {content}
          </div>
        </div>
      )}
    </div>
  );
}
