import React, { useEffect, useMemo, useState, type JSX } from 'react';

// ── Types ───────────────────────────────────────────────────────────

interface DiffHunk {
  header: string;            // @@ -a,b +c,d @@ context
  oldStart: number;
  oldCount: number;
  newStart: number;
  newCount: number;
  lines: DiffLine[];
}

interface DiffLine {
  type: 'context' | 'add' | 'del' | 'header';
  oldLine: number | null;
  newLine: number | null;
  content: string;
}

interface FileDiff {
  path: string;
  oldPath: string;
  lang: string;
  hunks: DiffHunk[];
  added: number;
  deleted: number;
}

// ── Language detection ──────────────────────────────────────────────

const LANG_MAP: Record<string, string> = {
  ts: 'typescript', tsx: 'typescript',
  js: 'javascript', jsx: 'javascript',
  py: 'python', rs: 'rust', go: 'go',
  json: 'json', yaml: 'yaml', yml: 'yaml',
  sql: 'sql', md: 'markdown',
  sh: 'shell', bash: 'shell', zsh: 'shell',
  css: 'css', scss: 'scss', html: 'html',
  java: 'java', cpp: 'cpp', c: 'c', h: 'c',
  rb: 'ruby', php: 'php', swift: 'swift',
  kt: 'kotlin', vue: 'html', svelte: 'html',
  toml: 'toml', ini: 'ini', cfg: 'ini',
  dockerfile: 'dockerfile', makefile: 'makefile',
};

function detectLang(path: string): string {
  const base = path.split('/').pop() || path;
  const lower = base.toLowerCase();
  if (lower === 'dockerfile') return 'dockerfile';
  if (lower === 'makefile') return 'makefile';
  const ext = base.includes('.') ? base.split('.').pop()?.toLowerCase() || '' : '';
  return LANG_MAP[ext] || ext || 'plaintext';
}

// ── Diff parser ─────────────────────────────────────────────────────

function parseDiff(diffText: string): FileDiff[] {
  const files: FileDiff[] = [];
  const fileBlocks = diffText.split(/^diff --git /m).filter(Boolean);

  for (const block of fileBlocks) {
    const lines = block.split('\n');
    // First line is "a/path b/path"
    const headerLine = lines[0] || '';
    const pathMatch = headerLine.match(/b\/(.+?)(?:\s|$)/);
    const oldPathMatch = headerLine.match(/a\/(.+?)(?:\s|$)/);
    const path = pathMatch?.[1] || '';
    const oldPath = oldPathMatch?.[1] || path;

    if (!path) continue;

    const lang = detectLang(path);
    const hunks: DiffHunk[] = [];
    let added = 0;
    let deleted = 0;

    let i = 1;
    // Skip --- and +++ lines
    while (i < lines.length && (lines[i].startsWith('--- ') || lines[i].startsWith('+++ ') || lines[i].startsWith('index ') || lines[i].startsWith('new file ') || lines[i].startsWith('deleted file ') || lines[i].startsWith('rename ') || lines[i].startsWith('similarity ') || lines[i].startsWith('old mode ') || lines[i].startsWith('new mode ') || lines[i] === '')) {
      i++;
    }

    // Parse hunks
    while (i < lines.length) {
      const line = lines[i];
      if (!line) { i++; continue; }

      // Hunk header: @@ -oldStart,oldCount +newStart,newCount @@
      const hunkMatch = line.match(/^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)/);
      if (hunkMatch) {
        const oldStart = parseInt(hunkMatch[1], 10);
        const oldCount = parseInt(hunkMatch[2] || '1', 10);
        const newStart = parseInt(hunkMatch[3], 10);
        const newCount = parseInt(hunkMatch[4] || '1', 10);
        const context = hunkMatch[5]?.trim() || '';

        const hunk: DiffHunk = {
          header: `@@ -${hunkMatch[1]},${oldCount} +${hunkMatch[3]},${newCount} @@${context ? ' ' + context : ''}`,
          oldStart, oldCount, newStart, newCount,
          lines: [],
        };

        i++;
        let curOld = oldStart;
        let curNew = newStart;

        while (i < lines.length) {
          const hunkLine = lines[i];
          if (!hunkLine || hunkLine.startsWith('@@') || hunkLine.startsWith('diff --git ')) break;

          if (hunkLine.startsWith('+')) {
            hunk.lines.push({ type: 'add', oldLine: null, newLine: curNew, content: hunkLine.slice(1) });
            curNew++;
            added++;
          } else if (hunkLine.startsWith('-')) {
            hunk.lines.push({ type: 'del', oldLine: curOld, newLine: null, content: hunkLine.slice(1) });
            curOld++;
            deleted++;
          } else {
            const content = hunkLine.startsWith(' ') ? hunkLine.slice(1) : hunkLine;
            hunk.lines.push({ type: 'context', oldLine: curOld, newLine: curNew, content });
            curOld++;
            curNew++;
          }
          i++;
        }
        hunks.push(hunk);
      } else {
        i++;
      }
    }

    files.push({ path, oldPath, lang, hunks, added, deleted });
  }

  return files;
}

// ── Inline syntax highlighting (lightweight, no heavy deps) ────────

const KEYWORDS: Record<string, RegExp> = {
  typescript: /\b(export|import|from|const|let|var|function|return|if|else|for|while|class|interface|type|extends|implements|new|this|super|async|await|try|catch|throw|typeof|instanceof|in|of|default|switch|case|break|continue|yield|void|never|any|boolean|string|number|symbol|null|undefined|true|false|readonly|private|public|protected|static|abstract|declare|enum|namespace|module|keyof|as|is)\b/,
  python: /\b(def|return|if|elif|else|for|while|class|import|from|as|try|except|finally|raise|with|yield|lambda|pass|break|continue|and|or|not|in|is|None|True|False|self|async|await|global|nonlocal|assert|del)\b/,
  rust: /\b(fn|let|mut|const|return|if|else|for|while|loop|match|struct|enum|impl|trait|pub|use|mod|where|as|in|ref|move|unsafe|extern|crate|super|self|Self|true|false|type|async|await|dyn|box)\b/,
  go: /\b(func|return|if|else|for|range|var|const|type|struct|interface|map|chan|go|defer|select|package|import|switch|case|break|continue|fallthrough|nil|true|false|make|new|append|len|cap|panic|recover)\b/,
  javascript: /\b(export|import|from|const|let|var|function|return|if|else|for|while|class|extends|new|this|super|async|await|try|catch|throw|typeof|instanceof|in|of|default|switch|case|break|continue|yield|void|true|false|null|undefined)\b/,
};

function highlightTokens(line: string, lang: string): (string | JSX.Element)[] {
  const kw = KEYWORDS[lang];
  if (!kw) return [line];

  const parts: (string | JSX.Element)[] = [];
  let remaining = line;
  let key = 0;

  // Highlight strings
  const strRe = /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)/g;
  let lastStrIdx = 0;
  let match: RegExpExecArray | null;

  while ((match = strRe.exec(line)) !== null) {
    // Process text before string
    const before = line.slice(lastStrIdx, match.index);
    if (before) {
      parts.push(...highlightKeywords(before, kw, key));
      key += 10;
    }
    parts.push(<span key={key++} className="text-[#C9A06C] dark:text-[#E5B87B]">{match[0]}</span>);
    lastStrIdx = match.index + match[0].length;
  }

  if (lastStrIdx < line.length) {
    parts.push(...highlightKeywords(line.slice(lastStrIdx), kw, key));
  }

  return parts.length > 0 ? parts : [line];
}

function highlightKeywords(text: string, kw: RegExp, keyBase: number): (string | JSX.Element)[] {
  const parts: (string | JSX.Element)[] = [];
  let remaining = text;
  let key = keyBase;
  let match: RegExpExecArray | null;

  while ((match = kw.exec(remaining)) !== null) {
    if (match.index > 0) {
      parts.push(remaining.slice(0, match.index));
    }
    parts.push(<span key={key++} className="font-semibold text-[#5A7ECF] dark:text-[#7B9BEF]">{match[0]}</span>);
    remaining = remaining.slice(match.index + match[0].length);
    kw.lastIndex = 0; // Reset since we sliced
  }

  if (remaining) parts.push(remaining);
  // Comment highlighting
  const result: (string | JSX.Element)[] = [];
  parts.forEach(p => {
    if (typeof p === 'string') {
      const commentMatch = p.match(/(\/\/.*$|#.*$)/);
      if (commentMatch && commentMatch.index !== undefined) {
        if (commentMatch.index > 0) result.push(p.slice(0, commentMatch.index));
        result.push(<span key={key++} className="italic text-warm-400 dark:text-[#6A9955]">{commentMatch[0]}</span>);
      } else {
        result.push(p);
      }
    } else {
      result.push(p);
    }
  });

  return result;
}

// ── Icons ───────────────────────────────────────────────────────────

function FileIcon({ lang }: { lang: string }) {
  const cls = 'h-4 w-4 shrink-0';
  const colorMap: Record<string, string> = {
    typescript: 'text-[#3178C6]', javascript: 'text-[#F7DF1E]',
    python: 'text-[#3776AB]', rust: 'text-[#DEA584]', go: 'text-[#00ADD8]',
    json: 'text-[#F5A623]', markdown: 'text-[#4F6CF7]',
    css: 'text-[#1572B6]', html: 'text-[#E34F26]',
    shell: 'text-[#4EAA25]', sql: 'text-[#E38C00]',
  };
  const color = colorMap[lang] || 'text-warm-400';

  return (
    <svg className={`${cls} ${color}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
    </svg>
  );
}

function AddedIcon() {
  return <svg className="h-3.5 w-3.5 shrink-0 text-green-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>;
}

function DeletedIcon() {
  return <svg className="h-3.5 w-3.5 shrink-0 text-red-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><line x1="5" y1="12" x2="19" y2="12"/></svg>;
}

function ChevronIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg
      className={`h-4 w-4 shrink-0 text-warm-400 transition-transform ${expanded ? 'rotate-90' : ''}`}
      viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
    >
      <polyline points="9 18 15 12 9 6"/>
    </svg>
  );
}

// ── Main component ──────────────────────────────────────────────────

interface CodeReviewPanelProps {
  content: string;
}

export default function CodeReviewPanel({ content }: CodeReviewPanelProps): JSX.Element {
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());
  const [copiedPath, setCopiedPath] = useState<string | null>(null);

  const files = useMemo(() => parseDiff(content), [content]);

  const totalAdded = useMemo(() => files.reduce((s, f) => s + f.added, 0), [files]);
  const totalDeleted = useMemo(() => files.reduce((s, f) => s + f.deleted, 0), [files]);

  // Auto-expand first file on new diff
  useEffect(() => {
    if (files.length > 0) {
      setExpandedFiles(new Set([files[0].path]));
    }
  }, [files]);

  if (files.length === 0) {
    return (
      <div className="rounded-xl border border-warm-200 bg-warm-100 p-6 text-center text-sm text-warm-500">
        未检测到有效的 diff 内容
      </div>
    );
  }

  function toggleFile(path: string) {
    setExpandedFiles(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  }

  function expandAll() {
    setExpandedFiles(new Set(files.map(f => f.path)));
  }

  function collapseAll() {
    setExpandedFiles(new Set());
  }

  async function copyPath(path: string) {
    try {
      await navigator.clipboard.writeText(path);
      setCopiedPath(path);
      setTimeout(() => setCopiedPath(null), 2000);
    } catch { /* noop */ }
  }

  return (
    <div className="my-3 overflow-hidden rounded-2xl border border-warm-200 bg-warm-100 shadow-card">
      {/* ── Summary header ───────────────────────────────────────── */}
      <div className="border-b border-warm-150 bg-[#FBFAF8] px-5 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1.5 text-sm font-semibold text-warm-800">
              <svg className="h-4 w-4 text-warm-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
              </svg>
              代码审查
            </span>
            <span className="text-xs text-warm-500">
              <strong className="text-warm-700">{files.length}</strong> 个文件已更改
            </span>
            <span className="flex items-center gap-1 text-xs">
              <span className="inline-flex items-center gap-0.5 font-semibold text-green-600">
                <AddedIcon />+{totalAdded}
              </span>
              <span className="text-warm-300">·</span>
              <span className="inline-flex items-center gap-0.5 font-semibold text-red-600">
                <DeletedIcon />-{totalDeleted}
              </span>
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              className="rounded-md px-2.5 py-1 text-[11px] font-medium text-warm-500 hover:bg-warm-100 hover:text-warm-700 transition-colors"
              onClick={expandAll}
            >
              展开
            </button>
            <button
              className="rounded-md px-2.5 py-1 text-[11px] font-medium text-warm-500 hover:bg-warm-100 hover:text-warm-700 transition-colors"
              onClick={collapseAll}
            >
              折叠
            </button>
          </div>
        </div>
      </div>

      {/* ── File list ────────────────────────────────────────────── */}
      <div className="divide-y divide-warm-100">
        {files.map((file) => {
          const isExpanded = expandedFiles.has(file.path);
          return (
            <div key={file.path}>
              {/* File header — clickable to expand/collapse */}
              <button
                className="flex w-full items-center gap-2.5 px-5 py-2.5 hover:bg-warm-50 transition-colors text-left"
                onClick={() => toggleFile(file.path)}
              >
                <ChevronIcon expanded={isExpanded} />
                <FileIcon lang={file.lang} />
                <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-warm-800">
                  {file.path}
                </span>
                <span className="flex items-center gap-3 text-[11px] shrink-0">
                  <span className="inline-flex items-center gap-0.5 font-medium text-green-600">
                    <AddedIcon />{file.added}
                  </span>
                  <span className="inline-flex items-center gap-0.5 font-medium text-red-600">
                    <DeletedIcon />{file.deleted}
                  </span>
                  <span
                    className="rounded border border-warm-200 bg-warm-100 px-1.5 py-0.5 text-[10px] text-warm-400 hover:text-warm-600 transition-colors"
                    onClick={(e) => { e.stopPropagation(); copyPath(file.path); }}
                    title="复制路径"
                  >
                    {copiedPath === file.path ? '已复制 [ok]' : '复制'}
                  </span>
                </span>
              </button>

              {/* Diff content — shown when expanded */}
              {isExpanded && (
                <div className="border-t border-warm-100 bg-[#FBFBF9]">
                  {file.hunks.length === 0 ? (
                    <div className="px-6 py-4 text-xs text-warm-400">
                      [warn] 无法解析 diff 内容 — 请检查格式
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full font-mono text-[12px] leading-5">
                        <tbody>
                          {file.hunks.map((hunk, hi) => (
                            <React.Fragment key={hi}>
                              {/* Hunk header */}
                              <tr className="bg-[#F0EFEB]/60">
                                <td colSpan={3} className="px-5 py-1 text-[11px] text-[#7B6F9B] font-medium select-all">
                                  {hunk.header}
                                </td>
                              </tr>
                              {/* Hunk lines */}
                              {hunk.lines.map((line, li) => (
                                <tr
                                  key={`${hi}-${li}`}
                                  className={
                                    line.type === 'add'
                                      ? 'bg-[#E8F5E9]/70 hover:bg-[#DCF1DD]'
                                      : line.type === 'del'
                                        ? 'bg-[#FFEBEE]/70 hover:bg-[#FFDDDF]'
                                        : 'hover:bg-warm-50/50'
                                  }
                                >
                                  {/* Old line number */}
                                  <td className={`w-[44px] min-w-[44px] select-none px-2 text-right text-[10px] tabular-nums ${
                                    line.type === 'del' ? 'text-red-400 bg-red-50' : line.type === 'add' ? 'text-transparent' : 'text-warm-400'
                                  }`}>
                                    {line.oldLine ?? ''}
                                  </td>
                                  {/* New line number */}
                                  <td className={`w-[44px] min-w-[44px] select-none px-2 text-right text-[10px] tabular-nums ${
                                    line.type === 'add' ? 'text-green-500 bg-green-50' : line.type === 'del' ? 'text-transparent' : 'text-warm-400'
                                  }`}>
                                    {line.newLine ?? ''}
                                  </td>
                                  {/* Prefix + Code */}
                                  <td className={`px-2 whitespace-pre-wrap break-all ${
                                    line.type === 'add'
                                      ? 'text-green-800'
                                      : line.type === 'del'
                                        ? 'text-red-800'
                                        : 'text-warm-700'
                                  }`}>
                                    <span className="select-none mr-1 font-bold">
                                      {line.type === 'add' ? '+' : line.type === 'del' ? '-' : ' '}
                                    </span>
                                    {highlightTokens(line.content, file.lang)}
                                  </td>
                                </tr>
                              ))}
                            </React.Fragment>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* ── Footer: summary & operations ─────────────────────────── */}
      <div className="border-t border-warm-150 bg-[#FBFAF8] px-5 py-2.5">
        <div className="flex items-center justify-between gap-3 text-[11px] text-warm-400">
          <span>
            变更统计：<span className="font-medium text-green-600">+{totalAdded}</span> <span className="font-medium text-red-600">-{totalDeleted}</span> 行 · {files.length} 个文件
          </span>
          <span className="text-warm-300">
            [idea] 输入 <kbd className="rounded border border-warm-200 bg-warm-100 px-1 text-[10px]">展开</kbd> / <kbd className="rounded border border-warm-200 bg-warm-100 px-1 text-[10px]">折叠</kbd> 切换 · <kbd className="rounded border border-warm-200 bg-warm-100 px-1 text-[10px]">汇总</kbd> 查看摘要
          </span>
        </div>
      </div>
    </div>
  );
}
