import { memo, useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react';
import type { FileReference, WorkspacePreviewTab } from '../../types';
import MarkdownRenderer from './MarkdownRenderer';
import ResizableDivider from '../common/ResizableDivider';
import { useResizableSize } from '../../hooks/useResizableSize';
import { lineHasReference } from '../../lib/references';

// ══════════════════════════════════════════════════════════════════════════════
// Language detection from file path
// ══════════════════════════════════════════════════════════════════════════════

const EXT_LANG: Record<string, string> = {
  ts: 'typescript', tsx: 'tsx', js: 'javascript', jsx: 'jsx',
  py: 'python', pyi: 'python',
  rs: 'rust', go: 'go', java: 'java', kt: 'kotlin', swift: 'swift',
  c: 'c', cpp: 'cpp', cc: 'cpp', cxx: 'cpp', h: 'c', hpp: 'cpp',
  cs: 'csharp', fs: 'fsharp', vb: 'vb',
  rb: 'ruby', php: 'php', pl: 'perl', sh: 'bash', bash: 'bash',
  zsh: 'bash', ps1: 'powershell', bat: 'batch', cmd: 'batch',
  sql: 'sql', graphql: 'graphql', gql: 'graphql',
  html: 'html', htm: 'html', css: 'css', scss: 'scss', less: 'less',
  json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'toml', ini: 'ini',
  md: 'markdown', mdx: 'mdx', txt: 'text', cfg: 'ini', conf: 'ini',
  dockerfile: 'dockerfile', env: 'bash', lock: 'text',
  vue: 'vue', svelte: 'svelte', astro: 'astro',
  tf: 'hcl', tfvars: 'hcl', proto: 'protobuf',
};

function getLanguageFromPath(path: string): string {
  const lower = path.toLowerCase();
  const base = lower.split('/').pop() || lower;
  if (base === 'dockerfile') return 'dockerfile';
  if (base === 'makefile') return 'makefile';
  if (base.startsWith('.')) {
    const dot = base.slice(1);
    if (dot in EXT_LANG) return EXT_LANG[dot];
  }
  const parts = base.split('.');
  if (parts.length >= 2) {
    for (let i = parts.length - 1; i >= 0; i--) {
      const ext = parts.slice(i).join('.');
      if (ext in EXT_LANG) return EXT_LANG[ext];
    }
  }
  return 'text';
}

function isMarkdownFile(path: string): boolean {
  const lang = getLanguageFromPath(path);
  return lang === 'markdown' || lang === 'mdx';
}

function isImageFile(path: string): boolean {
  const ext = path.split('.').pop()?.toLowerCase() || '';
  return ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp', 'ico'].includes(ext);
}

// ══════════════════════════════════════════════════════════════════════════════
// Diff parser & renderer
// ══════════════════════════════════════════════════════════════════════════════

interface DiffLine {
  type: 'header' | 'hunk' | 'added' | 'removed' | 'context' | 'normal';
  prefix: string;
  content: string;
  lineNum: number;
}

function parseDiff(diffText: string): DiffLine[] {
  const lines = diffText.split('\n');
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
    } else {
      result.push({ type: 'normal', prefix: '', content: line, lineNum });
    }
  }

  return result;
}

// ══════════════════════════════════════════════════════════════════════════════
// Inline syntax highlighter (keyword-aware HTML output)
// ══════════════════════════════════════════════════════════════════════════════

const PY_KEYWORDS = new Set([
  'def', 'class', 'import', 'from', 'return', 'if', 'elif', 'else',
  'try', 'except', 'finally', 'raise', 'with', 'as', 'for', 'while',
  'break', 'continue', 'pass', 'yield', 'lambda', 'async', 'await',
  'and', 'or', 'not', 'in', 'is', 'None', 'True', 'False', 'self',
  'assert', 'del', 'global', 'nonlocal',
]);

const JS_KEYWORDS = new Set([
  'const', 'let', 'var', 'function', 'return', 'if', 'else', 'for',
  'while', 'do', 'switch', 'case', 'break', 'continue', 'try', 'catch',
  'finally', 'throw', 'new', 'delete', 'typeof', 'instanceof', 'in',
  'of', 'class', 'extends', 'super', 'import', 'export', 'default',
  'from', 'as', 'async', 'await', 'this', 'true', 'false', 'null',
  'undefined', 'void', 'yield', 'static', 'get', 'set',
]);

function highlightCodeLine(line: string, lang: string): string {
  let escaped = line
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  if (lang === 'python') {
    escaped = escaped.replace(/'[^']*'/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/"[^"]*"/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/(#.*)$/, '<span style="color:#6A9955;font-style:italic">$1</span>');
    escaped = escaped.replace(
      /\b([a-zA-Z_]\w*)\b/g,
      (word) => PY_KEYWORDS.has(word)
        ? '<span style="color:#569CD6;font-weight:500">' + word + '</span>'
        : word,
    );
    escaped = escaped.replace(/\b(\d+\.?\d*)\b/g, '<span style="color:#D19A66">$1</span>');
    return escaped;
  }

  if (['typescript', 'tsx', 'javascript', 'jsx', 'java', 'c', 'cpp', 'csharp', 'go', 'rust', 'swift', 'kotlin', 'php'].includes(lang)) {
    escaped = escaped.replace(/'[^']*'/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/"[^"]*"/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/(\/\/.*)$/, '<span style="color:#6A9955;font-style:italic">$1</span>');
    escaped = escaped.replace(
      /\b([a-zA-Z_$]\w*)\b/g,
      (word) => JS_KEYWORDS.has(word)
        ? '<span style="color:#569CD6;font-weight:500">' + word + '</span>'
        : word,
    );
    escaped = escaped.replace(/\b(\d+\.?\d*)\b/g, '<span style="color:#D19A66">$1</span>');
    return escaped;
  }

  if (lang === 'css' || lang === 'scss' || lang === 'less') {
    escaped = escaped.replace(/"[^"]*"/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/'[^']*'/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/(\/\*[\s\S]*?\*\/)/g, '<span style="color:#6A9955;font-style:italic">$1</span>');
    return escaped;
  }

  if (lang === 'bash' || lang === 'dockerfile' || lang === 'makefile') {
    escaped = escaped.replace(/(#.*)$/, '<span style="color:#6A9955;font-style:italic">$1</span>');
    escaped = escaped.replace(/"[^"]*"/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/'[^']*'/g, '<span style="color:#98C379">$&</span>');
    return escaped;
  }

  if (lang === 'sql') {
    escaped = escaped.replace(/(--.*)$/, '<span style="color:#6A9955;font-style:italic">$1</span>');
    escaped = escaped.replace(/'[^']*'/g, '<span style="color:#98C379">$&</span>');
    return escaped;
  }

  escaped = escaped.replace(/"[^"]*"/g, '<span style="color:#98C379">$&</span>');
  escaped = escaped.replace(/'[^']*'/g, '<span style="color:#98C379">$&</span>');
  return escaped;
}

// ══════════════════════════════════════════════════════════════════════════════
// Code Preview (inline syntax highlighting + line numbers)
// ══════════════════════════════════════════════════════════════════════════════

function CodePreview({ content, language, maxLines = 5000, references }: {
  content: string;
  language: string;
  maxLines?: number;
  references?: FileReference[];
}): JSX.Element {
  const lines = useMemo(() => content.split('\n'), [content]);
  const [showAll, setShowAll] = useState(false);
  const visibleLines = showAll ? lines : lines.slice(0, maxLines);
  const truncated = lines.length > maxLines && !showAll;

  return (
    <div className="code-preview font-mono text-sm leading-relaxed bg-[#1E1E1E]">
      <table className="w-full border-collapse">
        <tbody>
          {visibleLines.map((line, i) => {
            const lineNumber = i + 1;
            const hitRef = lineHasReference(references, lineNumber);
            const baseBg = i % 2 === 0 ? '#1E1E1E' : '#1A1A1A';
            const rowBg = hitRef ? 'rgba(245, 158, 11, 0.12)' : baseBg;
            return (
              <tr
                key={i}
                className="hover:bg-[#2A2A2A] transition-colors"
                data-line-number={lineNumber}
                id={hitRef ? `code-ref-${hitRef.id}` : undefined}
                data-reference-id={hitRef?.id}
                style={{ background: rowBg }}
              >
                <td className="select-none text-right text-[#5A5A5A] border-r border-[#2D2D2D] bg-[#161616] px-3 py-0 w-[1%] whitespace-nowrap align-top">
                  {lineNumber}
                </td>
                <td className="px-4 py-0 align-top">
                  <pre
                    className="my-0 text-[#D4D4D4] whitespace-pre"
                    style={{ fontFamily: "'JetBrains Mono', 'Cascadia Code', 'Consolas', 'Fira Code', monospace", tabSize: 4 }}
                    dangerouslySetInnerHTML={{ __html: highlightCodeLine(line, language) }}
                  />
                </td>
              </tr>
            );
          })}
          {truncated && (
            <tr>
              <td className="select-none text-center text-[#6A6A6A] border-r border-[#2D2D2D] bg-[#161616] px-3 py-2" style={{ fontFamily: 'monospace' }}>
                …
              </td>
              <td className="px-4 py-2 text-[#808080] italic text-sm">
                仅显示前 {maxLines.toLocaleString()} 行，共 {lines.length.toLocaleString()} 行
                <button
                  className="ml-3 text-[#3B82F6] hover:underline not-italic font-medium"
                  onClick={() => setShowAll(true)}
                >
                  显示全部
                </button>
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Diff Preview
// ══════════════════════════════════════════════════════════════════════════════

function DiffPreview({ diffText, language, references }: {
  diffText: string;
  language: string;
  references?: FileReference[];
}): JSX.Element {
  const diffLines = useMemo(() => parseDiff(diffText), [diffText]);
  const [showAll, setShowAll] = useState(false);
  const maxLines = 5000;
  const visibleLines = showAll ? diffLines : diffLines.slice(0, maxLines);
  const truncated = diffLines.length > maxLines && !showAll;

  return (
    <div className="diff-preview font-mono text-sm leading-relaxed bg-[#1E1E1E]">
      <div>
        {visibleLines.map((line, i) => {
          const isHeader = line.type === 'header';
          const isHunk = line.type === 'hunk';
          const isAdded = line.type === 'added';
          const isRemoved = line.type === 'removed';

          let bgColor = 'transparent';
          if (isAdded) bgColor = 'rgba(16, 185, 129, 0.15)';
          else if (isRemoved) bgColor = 'rgba(239, 68, 68, 0.15)';
          else if (isHunk) bgColor = 'rgba(245, 158, 11, 0.12)';

          // 引用高亮：仅 added/context 行能匹配上（removed 行没"新"行号）
          const hitRef =
            !isHeader && !isRemoved && line.lineNum != null
              ? lineHasReference(references, line.lineNum)
              : undefined;
          if (hitRef) {
            // 引用高亮叠在原有 diff 颜色之上，用一个 ring 边框表示
            bgColor = 'rgba(245, 158, 11, 0.22)';
          }

          return (
            <div
              key={i}
              className="grid gap-2 px-3 hover:brightness-110 transition-colors"
              style={{
                gridTemplateColumns: '48px 18px 1fr',
                background: bgColor,
              }}
              data-line-number={!isHeader && !isRemoved ? line.lineNum ?? undefined : undefined}
              id={hitRef ? `diff-ref-${hitRef.id}` : undefined}
              data-reference-id={hitRef?.id}
            >
              <span className="select-none text-right text-[#5A5A5A]">{line.lineNum}</span>
              <span
                className={`select-none ${
                  isAdded ? 'text-[#10B981]' : isRemoved ? 'text-[#EF4444]' : 'text-[#5A5A5A]'
                }`}
              >
                {line.prefix}
              </span>
              <span className="whitespace-pre text-[#D4D4D4]">
                {isHeader ? (
                  <span style={{ color: '#F5A623', fontWeight: 600 }}>{line.content}</span>
                ) : isHunk ? (
                  <span style={{ color: '#F5A623' }}>{line.content}</span>
                ) : (
                  <span dangerouslySetInnerHTML={{ __html: highlightCodeLine(line.content, language) }} />
                )}
              </span>
            </div>
          );
        })}
        {truncated && (
          <div className="px-3 py-2 text-[#808080] italic text-sm bg-[#161616] border-t border-[#2D2D2D]">
            仅显示前 {maxLines.toLocaleString()} 行，共 {diffLines.length.toLocaleString()} 行
            <button
              className="ml-3 text-[#3B82F6] hover:underline not-italic font-medium"
              onClick={() => setShowAll(true)}
            >
              显示全部
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Selection Popover — floating "Add to chat" button
// ══════════════════════════════════════════════════════════════════════════════

function SelectionPopover({
  position,
  onAdd,
  onClose,
}: {
  position: { x: number; y: number } | null;
  onAdd: () => void;
  onClose: () => void;
}): JSX.Element | null {
  if (!position) return null;

  return (
    <div
      className="fixed z-50 inline-flex items-center rounded-full border border-warm-200 bg-white shadow-lg hover:shadow-xl transition-shadow"
      style={{
        left: Math.min(position.x, window.innerWidth - 220),
        top: Math.max(position.y - 48, 8),
      }}
    >
      <button
        onClick={onAdd}
        className="flex items-center gap-2 rounded-full px-4 py-2 text-sm font-semibold text-warm-700 hover:bg-primary-50 hover:text-primary-600 transition-colors"
      >
        <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
        添加到对话
      </button>
      <button
        onClick={onClose}
        className="mr-1.5 text-warm-400 hover:text-warm-600 p-1 rounded-full hover:bg-warm-100 transition-colors"
      >
        <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// File status icon
// ══════════════════════════════════════════════════════════════════════════════

function StatusIcon({ status }: { status?: string }): JSX.Element | null {
  const cls = 'h-3.5 w-3.5 shrink-0';
  switch (status) {
    case 'added':
      return (
        <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="#10B981" strokeWidth="2" strokeLinecap="round">
          <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
        </svg>
      );
    case 'modified':
      return (
        <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="#F59E0B" strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="3" />
        </svg>
      );
    case 'deleted':
      return (
        <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="#EF4444" strokeWidth="2" strokeLinecap="round">
          <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      );
    case 'untracked':
      return (
        <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="#94A3B8" strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="9" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
      );
    default:
      return null;
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// File icon (by language)
// ══════════════════════════════════════════════════════════════════════════════

function FileTypeIcon({ language }: { language: string }): JSX.Element {
  const cls = 'h-4 w-4 shrink-0 text-warm-400';
  if (language === 'python') {
    return (
      <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="#306998" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M8 6h8v2l-2 1 2 1v2H8" />
      </svg>
    );
  }
  if (language === 'typescript' || language === 'tsx') {
    return <span className="text-[11px] font-bold text-[#3178C6] w-4 text-center">TS</span>;
  }
  if (language === 'javascript' || language === 'jsx') {
    return <span className="text-[11px] font-bold text-[#BBA400] w-4 text-center">JS</span>;
  }
  if (language === 'markdown') {
    return <span className="text-[11px] font-bold text-warm-500 w-4 text-center">MD</span>;
  }
  if (language === 'json') {
    return <span className="text-[10px] font-bold text-warm-500 w-4 text-center">{'{ }'}</span>;
  }
  if (language === 'html') {
    return <span className="text-[11px] font-bold text-[#EA580C] w-4 text-center">&lt;/</span>;
  }
  if (language === 'css' || language === 'scss') {
    return <span className="text-[11px] font-bold text-[#2563EB] w-4 text-center">#</span>;
  }
  return (
    <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Workspace File Tree
// ══════════════════════════════════════════════════════════════════════════════

interface FileTreeNode {
  name: string;
  path: string;
  isDirectory: boolean;
  size?: number;
  language?: string;
  children?: FileTreeNode[];
  loaded?: boolean;
}

function WorkspaceFileTree({
  onOpenFile,
  width,
}: {
  onOpenFile: (path: string) => void;
  width: number;
}): JSX.Element {
  const [tree, setTree] = useState<FileTreeNode[]>([]);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<{ ok: number; fail: number; msg?: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const fetchDir = useCallback(async (subdir: string) => {
    setLoading(true);
    setError(null);
    try {
      const token = localStorage.getItem('agenthub_token') || '';
      const url = subdir
        ? `/api/files/workspace/list?subdir=${encodeURIComponent(subdir)}`
        : '/api/files/workspace/list';
      const res = await fetch(url, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || 'Failed to load');
        return null;
      }
      return data;
    } catch (e) {
      setError(String(e));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  // Upload file to workspace
  const handleUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    setUploading(true);
    setUploadStatus(null);
    const token = localStorage.getItem('agenthub_token') || '';
    let successCount = 0;
    let failCount = 0;
    const errors: string[] = [];

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      try {
        const formData = new FormData();
        formData.append('file', file);

        const res = await fetch('/api/files/workspace/upload', {
          method: 'POST',
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          body: formData,
        });

        if (res.ok) {
          successCount++;
        } else {
          failCount++;
          let detail = '';
          try {
            const errData = await res.json();
            detail = errData.detail || '';
          } catch { /* ignore parse errors */ }
          const msg = `${file.name}: ${res.status} ${detail || res.statusText}`;
          errors.push(msg);
          console.error('[WorkspaceUpload]', msg);
        }
      } catch (err) {
        failCount++;
        const msg = `${file.name}: ${String(err)}`;
        errors.push(msg);
        console.error('[WorkspaceUpload]', msg);
      }
    }

    setUploading(false);
    setUploadStatus({
      ok: successCount,
      fail: failCount,
      msg: errors.length > 0 ? errors.slice(0, 3).join('; ') : undefined,
    });

    // Auto-clear status after 6s
    if (failCount === 0) {
      setTimeout(() => setUploadStatus(null), 6000);
    }

    // Reset file input so the same file can be re-uploaded
    if (fileInputRef.current) fileInputRef.current.value = '';

    if (successCount > 0) {
      // Refresh the tree
      setTree([]);
      setExpandedDirs(new Set());
      const data = await fetchDir('');
      if (data?.files || data?.dirs) {
        const nodes: FileTreeNode[] = [
          ...(data.dirs || []).map((d: Record<string, unknown>) => ({
            name: d.name as string, path: d.path as string, isDirectory: true, children: [], loaded: false,
          })),
          ...(data.files || []).map((f: Record<string, unknown>) => ({
            name: f.name as string, path: f.path as string, isDirectory: false, size: f.size as number, language: f.language as string,
          })),
        ];
        setTree(nodes);
      }
    }
  }, [fetchDir]);

  // Load root on mount
  useEffect(() => {
    fetchDir('').then((data) => {
      if (data?.files || data?.dirs) {
        const nodes: FileTreeNode[] = [
          ...(data.dirs || []).map((d: Record<string, unknown>) => ({
            name: d.name as string,
            path: d.path as string,
            isDirectory: true,
            children: [],
            loaded: false,
          })),
          ...(data.files || []).map((f: Record<string, unknown>) => ({
            name: f.name as string,
            path: f.path as string,
            isDirectory: false,
            size: f.size as number,
            language: f.language as string,
          })),
        ];
        setTree(nodes);
      }
    });
  }, [fetchDir]);

  const handleToggleDir = useCallback(async (node: FileTreeNode) => {
    if (expandedDirs.has(node.path)) {
      setExpandedDirs((prev) => { const n = new Set(prev); n.delete(node.path); return n; });
      return;
    }
    setExpandedDirs((prev) => { const n = new Set(prev); n.add(node.path); return n; });

    if (!node.loaded) {
      const data = await fetchDir(node.path);
      if (data) {
        const children: FileTreeNode[] = [
          ...(data.dirs || []).map((d: Record<string, unknown>) => ({
            name: d.name as string,
            path: d.path as string,
            isDirectory: true,
            children: [],
            loaded: false,
          })),
          ...(data.files || []).map((f: Record<string, unknown>) => ({
            name: f.name as string,
            path: f.path as string,
            isDirectory: false,
            size: f.size as number,
            language: f.language as string,
          })),
        ];
        setTree((prev) => {
          const update = (nodes: FileTreeNode[]): FileTreeNode[] =>
            nodes.map((n) => (n.path === node.path ? { ...n, children, loaded: true } : { ...n, children: update(n.children || []) }));
          return update(prev);
        });
      }
    }
  }, [expandedDirs, fetchDir]);

  const handleClickFile = useCallback((node: FileTreeNode) => {
    onOpenFile(node.path);
  }, [onOpenFile]);

  // Refresh
  const handleRefresh = useCallback(() => {
    setTree([]);
    setExpandedDirs(new Set());
    fetchDir('').then((data) => {
      if (data?.files || data?.dirs) {
        const nodes: FileTreeNode[] = [
          ...(data.dirs || []).map((d: Record<string, unknown>) => ({
            name: d.name as string, path: d.path as string, isDirectory: true, children: [], loaded: false,
          })),
          ...(data.files || []).map((f: Record<string, unknown>) => ({
            name: f.name as string, path: f.path as string, isDirectory: false, size: f.size as number, language: f.language as string,
          })),
        ];
        setTree(nodes);
      }
    });
  }, [fetchDir]);

  function renderNode(node: FileTreeNode, depth: number): JSX.Element {
    const isExpanded = expandedDirs.has(node.path);
    const padLeft = 8 + depth * 16;

    if (node.isDirectory) {
      return (
        <div key={node.path}>
          <button
            className="flex w-full items-center gap-1 px-2 py-1 text-left text-sm hover:bg-warm-100 transition-colors"
            style={{ paddingLeft: padLeft }}
            onClick={() => handleToggleDir(node)}
          >
            <svg
              className={`h-3.5 w-3.5 shrink-0 text-warm-400 transition-transform ${isExpanded ? 'rotate-90' : ''}`}
              viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
            >
              <path d="M9 18l6-6-6-6" />
            </svg>
            <svg className="h-4 w-4 shrink-0 text-amber-400" viewBox="0 0 24 24" fill="currentColor" stroke="none">
              <path d="M2 6a2 2 0 0 1 2-2h5l2 2h9a2 2 0 0 1 2 2v1H2V6z" />
              <path d="M2 9h20v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9z" fill="currentColor" opacity="0.4" />
            </svg>
            <span className="truncate text-warm-700 text-xs">{node.name}</span>
          </button>
          {isExpanded && node.children && node.children.length > 0 && (
            <div>{node.children.map((child) => renderNode(child, depth + 1))}</div>
          )}
          {isExpanded && (!node.children || node.children.length === 0) && (
            <div className="text-xs text-warm-400" style={{ paddingLeft: padLeft + 24 }}>空目录</div>
          )}
        </div>
      );
    }

    return (
      <button
        key={node.path}
        className="flex w-full items-center gap-1 px-2 py-1 text-left text-sm hover:bg-warm-100 transition-colors group"
        style={{ paddingLeft: padLeft }}
        onClick={() => handleClickFile(node)}
        title={node.path}
      >
        <span className="w-3.5 shrink-0" />
        <FileTypeIcon language={node.language || getLanguageFromPath(node.path)} />
        <span className="truncate text-warm-600 text-xs group-hover:text-primary-600">{node.name}</span>
      </button>
    );
  }

  return (
    <div
      className="flex flex-col h-full border-r border-warm-150 bg-warm-50/50 shrink-0 min-h-0"
      style={{ width: `${width}px` }}
    >
      <div className="flex items-center justify-between px-3 py-2 border-b border-warm-150">
        <span className="text-xs font-semibold text-warm-500">工作区文件</span>
        <div className="flex items-center gap-0.5">
          {/* Upload button */}
          <label
            className={`p-0.5 rounded cursor-pointer transition-colors ${
              uploading
                ? 'text-primary-500 animate-pulse'
                : 'text-warm-400 hover:text-primary-500 hover:bg-warm-100'
            }`}
            title="上传文件到工作区"
          >
            {uploading ? (
              <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <circle cx="12" cy="12" r="10" strokeDasharray="32" strokeDashoffset="8" />
              </svg>
            ) : (
              <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
            )}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="sr-only"
              onChange={handleUpload}
              disabled={uploading}
            />
          </label>
          {/* Refresh button */}
          <button
            onClick={handleRefresh}
            className="p-0.5 rounded text-warm-400 hover:text-primary-500 hover:bg-warm-100 transition-colors"
            title="刷新"
            disabled={loading}
          >
            <svg className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <polyline points="23 4 23 10 17 10" />
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
            </svg>
          </button>
        </div>
      </div>
      {/* Upload status feedback */}
      {uploadStatus && (
        <div className={`px-3 py-1.5 text-xs border-b border-warm-150 ${
          uploadStatus.fail > 0 ? 'bg-danger-50 text-danger-600' : 'bg-success-50 text-success-600'
        }`}>
          {uploadStatus.fail > 0
            ? `上传完成: ${uploadStatus.ok} 成功, ${uploadStatus.fail} 失败${uploadStatus.msg ? ` — ${uploadStatus.msg}` : ''}`
            : `已上传 ${uploadStatus.ok} 个文件`}
        </div>
      )}
      <div className="flex-1 overflow-y-auto py-1" style={{ scrollbarWidth: 'thin' }}>
        {loading && tree.length === 0 && (
          <div className="flex items-center justify-center py-8">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
          </div>
        )}
        {error && (
          <div className="px-3 py-4 text-xs text-danger-500 text-center">{error}</div>
        )}
        {!loading && !error && tree.length === 0 && (
          <div className="px-3 py-4 text-xs text-warm-400 text-center">暂无文件</div>
        )}
        {tree.map((node) => renderNode(node, 0))}
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Main FilePreviewPanel Component
// ══════════════════════════════════════════════════════════════════════════════

interface FilePreviewPanelProps {
  tabs: WorkspacePreviewTab[];
  activeTabId: string | null;
  onSelectTab: (tabId: string) => void;
  onCloseTab: (tabId: string) => void;
  onAddReference: (ref: FileReference) => void;
  onOpenFile?: (path: string) => void;
  onOpenDiff?: (path: string, oldStr: string, newStr: string) => void;
  onOpenWorkspaceFile?: (path: string, content: string, language: string, state: string) => void;
  /**
   * 当前所有引用（来自 ChatInput 的 fileReferences）。
   * 组件内部会按当前激活 tab 的 path 过滤后传给子预览器。
   */
  references?: FileReference[];
  /**
   * 外部触发「滚动到某条引用」信号。变更时（含 nonce 变化）组件会找到对应 id 锚点并滚动。
   * 锚点格式：code-ref-{id} / diff-ref-{id} / md-ref-{id}。
   */
  pendingScrollRef?: { id: string; nonce: number } | null;
}

const FilePreviewPanel = memo(function FilePreviewPanel({
  tabs,
  activeTabId,
  onSelectTab,
  onCloseTab,
  onAddReference,
  onOpenWorkspaceFile,
  references,
  pendingScrollRef,
}: FilePreviewPanelProps): JSX.Element {
  const contentRef = useRef<HTMLDivElement>(null);
  const tabBarRef = useRef<HTMLDivElement>(null);

  const [popoverPos, setPopoverPos] = useState<{ x: number; y: number } | null>(null);
  const [popoverSelection, setPopoverSelection] = useState<{
    text: string;
    startLine?: number;
    endLine?: number;
  } | null>(null);

  // ── 文件树宽度可调整 ────────────────────────────────
  const [treeWidth, setTreeWidth, resetTreeWidth] = useResizableSize(
    'agenthub.layout.previewTreeWidth',
    224, // 14rem
    160,
    400,
  );
  const [treeWidthLive, setTreeWidthLive] = useState<number | null>(null);

  const activeTab = useMemo(
    () => tabs.find((t) => t.id === activeTabId) || null,
    [tabs, activeTabId],
  );

  /** 当前激活 tab 命中的引用（按 path 过滤） */
  const activeReferences = useMemo(() => {
    if (!references || !activeTab) return undefined;
    return references.filter((r) => r.path === activeTab.path);
  }, [references, activeTab]);

  // ── Selection detection ─────────────────────────────────────────────
  const handleMouseUp = useCallback(() => {
    // Delay to let the selection settle
    setTimeout(() => {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || !contentRef.current) {
        setPopoverPos(null);
        return;
      }

      const range = selection.getRangeAt(0);
      if (!contentRef.current.contains(range.commonAncestorContainer)) {
        setPopoverPos(null);
        return;
      }

      const text = selection.toString().trim();
      if (!text || text.length < 2) {
        setPopoverPos(null);
        return;
      }

      // Get line numbers from data attributes
      let startLine: number | undefined;
      let endLine: number | undefined;

      const startEl = range.startContainer.nodeType === Node.TEXT_NODE
        ? range.startContainer.parentElement
        : range.startContainer as HTMLElement;

      const endEl = range.endContainer.nodeType === Node.TEXT_NODE
        ? range.endContainer.parentElement
        : range.endContainer as HTMLElement;

      const startRow = startEl?.closest?.('[data-line-number]');
      const endRow = endEl?.closest?.('[data-line-number]');

      if (startRow) {
        startLine = parseInt(startRow.getAttribute('data-line-number') || '', 10) || undefined;
      }
      if (endRow) {
        endLine = parseInt(endRow.getAttribute('data-line-number') || '', 10) || undefined;
      }

      const rect = range.getBoundingClientRect();
      setPopoverPos({
        x: rect.left + rect.width / 2 - 100,
        y: rect.top - 44,
      });
      setPopoverSelection({
        text,
        startLine,
        endLine: endLine || startLine,
      });
    }, 10);
  }, []);

  // ── Auto-scroll tab bar so the active tab is always visible ──────────
  useEffect(() => {
    if (!tabBarRef.current || !activeTabId) return;
    const activeEl = tabBarRef.current.querySelector(
      `[data-tab-id="${CSS.escape(activeTabId)}"]`,
    ) as HTMLElement | null;
    if (activeEl) {
      activeEl.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
    }
  }, [activeTabId]);

  // ── Reset scroll when switching tabs ─────────────────────────────────
  useEffect(() => {
    if (contentRef.current) {
      contentRef.current.scrollTop = 0;
      contentRef.current.scrollLeft = 0;
    }
  }, [activeTabId]);

  // ── Scroll to pending reference anchor ─────────────────────────────
  useEffect(() => {
    if (!pendingScrollRef || !contentRef.current) return;
    const root = contentRef.current;
    // 给 Markdown 渲染一点时间（react-markdown 是同步的，但 layout 有可能未稳定）
    const tryScroll = (attempt = 0) => {
      const id = pendingScrollRef.id;
      const el =
        root.querySelector(`#code-ref-${CSS.escape(id)}`) ||
        root.querySelector(`#diff-ref-${CSS.escape(id)}`) ||
        root.querySelector(`#md-ref-${CSS.escape(id)}`) ||
        root.querySelector(`[data-reference-id="${CSS.escape(id)}"]`);
      if (el && el instanceof HTMLElement) {
        // 找到该元素上方最近的可滚动祖先并滚动
        const scrollTarget = root; // contentRef 是 overflow 容器
        const elRect = el.getBoundingClientRect();
        const rootRect = scrollTarget.getBoundingClientRect();
        const targetTop = scrollTarget.scrollTop + (elRect.top - rootRect.top) - 80;
        scrollTarget.scrollTo({ top: Math.max(targetTop, 0), behavior: 'smooth' });
        // 加一个短暂高亮动画
        el.classList.add('reference-flash');
        window.setTimeout(() => el.classList.remove('reference-flash'), 1500);
        return;
      }
      if (attempt < 8) {
        window.setTimeout(() => tryScroll(attempt + 1), 80);
      }
    };
    tryScroll();
  }, [pendingScrollRef]);

  // ── Add selection to chat ───────────────────────────────────────────
  const handleAddToChat = useCallback(() => {
    if (!popoverSelection || !activeTab) return;

    onAddReference({
      id: `ref-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      name: activeTab.path.split('/').pop() || activeTab.path,
      path: activeTab.path,
      lineStart: popoverSelection.startLine,
      lineEnd: popoverSelection.endLine,
      quote: popoverSelection.text,
      kind: 'chat-selection',
    });

    setPopoverPos(null);
    setPopoverSelection(null);
    window.getSelection()?.removeAllRanges();
  }, [popoverSelection, activeTab, onAddReference]);

  // ── Keyboard shortcut to close popover ──────────────────────────────
  useEffect(() => {
    if (!popoverPos) return;
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setPopoverPos(null);
        setPopoverSelection(null);
      }
    };
    document.addEventListener('keydown', onEsc);
    return () => document.removeEventListener('keydown', onEsc);
  }, [popoverPos]);

  // ── Render ──────────────────────────────────────────────────────────
  return (
    <div className="flex h-full bg-white">
      {/* ── Workspace File Tree (left sidebar) ────────────────────── */}
      <WorkspaceFileTree
        width={treeWidthLive ?? treeWidth}
        onOpenFile={(path) => {
          const token = localStorage.getItem('agenthub_token') || '';
          fetch(`/api/files/workspace/read?path=${encodeURIComponent(path)}`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          })
            .then((r) => r.json())
            .then((data) => {
              if (onOpenWorkspaceFile && data.content) {
                onOpenWorkspaceFile(path, data.content, data.language || '', data.state || 'ok');
              }
            })
            .catch(() => {});
        }}
      />
      {/* ── 可调整分隔条：文件树 / 内容 ─────────────────────── */}
      <ResizableDivider
        orientation="horizontal"
        size={treeWidthLive ?? treeWidth}
        onPreview={setTreeWidthLive}
        onCommit={(v) => {
          setTreeWidthLive(null);
          setTreeWidth(v);
        }}
        min={160}
        max={400}
        defaultValue={224}
        onReset={resetTreeWidth}
        ariaLabel="文件树宽度"
        title="拖动调整文件树宽度 · 右键输入数值 · 双击重置"
        // 气泡出现在被调整的文件树内部（分隔条左侧）
        bubbleSide="left"
      />
      {/* ── Main preview area ─────────────────────────────────────── */}
      <div className="flex flex-col flex-1 min-w-0 min-h-0">
        {/* ── Tab bar ────────────────────────────────────────────── */}
        {tabs.length > 0 && (
          <div ref={tabBarRef} className="flex shrink-0 border-b border-warm-150 bg-warm-50 overflow-x-auto" style={{ scrollbarWidth: 'thin' }}>
          {tabs.map((tab) => (
            <div
              key={tab.id}
              data-tab-id={tab.id}
              className={`flex items-center gap-1.5 px-3 py-2 cursor-pointer border-r border-warm-150 text-sm transition-colors select-none shrink-0 ${
                activeTabId === tab.id
                  ? 'bg-white text-warm-900 font-medium'
                  : 'bg-transparent text-warm-500 hover:bg-warm-100 hover:text-warm-700'
              }`}
              onClick={() => onSelectTab(tab.id)}
            >
              <FileTypeIcon language={tab.language || getLanguageFromPath(tab.path)} />
              <span className="max-w-[160px] truncate">
                {tab.path.split('/').pop()}
              </span>
              <StatusIcon status={tab.status} />
              {tab.kind === 'diff' && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-600 font-medium border border-amber-200">
                  DIFF
                </span>
              )}
              {tab.state === 'loading' && (
                <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
              )}
              <button
                onClick={(e) => { e.stopPropagation(); onCloseTab(tab.id); }}
                className="ml-0.5 p-0.5 rounded hover:bg-warm-200 text-warm-400 hover:text-warm-600 transition-colors"
                title="关闭"
              >
                <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
          ))}
        </div>
      )}

      {/* ── Content area ────────────────────────────────────────────── */}
      <div
        ref={contentRef}
        className="flex-1 min-w-0 min-h-0 overflow-auto bg-[#1E1E1E]"
        onMouseUp={handleMouseUp}
      >
        {!activeTab && (
          <div className="flex h-full items-center justify-center bg-white">
            <div className="text-center text-warm-400">
              <svg className="mx-auto mb-3 h-12 w-12 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
                <line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" />
              </svg>
              <p className="text-sm">选择左侧文件树中的文件即可预览</p>
              <p className="text-xs mt-1 text-warm-300">支持代码 / Markdown / Diff / 图片预览</p>
            </div>
          </div>
        )}

        {activeTab && activeTab.state === 'loading' && (
          <div className="flex h-full items-center justify-center bg-white">
            <div className="flex flex-col items-center gap-3">
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
              <span className="text-sm text-warm-400">加载中...</span>
            </div>
          </div>
        )}

        {activeTab && activeTab.state === 'error' && (
          <div className="flex h-full items-center justify-center bg-white">
            <div className="text-center">
              <svg className="mx-auto mb-3 h-12 w-12 text-danger-300" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <circle cx="12" cy="12" r="10" /><line x1="15" y1="9" x2="9" y2="15" /><line x1="9" y1="9" x2="15" y2="15" />
              </svg>
              <p className="text-sm text-danger-500">文件加载失败</p>
              <p className="text-xs text-warm-400 mt-1">{activeTab.path}</p>
            </div>
          </div>
        )}

        {activeTab && activeTab.state !== 'loading' && activeTab.state !== 'error' && (
          <>
            {/* Code file preview */}
            {activeTab.kind === 'file' && !isMarkdownFile(activeTab.path) && !isImageFile(activeTab.path) && (
              <CodePreview
                content={activeTab.content || ''}
                language={activeTab.language || getLanguageFromPath(activeTab.path)}
                references={activeReferences}
              />
            )}

            {/* Markdown preview */}
            {activeTab.kind === 'file' && isMarkdownFile(activeTab.path) && (
              <div className="markdown-container min-h-full min-w-0 bg-white p-6">
                <MarkdownRenderer content={activeTab.content || ''} references={activeReferences} />
              </div>
            )}

            {/* Image preview */}
            {activeTab.kind === 'file' && isImageFile(activeTab.path) && (
              <div className="flex h-full items-center justify-center bg-[#0D0D0D] p-4">
                {activeTab.content ? (
                  <img
                    src={activeTab.content.startsWith('data:') ? activeTab.content : `data:image/png;base64,${activeTab.content}`}
                    alt={activeTab.path.split('/').pop()}
                    className="max-h-full max-w-full object-contain rounded-lg shadow-2xl"
                  />
                ) : (
                  <div className="text-center text-warm-500">
                    <svg className="mx-auto mb-3 h-12 w-12 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                      <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                      <circle cx="8.5" cy="8.5" r="1.5" /><polyline points="21 15 16 10 5 21" />
                    </svg>
                    <p className="text-sm">图片数据不可用</p>
                  </div>
                )}
              </div>
            )}

            {/* Diff preview */}
            {activeTab.kind === 'diff' && (
              activeTab.content ? (
                <DiffPreview
                  diffText={activeTab.content}
                  language={activeTab.language || getLanguageFromPath(activeTab.path)}
                  references={activeReferences}
                />
              ) : activeTab.diffOld && activeTab.diffNew ? (
                <DiffPreview
                  diffText={`--- a/${activeTab.path}\n+++ b/${activeTab.path}\n@@ -1,${activeTab.diffOld.split('\n').length} +1,${activeTab.diffNew.split('\n').length} @@\n${
                    activeTab.diffOld.split('\n').map((l) => '- ' + l).join('\n')
                  }\n${
                    activeTab.diffNew.split('\n').map((l) => '+ ' + l).join('\n')
                  }`}
                  language={activeTab.language || getLanguageFromPath(activeTab.path)}
                  references={activeReferences}
                />
              ) : (
                <div className="flex h-full items-center justify-center bg-white">
                  <p className="text-sm text-warm-400">Diff 数据不可用</p>
                </div>
              )
            )}

            {/* Binary / too_large states */}
            {activeTab.state === 'binary' && (
              <div className="flex h-full items-center justify-center bg-white">
                <div className="text-center text-warm-400">
                  <svg className="mx-auto mb-3 h-12 w-12 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <rect x="2" y="2" width="20" height="20" rx="2" /><path d="M12 8v4l2 2" />
                  </svg>
                  <p className="text-sm">二进制文件无法预览</p>
                </div>
              </div>
            )}
            {activeTab.state === 'too_large' && (
              <div className="flex h-full items-center justify-center bg-white">
                <div className="text-center text-warm-400">
                  <svg className="mx-auto mb-3 h-12 w-12 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                  </svg>
                  <p className="text-sm">文件过大，无法预览</p>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* ── Selection popover ───────────────────────────────────────── */}
      <SelectionPopover
        position={popoverPos}
        onAdd={handleAddToChat}
        onClose={() => {
          setPopoverPos(null);
          setPopoverSelection(null);
        }}
      />
      </div>

    </div>
  );
});

export default FilePreviewPanel;

export { getLanguageFromPath, isMarkdownFile, isImageFile };
