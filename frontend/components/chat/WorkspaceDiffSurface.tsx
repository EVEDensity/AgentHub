'use client';

import { useState, type JSX } from 'react';
import { inferLanguage } from './DiffViewer';
import InlineHighlightedCode from './InlineHighlightedCode';

// ── Constants ────────────────────────────────────────────────────────

export const WORKSPACE_PREVIEW_LINE_LIMIT = 2000;
export const WORKSPACE_PLAIN_TEXT_LINE_THRESHOLD = 5000;

// ── Diff line parser ─────────────────────────────────────────────────

interface DiffLine {
  type: 'header' | 'hunk' | 'added' | 'removed' | 'context';
  prefix: string;
  content: string;
  lineNum: number;
}

function parseDiffLines(value: string): DiffLine[] {
  const lines = value.split('\n');
  const result: DiffLine[] = [];
  let lineNum = 0;

  for (const line of lines) {
    lineNum++;
    if (line.startsWith('diff --') || line.startsWith('--- ') || line.startsWith('+++ ')) {
      result.push({ type: 'header', prefix: '', content: line, lineNum });
    } else if (line.startsWith('@@')) {
      result.push({ type: 'hunk', prefix: '', content: line, lineNum });
    } else if (line.startsWith('+') && !line.startsWith('+++')) {
      result.push({ type: 'added', prefix: '+', content: line.slice(1), lineNum });
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      result.push({ type: 'removed', prefix: '-', content: line.slice(1), lineNum });
    } else if (line.startsWith(' ')) {
      result.push({ type: 'context', prefix: ' ', content: line.slice(1), lineNum });
    }
  }

  return result;
}

// ── WorkspaceDiffSurface ─────────────────────────────────────────────

interface WorkspaceDiffSurfaceProps {
  /** Raw unified diff text (as from `git diff`) */
  value: string;
  /** File path for language detection */
  path: string;
  className?: string;
  lineLimit?: number;
}

export function WorkspaceDiffSurface({
  value,
  path,
  className = '',
  lineLimit = WORKSPACE_PREVIEW_LINE_LIMIT,
}: WorkspaceDiffSurfaceProps): JSX.Element {
  const [showAllLines, setShowAllLines] = useState(false);
  const language = inferLanguage(path);

  const parsedLines = parseDiffLines(value);
  const totalLines = parsedLines.length;
  const visibleLines = showAllLines ? parsedLines : parsedLines.slice(0, lineLimit);
  const usePlainText = totalLines > WORKSPACE_PLAIN_TEXT_LINE_THRESHOLD;

  return (
    <div className={`workspace-diff-surface ${className}`}>
      <pre className="m-0 font-mono text-xs leading-[1.55]">
        {visibleLines.map((line, i) => {
          const isFileHeader = line.type === 'header';
          const isHunk = line.type === 'hunk';
          const isAdded = line.type === 'added';
          const isRemoved = line.type === 'removed';
          const isCodeLine = isAdded || isRemoved || line.type === 'context';

          return (
            <div
              key={i}
              className={`grid grid-cols-[48px_18px_1fr] gap-2 px-3 ${
                isAdded
                  ? 'bg-[var(--color-diff-added-bg,rgba(16,185,129,0.15))]'
                  : isRemoved
                    ? 'bg-[var(--color-diff-removed-bg,rgba(239,68,68,0.15))]'
                    : isHunk
                      ? 'bg-[var(--color-diff-highlight-bg,rgba(245,158,11,0.12))]'
                      : isFileHeader
                        ? 'bg-[var(--color-surface-container-lowest,#0D0D0D)]'
                        : 'hover:bg-[var(--color-surface-hover,#2A2A2A)]'
              }`}
            >
              {/* Line number */}
              <span className="select-none text-right text-[11px] text-[var(--color-text-tertiary,#5A5A5A)]">
                {line.lineNum}
              </span>

              {/* Change indicator (+, -, space) */}
              <span
                className={`select-none text-center ${
                  isAdded
                    ? 'text-[var(--color-diff-added-text,#10B981)]'
                    : isRemoved
                      ? 'text-[var(--color-diff-removed-text,#EF4444)]'
                      : 'text-[var(--color-text-tertiary,#5A5A5A)]'
                }`}
              >
                {line.prefix}
              </span>

              {/* Code content */}
              <span className={`whitespace-pre pr-6 ${
                isFileHeader
                  ? 'text-[var(--color-text-secondary,#F5A623)] font-semibold'
                  : isHunk
                    ? 'text-[var(--color-text-secondary,#F5A623)]'
                    : ''
              }`}>
                {isCodeLine && !usePlainText ? (
                  <InlineHighlightedCode value={line.content} language={language} />
                ) : (
                  line.content || ' '
                )}
              </span>
            </div>
          );
        })}
      </pre>

      {/* ── "Show all" / "Collapse" footer ────────────────────────── */}
      {totalLines > lineLimit && (
        <div className="sticky bottom-0 flex items-center justify-between border-t border-[var(--color-border,#2D2D2D)] bg-[var(--color-surface-container-lowest,#0D0D0D)] px-4 py-2">
          <span className="text-xs text-[var(--color-text-tertiary,#808080)]">
            显示前 {Math.min(lineLimit, totalLines).toLocaleString()} 行 / 共 {totalLines.toLocaleString()} 行
          </span>
          <button
            className="rounded px-3 py-1 text-xs font-medium text-[var(--color-primary,#3B82F6)] hover:bg-[var(--color-primary-bg,rgba(59,130,246,0.1))] transition-colors"
            onClick={() => setShowAllLines(!showAllLines)}
          >
            {showAllLines ? '折叠' : '显示全部'}
          </button>
        </div>
      )}
    </div>
  );
}

export default WorkspaceDiffSurface;
