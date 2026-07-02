'use client';

import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued';
import { Highlight, type PrismTheme } from 'prism-react-renderer';
import type { JSX } from 'react';

// ── Language inference from file path ─────────────────────────────────

function inferLanguage(filePath: string): string {
  const ext = filePath.split('.').pop()?.toLowerCase();
  const langMap: Record<string, string> = {
    ts: 'typescript', tsx: 'tsx', js: 'javascript', jsx: 'jsx',
    py: 'python', rs: 'rust', go: 'go', rb: 'ruby',
    json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'toml',
    md: 'markdown', css: 'css', html: 'markup', xml: 'markup',
    sql: 'sql', sh: 'bash', bash: 'bash', zsh: 'bash',
    java: 'java', kt: 'kotlin', swift: 'swift',
    c: 'c', cpp: 'cpp', h: 'c', hpp: 'cpp',
    php: 'php', scss: 'scss', less: 'less',
    graphql: 'graphql', proto: 'protobuf',
    dockerfile: 'dockerfile',
  };
  return langMap[ext ?? ''] || 'text';
}

// ── Syntax highlighting theme (warm, matching project design) ────────

const warmSyntaxTheme: PrismTheme = {
  plain: { color: 'var(--color-code-fg, #D4D4D4)', backgroundColor: 'transparent' },
  styles: [
    { types: ['comment'], style: { color: 'var(--color-code-comment, #6A9955)', fontStyle: 'italic' } },
    { types: ['string'], style: { color: 'var(--color-code-string, #98C379)' } },
    { types: ['keyword'], style: { color: 'var(--color-code-keyword, #569CD6)' } },
    { types: ['function'], style: { color: 'var(--color-code-function, #DCDCAA)' } },
    { types: ['number'], style: { color: 'var(--color-code-number, #D19A66)' } },
    { types: ['property'], style: { color: 'var(--color-code-property, #9CDCFE)' } },
    { types: ['operator'], style: { color: 'var(--color-code-operator, #D4D4D4)' } },
    { types: ['punctuation'], style: { color: 'var(--color-code-punctuation, #808080)' } },
    { types: ['variable'], style: { color: 'var(--color-code-variable, #9CDCFE)' } },
    { types: ['class-name'], style: { color: 'var(--color-code-class, #4EC9B0)' } },
    { types: ['builtin'], style: { color: 'var(--color-code-builtin, #C586C0)' } },
  ],
};

// ── Custom content renderer (syntax highlighted) ────────────────────

function highlightSyntax(str: string, language: string): JSX.Element {
  return (
    <Highlight theme={warmSyntaxTheme} code={str} language={language}>
      {({ tokens, getTokenProps }) => (
        <>
          {tokens.map((line, i) => (
            <span key={i}>
              {line.map((token, key) => (
                <span key={key} {...getTokenProps({ token })} />
              ))}
            </span>
          ))}
        </>
      )}
    </Highlight>
  );
}

// ── Diff styles (CSS variable-driven, light/dark compatible) ────────

const diffStyles = {
  variables: {
    light: {
      diffViewerBackground: 'var(--color-diff-bg)',
      addedBackground: 'var(--color-diff-added-bg)',
      removedBackground: 'var(--color-diff-removed-bg)',
      wordAddedBackground: 'var(--color-diff-added-word)',
      wordRemovedBackground: 'var(--color-diff-removed-word)',
      addedGutterBackground: 'var(--color-diff-added-gutter)',
      removedGutterBackground: 'var(--color-diff-removed-gutter)',
      gutterBackground: 'var(--color-diff-gutter-bg)',
      highlightBackground: 'var(--color-diff-highlight-bg)',
    },
  },
  diffContainer: { fontSize: '12px', lineHeight: '1.45' },
  gutter: { padding: '1px 8px', minWidth: '40px', fontSize: '11px' },
  wordDiff: { padding: '1px 2px', borderRadius: '2px' },
};

// ── DiffViewer component ────────────────────────────────────────────

export interface DiffViewerProps {
  filePath: string;
  oldString: string;
  newString: string;
  /** Show in unified (single-pane) or split (side-by-side) view */
  splitView?: boolean;
  /** Max height for the diff body (with overflow scroll) */
  maxHeight?: string;
  /** Show the file header bar with +N / -N stats */
  showHeader?: boolean;
}

export function DiffViewer({
  filePath,
  oldString,
  newString,
  splitView = false,
  maxHeight = '400px',
  showHeader = true,
}: DiffViewerProps): JSX.Element {
  const language = inferLanguage(filePath);

  // ── Compute +/- stats ──────────────────────────────────────────
  const oldLines = oldString.split('\n');
  const newLines = newString.split('\n');
  const additions = newLines.filter((l, i) => l !== (oldLines[i] ?? null)).length;
  const deletions = oldLines.filter((l, i) => l !== (newLines[i] ?? null)).length;

  return (
    <div className="diff-viewer overflow-hidden rounded-lg border bg-[var(--color-surface-container-low,#1E1E1E)]">
      {/* ── Header ────────────────────────────────────────────── */}
      {showHeader && (
        <div className="flex items-center justify-between border-b border-[var(--color-border,#2D2D2D)] px-3 py-1.5">
          <div className="min-w-0">
            <div className="font-mono text-[11px] text-[var(--color-text-tertiary,#808080)] truncate">
              {filePath}
            </div>
            <div className="mt-1 flex items-center gap-2 text-[10px] uppercase tracking-wide">
              <span className="inline-flex items-center gap-1 rounded-full bg-[var(--color-diff-added-bg)] px-2 py-0.5 text-[var(--color-diff-added-text,#10B981)] font-medium">
                <svg className="h-2.5 w-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round">
                  <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
                </svg>
                {additions}
              </span>
              <span className="inline-flex items-center gap-1 rounded-full bg-[var(--color-diff-removed-bg)] px-2 py-0.5 text-[var(--color-diff-removed-text,#EF4444)] font-medium">
                <svg className="h-2.5 w-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round">
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
                {deletions}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* ── Diff Body ─────────────────────────────────────────── */}
      <div style={{ maxHeight, overflow: 'auto' }}>
        <ReactDiffViewer
          oldValue={oldString}
          newValue={newString}
          splitView={splitView}
          compareMethod={DiffMethod.WORDS}
          renderContent={(str: string) => highlightSyntax(str, language)}
          hideLineNumbers={false}
          styles={diffStyles}
          useDarkTheme={false}
        />
      </div>
    </div>
  );
}

export default DiffViewer;
export { inferLanguage };
