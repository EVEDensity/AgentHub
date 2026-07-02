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
  pptx: 'pptx', ppt: 'ppt', docx: 'docx', doc: 'docx',
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

function isPptxFile(path: string): boolean {
  const ext = path.split('.').pop()?.toLowerCase() || '';
  return ['pptx', 'ppt'].includes(ext);
}

function isDocxFile(path: string): boolean {
  const ext = path.split('.').pop()?.toLowerCase() || '';
  return ext === 'docx';
}

function isHtmlContent(tab: WorkspacePreviewTab): boolean {
  return tab.contentType === 'html' || isPptxFile(tab.path) || isDocxFile(tab.path);
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
// HTML / Rich Document Preview (PPTX, DOCX)
// ══════════════════════════════════════════════════════════════════════════════

function HtmlPreview({ htmlContent, slideCount, imageCount, textLength, totalChars, truncated }: {
  htmlContent: string;
  slideCount?: number;
  imageCount?: number;
  textLength?: number;
  totalChars?: number;
  truncated?: boolean;
}): JSX.Element {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [iframeHeight, setIframeHeight] = useState(600);

  // Generate a unique srcdoc that auto-sizes the iframe
  const srcdoc = useMemo(() => {
    // Inject a height-reporter script into the HTML
    const reporter = `
<script>
(function(){
  function reportHeight() {
    var h = Math.max(
      document.body.scrollHeight,
      document.documentElement.scrollHeight,
      document.body.offsetHeight,
      document.documentElement.offsetHeight
    );
    window.parent.postMessage({type:'pptx-resize', height: h + 40}, '*');
  }
  window.addEventListener('load', reportHeight);
  // Also report after images load
  var imgs = document.querySelectorAll('img');
  imgs.forEach(function(img) { img.addEventListener('load', reportHeight); });
  // Fallback: report after a short delay
  setTimeout(reportHeight, 500);
  setTimeout(reportHeight, 1500);
})();
</script>`;
    // Insert reporter before </body> or at the end
    if (htmlContent.includes('</body>')) {
      return htmlContent.replace('</body>', reporter + '</body>');
    }
    return htmlContent + reporter;
  }, [htmlContent]);

  // Listen for resize messages from the iframe
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (e.data && e.data.type === 'pptx-resize' && typeof e.data.height === 'number') {
        setIframeHeight(Math.max(400, e.data.height));
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, []);

  // Reset height when content changes
  useEffect(() => {
    setIframeHeight(600);
  }, [htmlContent]);

  return (
    <div className="flex flex-col h-full bg-[#f0f2f5]">
      {/* Metadata bar */}
      <div className="flex items-center gap-4 px-4 py-2 bg-white border-b border-warm-150 text-xs text-warm-500 shrink-0">
        {slideCount != null && slideCount > 0 && (
          <span className="flex items-center gap-1">
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <rect x="3" y="3" width="18" height="14" rx="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" />
            </svg>
            <span>{slideCount} 张幻灯片</span>
          </span>
        )}
        {imageCount != null && imageCount > 0 && (
          <span className="flex items-center gap-1">
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><polyline points="21 15 16 10 5 21" />
            </svg>
            <span>{imageCount} 张图片</span>
          </span>
        )}
        {textLength != null && (
          <span className="flex items-center gap-1">
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <polyline points="4 7 4 4 20 4 20 7" /><line x1="9" y1="20" x2="15" y2="20" /><line x1="12" y1="4" x2="12" y2="20" />
            </svg>
            <span>{textLength.toLocaleString()} 字符</span>
          </span>
        )}
        {truncated && (
          <span className="text-amber-500 font-medium">内容已截断（{(totalChars || 0).toLocaleString()} 字符）</span>
        )}
      </div>

      {/* Sandboxed iframe */}
      <div className="flex-1 overflow-auto">
        <iframe
          ref={iframeRef}
          srcDoc={srcdoc}
          sandbox="allow-scripts"
          className="w-full border-0"
          style={{ height: `${iframeHeight}px` }}
          title="文档预览"
        />
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
  if (language === 'pptx' || language === 'ppt') {
    return (
      <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="#D24726" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="14" rx="2" />
        <line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" />
      </svg>
    );
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

interface DndFileEntry {
  file: File;
  /** Relative path within the dropped folder, e.g. "src/utils/helper.py" */
  relativePath: string;
}

// ── Drag-and-drop helpers ──────────────────────────────────────────────

/** Recursively traverse a DataTransferItemList, resolving files and folders. */
async function traverseDataTransferItems(
  items: DataTransferItemList,
): Promise<DndFileEntry[]> {
  const entries: DndFileEntry[] = [];

  async function walk(entry: FileSystemEntry, parentPath: string): Promise<void> {
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) => {
        (entry as FileSystemFileEntry).file(resolve, reject);
      });
      const relPath = parentPath ? `${parentPath}/${entry.name}` : entry.name;
      entries.push({ file, relativePath: relPath });
    } else if (entry.isDirectory) {
      const dirEntry = entry as FileSystemDirectoryEntry;
      const reader = dirEntry.createReader();
      // readEntries may batch — loop until done
      const children: FileSystemEntry[] = [];
      let batch: FileSystemEntry[];
      do {
        batch = await new Promise<FileSystemEntry[]>((resolve, reject) => {
          reader.readEntries(resolve, reject);
        });
        children.push(...batch);
      } while (batch.length > 0);

      const subPath = parentPath ? `${parentPath}/${entry.name}` : entry.name;
      for (const child of children) {
        await walk(child, subPath);
      }
    }
  }

  const itemList: DataTransferItem[] = [];
  for (let i = 0; i < items.length; i++) itemList.push(items[i]);

  for (const item of itemList) {
    const entry = (item as any).webkitGetAsEntry?.() as FileSystemEntry | null;
    if (entry) {
      await walk(entry, '');
    } else if (item.kind === 'file') {
      // Fallback: plain file without directory info
      const file = item.getAsFile();
      if (file) entries.push({ file, relativePath: file.name });
    }
  }

  return entries;
}

/** Extract DndFileEntry list from a folder <input>'s selected files. */
function entriesFromFolderInput(files: FileList): DndFileEntry[] {
  const entries: DndFileEntry[] = [];
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    entries.push({
      file: f,
      relativePath: (f as any).webkitRelativePath || f.name,
    });
  }
  return entries;
}

/** Extract DndFileEntry list from a files <input>'s selected files. */
function entriesFromFileInput(files: FileList): DndFileEntry[] {
  const entries: DndFileEntry[] = [];
  for (let i = 0; i < files.length; i++) {
    entries.push({ file: files[i], relativePath: files[i].name });
  }
  return entries;
}

// ── Component ──────────────────────────────────────────────────────────

function WorkspaceFileTree({
  onOpenFile,
  width,
  sessionId,
  workspaceVersion = 0,
}: {
  onOpenFile: (path: string) => void;
  width: number;
  sessionId?: string;
  workspaceVersion?: number;
}): JSX.Element {
  const [tree, setTree] = useState<FileTreeNode[]>([]);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Upload state
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{
    total: number;
    done: number;
    ok: number;
    fail: number;
    currentFile?: string;
  } | null>(null);
  const [uploadStatus, setUploadStatus] = useState<{
    ok: number;
    fail: number;
    msg?: string;
  } | null>(null);

  // Drag-over state
  const [isDragOver, setIsDragOver] = useState(false);
  const dragCounterRef = useRef(0);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);

  const fetchDir = useCallback(async (subdir: string) => {
    setLoading(true);
    setError(null);
    try {
      const token = localStorage.getItem('agenthub_token') || '';
      const params = new URLSearchParams();
      if (subdir) params.set('subdir', subdir);
      if (sessionId) params.set('session_id', sessionId);
      const qs = params.toString();
      const url = qs ? `/api/files/workspace/list?${qs}` : '/api/files/workspace/list';
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
  }, [sessionId]);

  // Upload a single file — returns true on success
  const uploadOne = useCallback(async (
    file: File,
    subdir: string,
    token: string,
  ): Promise<boolean> => {
    try {
      const formData = new FormData();
      formData.append('file', file);
      const params = new URLSearchParams();
      if (subdir) params.set('subdir', subdir);
      if (sessionId) params.set('session_id', sessionId);
      const qs = params.toString();
      const url = qs ? `/api/files/workspace/upload?${qs}` : '/api/files/workspace/upload';
      const res = await fetch(url, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      });
      if (!res.ok) {
        let detail = '';
        try { const errData = await res.json(); detail = errData.detail || ''; } catch { /* */ }
        console.error('[WorkspaceUpload]', `${file.name}: ${res.status} ${detail || res.statusText}`);
        return false;
      }
      return true;
    } catch (err) {
      console.error('[WorkspaceUpload]', `${file.name}: ${String(err)}`);
      return false;
    }
  }, []);

  // Batch upload with concurrency control
  const uploadBatch = useCallback(async (
    entries: DndFileEntry[],
    token: string,
  ) => {
    if (entries.length === 0) return;

    setUploading(true);
    setUploadProgress({ total: entries.length, done: 0, ok: 0, fail: 0 });

    let ok = 0;
    let fail = 0;
    const errors: string[] = [];
    const CONCURRENCY = 3;

    // Process in concurrent batches
    for (let i = 0; i < entries.length; i += CONCURRENCY) {
      const batch = entries.slice(i, i + CONCURRENCY);
      const results = await Promise.all(
        batch.map(async (entry) => {
          // Derive subdir from relativePath (strip filename)
          const parts = entry.relativePath.split('/');
          const fileName = parts.pop() || entry.file.name;
          const subdir = parts.join('/');

          // Create a File with the correct name if they differ
          const uploadFile =
            fileName !== entry.file.name
              ? new File([entry.file], fileName, { type: entry.file.type })
              : entry.file;

          const success = await uploadOne(uploadFile, subdir, token);
          return { success, name: entry.relativePath };
        }),
      );

      for (const r of results) {
        if (r.success) ok++;
        else { fail++; errors.push(r.name); }
      }

      setUploadProgress({
        total: entries.length,
        done: Math.min(i + CONCURRENCY, entries.length),
        ok,
        fail,
        currentFile: batch[batch.length - 1]?.relativePath,
      });
    }

    setUploading(false);
    setUploadProgress(null);
    setUploadStatus({
      ok,
      fail,
      msg: errors.length > 0 ? errors.slice(0, 3).join('; ') : undefined,
    });

    // Auto-clear success status after 6s; keep errors visible
    if (fail === 0) {
      setTimeout(() => setUploadStatus(null), 6000);
    }

    // Refresh tree if anything was uploaded successfully
    if (ok > 0) {
      setTree([]);
      setExpandedDirs(new Set());
      const data = await fetchDir('');
      if (data?.files || data?.dirs) {
        setTree(buildRootNodes(data));
      }
    }
  }, [uploadOne, fetchDir]);

  // ── Event handlers ──────────────────────────────────────────────────

  /** File input (single/multiple files — no folder structure). */
  const handleFileUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    const token = localStorage.getItem('agenthub_token') || '';
    const entries = entriesFromFileInput(files);
    await uploadBatch(entries, token);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, [uploadBatch]);

  /** Folder input (webkitdirectory — preserves folder structure). */
  const handleFolderUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    const token = localStorage.getItem('agenthub_token') || '';
    const entries = entriesFromFolderInput(files);
    await uploadBatch(entries, token);
    if (folderInputRef.current) folderInputRef.current.value = '';
  }, [uploadBatch]);

  // Drag-and-drop handlers
  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current += 1;
    if (e.dataTransfer.types && e.dataTransfer.types.length > 0) {
      setIsDragOver(true);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer.dropEffect) {
      e.dataTransfer.dropEffect = 'copy';
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current -= 1;
    if (dragCounterRef.current <= 0) {
      dragCounterRef.current = 0;
      setIsDragOver(false);
    }
  }, []);

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
    dragCounterRef.current = 0;

    if (uploading) return;

    const items = e.dataTransfer.items;
    if (!items || items.length === 0) return;

    const token = localStorage.getItem('agenthub_token') || '';
    const entries = await traverseDataTransferItems(items);
    if (entries.length === 0) return;

    await uploadBatch(entries, token);
  }, [uploading, uploadBatch]);

  // ── Tree helpers ────────────────────────────────────────────────────

  function buildRootNodes(data: any): FileTreeNode[] {
    return [
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
  }

  // Load root on mount, and auto-refresh when workspace changes
  useEffect(() => {
    fetchDir('').then((data) => {
      if (data?.files || data?.dirs) setTree(buildRootNodes(data));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchDir, workspaceVersion]);

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
            name: d.name as string, path: d.path as string, isDirectory: true, children: [], loaded: false,
          })),
          ...(data.files || []).map((f: Record<string, unknown>) => ({
            name: f.name as string, path: f.path as string, isDirectory: false, size: f.size as number, language: f.language as string,
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

  const handleRefresh = useCallback(() => {
    setTree([]);
    setExpandedDirs(new Set());
    fetchDir('').then((data) => {
      if (data?.files || data?.dirs) setTree(buildRootNodes(data));
    });
  }, [fetchDir]);

  // Delete a file or directory from the workspace
  const handleDeleteItem = useCallback(async (nodePath: string, isDir: boolean) => {
    const label = isDir ? `目录 "${nodePath}"` : `文件 "${nodePath}"`;
    if (!window.confirm(`确定要删除 ${label} 吗？${isDir ? '目录内所有文件将被递归删除。' : '此操作不可撤销。'}`)) {
      return;
    }
    try {
      const token = localStorage.getItem('agenthub_token') || '';
      const body: Record<string, string> = { path: nodePath };
      if (sessionId) body.session_id = sessionId;
      const res = await fetch('/api/files/workspace/item', {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        alert(err.detail || 'Delete failed');
        return;
      }
      // Refresh tree after delete
      setTree([]);
      setExpandedDirs(new Set());
      const data = await fetchDir('');
      if (data?.files || data?.dirs) setTree(buildRootNodes(data));
    } catch (e) {
      alert(String(e));
    }
  }, [fetchDir, sessionId]);

  function renderNode(node: FileTreeNode, depth: number): JSX.Element {
    const isExpanded = expandedDirs.has(node.path);
    const padLeft = 8 + depth * 16;

    if (node.isDirectory) {
      return (
        <div key={node.path}>
          <div className="flex w-full items-center gap-1 px-2 py-1 text-left text-sm hover:bg-warm-100 transition-colors group"
            style={{ paddingLeft: padLeft }}>
            <button
              className="flex flex-1 items-center gap-1 min-w-0"
              onClick={() => handleToggleDir(node)}
              title={node.path}
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
            <button
              className="shrink-0 opacity-0 group-hover:opacity-100 p-0.5 text-warm-400 hover:text-red-500 transition-all rounded"
              onClick={(e) => { e.stopPropagation(); handleDeleteItem(node.path, true); }}
              title={`删除目录: ${node.name}`}
            >
              <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              </svg>
            </button>
          </div>
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
      <div
        key={node.path}
        className="flex w-full items-center gap-1 px-2 py-1 text-left text-sm hover:bg-warm-100 transition-colors group"
        style={{ paddingLeft: padLeft }}
      >
        <button
          className="flex flex-1 items-center gap-1 min-w-0"
          onClick={() => handleClickFile(node)}
          title={node.path}
        >
          <span className="w-3.5 shrink-0" />
          <FileTypeIcon language={node.language || getLanguageFromPath(node.path)} />
          <span className="truncate text-warm-600 text-xs group-hover:text-primary-600">{node.name}</span>
        </button>
        <button
          className="shrink-0 opacity-0 group-hover:opacity-100 p-0.5 text-warm-400 hover:text-red-500 transition-all rounded"
          onClick={(e) => { e.stopPropagation(); handleDeleteItem(node.path, false); }}
          title={`删除文件: ${node.name}`}
        >
          <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="3 6 5 6 21 6" />
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
          </svg>
        </button>
      </div>
    );
  }

  return (
    <div
      className={`flex flex-col h-full border-r border-warm-150 bg-warm-50/50 shrink-0 min-h-0 relative transition-colors ${
        isDragOver ? 'bg-primary-50/70 ring-2 ring-inset ring-primary-400' : ''
      }`}
      style={{ width: `${width}px` }}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* ── Drag-overlay ─────────────────────────────────────────── */}
      {isDragOver && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-primary-50/60 pointer-events-none">
          <div className="flex flex-col items-center gap-2 text-primary-600">
            <svg className="h-10 w-10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="14 5 9 0 4 5" />
              <line x1="9" y1="0" x2="9" y2="11" />
            </svg>
            <span className="text-sm font-semibold">释放以将文件/文件夹上传到工作区</span>
            <span className="text-xs text-primary-400">支持拖拽整个文件夹</span>
          </div>
        </div>
      )}

      {/* ── Header ───────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-warm-150">
        <span className="text-xs font-semibold text-warm-500">工作区文件</span>
        <div className="flex items-center gap-0.5">
          {/* Upload files button */}
          <label
            className={`p-0.5 rounded cursor-pointer transition-colors ${
              uploading
                ? 'text-primary-500 animate-pulse'
                : 'text-warm-400 hover:text-primary-500 hover:bg-warm-100'
            }`}
            title="上传文件"
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
              onChange={handleFileUpload}
              disabled={uploading}
            />
          </label>

          {/* Upload folder button */}
          <label
            className={`p-0.5 rounded cursor-pointer transition-colors ${
              uploading
                ? 'text-warm-300 pointer-events-none'
                : 'text-warm-400 hover:text-primary-500 hover:bg-warm-100'
            }`}
            title="上传文件夹"
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
              <line x1="12" y1="11" x2="12" y2="17" />
              <line x1="9" y1="14" x2="15" y2="14" />
            </svg>
            <input
              ref={folderInputRef}
              type="file"
              /* @ts-expect-error webkitdirectory is widely supported */
              webkitdirectory=""
              directory=""
              multiple
              className="sr-only"
              onChange={handleFolderUpload}
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

      {/* ── Upload progress bar ──────────────────────────────────── */}
      {uploadProgress && (
        <div className="px-3 py-2 border-b border-warm-150 bg-primary-50/50">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs text-primary-700 font-medium">
              {uploadProgress.done < uploadProgress.total
                ? `上传中 ${uploadProgress.done}/${uploadProgress.total}`
                : `处理完成 ${uploadProgress.total} 个文件`}
            </span>
            <span className="text-xs text-primary-500">
              ✓{uploadProgress.ok}{uploadProgress.fail > 0 ? ` ✗${uploadProgress.fail}` : ''}
            </span>
          </div>
          <div className="h-1.5 bg-primary-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-primary-500 rounded-full transition-all duration-300 ease-out"
              style={{ width: `${Math.round((uploadProgress.done / uploadProgress.total) * 100)}%` }}
            />
          </div>
          {uploadProgress.currentFile && (
            <div className="mt-1 text-xs text-warm-400 truncate" title={uploadProgress.currentFile}>
              {uploadProgress.currentFile}
            </div>
          )}
        </div>
      )}

      {/* ── Upload status feedback ────────────────────────────────── */}
      {uploadStatus && !uploadProgress && (
        <div className={`px-3 py-1.5 text-xs border-b border-warm-150 ${
          uploadStatus.fail > 0 ? 'bg-danger-50 text-danger-600' : 'bg-success-50 text-success-600'
        }`}>
          {uploadStatus.fail > 0
            ? `上传完成: ${uploadStatus.ok} 成功, ${uploadStatus.fail} 失败${uploadStatus.msg ? ` — ${uploadStatus.msg}` : ''}`
            : `已上传 ${uploadStatus.ok} 个文件`}
        </div>
      )}

      {/* ── File tree ─────────────────────────────────────────────── */}
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
          <div className="px-3 py-6 text-xs text-warm-400 text-center">
            <div className="mb-2 text-warm-300">
              <svg className="h-8 w-8 mx-auto mb-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
            </div>
            <p className="mb-1">暂无文件</p>
            <p className="text-warm-300">上传或拖拽文件/文件夹到此处</p>
          </div>
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
  onOpenWorkspaceFile?: (path: string, content: string, language: string, state: string, meta?: Record<string, unknown>) => void;
  /** Current session ID for session-scoped workspace isolation. */
  sessionId?: string;
  /** Version counter — increments on every workspace file change to trigger auto-refresh. */
  workspaceVersion?: number;
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
  sessionId,
  references,
  pendingScrollRef,
  workspaceVersion,
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
        sessionId={sessionId}
        workspaceVersion={workspaceVersion}
        onOpenFile={(path) => {
          const token = localStorage.getItem('agenthub_token') || '';
          const readParams = new URLSearchParams({ path });
          if (sessionId) readParams.set('session_id', sessionId);
          fetch(`/api/files/workspace/read?${readParams.toString()}`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          })
            .then((r) => {
              if (!r.ok) throw new Error(`HTTP ${r.status}: ${r.statusText}`);
              return r.json();
            })
            .then((data) => {
              if (onOpenWorkspaceFile) {
                if (data.content || data.contentType === 'html') {
                  onOpenWorkspaceFile(path, data.content || '', data.language || '', data.state || 'ok', {
                    contentType: data.contentType,
                    slideCount: data.slideCount,
                    imageCount: data.imageCount,
                    textLength: data.textLength,
                    totalChars: data.totalChars,
                    truncated: data.truncated,
                  });
                } else if (data.state === 'binary' || data.state === 'too_large') {
                  // Open with empty content — the panel will show the appropriate state
                  onOpenWorkspaceFile(path, '', data.language || '', data.state);
                }
              }
            })
            .catch((err) => {
              console.error('[FilePreview] Failed to load workspace file:', path, err);
            });
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
              <p className="text-xs mt-1 text-warm-300">支持代码 / Markdown / Diff / 图片 / PPT / Word 预览</p>
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
            {activeTab.kind === 'file' && !isMarkdownFile(activeTab.path) && !isImageFile(activeTab.path) && !isHtmlContent(activeTab) && (
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

            {/* PPTX / DOCX / HTML rich preview */}
            {activeTab.kind === 'file' && isHtmlContent(activeTab) && (
              activeTab.content ? (
                <HtmlPreview
                  htmlContent={activeTab.content}
                  slideCount={activeTab.slideCount}
                  imageCount={activeTab.imageCount}
                  textLength={activeTab.textLength}
                  totalChars={activeTab.totalChars}
                  truncated={activeTab.truncated}
                />
              ) : (
                <div className="flex h-full items-center justify-center bg-white">
                  <div className="text-center text-warm-400">
                    <svg className="mx-auto mb-3 h-12 w-12 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                      <rect x="3" y="3" width="18" height="14" rx="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" />
                    </svg>
                    <p className="text-sm">文档内容为空</p>
                  </div>
                </div>
              )
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
