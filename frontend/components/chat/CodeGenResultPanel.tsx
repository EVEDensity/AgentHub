import { memo, useCallback, useMemo, useState, type JSX } from 'react';

// ══════════════════════════════════════════════════════════════════════════════
// Types
// ══════════════════════════════════════════════════════════════════════════════

interface CodeGenFile {
  path: string;
  content: string;
  language?: string;
}

interface ParsedCodeGen {
  files: CodeGenFile[];
  prelude: string;
  suffix: string;
}

// ══════════════════════════════════════════════════════════════════════════════
// Language detection
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
  tf: 'hcl', tfvars: 'hcl',
  proto: 'protobuf',
};

const LANG_LABEL: Record<string, string> = {
  typescript: 'TS', tsx: 'TSX', javascript: 'JS', jsx: 'JSX',
  python: 'PY', rust: 'RS', go: 'GO', java: 'JAVA', kotlin: 'KT',
  swift: 'SWIFT', c: 'C', cpp: 'C++', csharp: 'C#', fsharp: 'F#',
  ruby: 'RB', php: 'PHP', perl: 'PL', bash: 'SH', powershell: 'PS1',
  sql: 'SQL', graphql: 'GQL', html: 'HTML', css: 'CSS', scss: 'SCSS',
  less: 'LESS', json: 'JSON', yaml: 'YAML', toml: 'TOML', ini: 'INI',
  markdown: 'MD', mdx: 'MDX', vue: 'VUE', svelte: 'SVELTE',
  astro: 'ASTRO', dockerfile: 'DOCKERFILE', hcl: 'HCL', protobuf: 'PROTO',
  text: 'TEXT',
};

function detectLang(path: string): string {
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

function langLabel(lang: string): string {
  return LANG_LABEL[lang] || lang.slice(0, 4).toUpperCase();
}

// ══════════════════════════════════════════════════════════════════════════════
// Parsing
// ══════════════════════════════════════════════════════════════════════════════

function parseCodeGenOutput(text: string): ParsedCodeGen | null {
  const startMarker = /\{\s*"files"\s*:\s*\[/;
  const match = startMarker.exec(text);
  if (!match) return null;

  const startIdx = match.index;
  const prelude = text.slice(0, startIdx).trim();

  let depth = 0;
  let inString = false;
  let escape = false;
  let endIdx = -1;

  for (let i = startIdx; i < text.length; i++) {
    const ch = text[i];
    if (escape) { escape = false; continue; }
    if (ch === '\\') { escape = true; continue; }
    if (ch === '"') { inString = !inString; continue; }
    if (inString) continue;
    if (ch === '{' || ch === '[') { depth++; continue; }
    if (ch === '}' || ch === ']') { depth--; if (depth === 0) { endIdx = i + 1; break; } }
  }

  if (endIdx === -1) return null;

  const jsonBlock = text.slice(startIdx, endIdx);
  const suffix = text.slice(endIdx).trim();

  try {
    const parsed = JSON.parse(jsonBlock);
    if (!Array.isArray(parsed?.files)) return null;

    const files: CodeGenFile[] = parsed.files.map((f: Record<string, unknown>) => ({
      path: typeof f.path === 'string' ? f.path : 'unknown',
      content: typeof f.content === 'string' ? f.content : JSON.stringify(f.content, null, 2),
      language: typeof f.language === 'string' ? f.language : detectLang(typeof f.path === 'string' ? f.path : ''),
    }));

    if (files.length === 0) return null;
    return { files, prelude, suffix };
  } catch {
    return null;
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Syntax highlighting — keyword-aware, produces inline-colored HTML
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

const DECORATOR_RE = /^(\s*)(@\w+)/;
const NUMBER_RE = /\b(\d+\.?\d*)\b/g;
const STRING_RE_SQ = /('[^']*')/g;
const STRING_RE_DQ = /("[^"]*")/g;
const COMMENT_PY_RE = /(#.*)$/;
const COMMENT_JS_RE = /(\/\/.*)$/;

function highlightLine(line: string, lang: string): string {
  let escaped = line
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  if (lang === 'python') {
    const dec = DECORATOR_RE.exec(escaped);
    if (dec) {
      escaped = dec[1] + '<span style="color:#C4A000">' + dec[2] + '</span>';
    }
    escaped = escaped.replace(STRING_RE_SQ, '<span style="color:#98C379">$1</span>');
    escaped = escaped.replace(STRING_RE_DQ, '<span style="color:#98C379">$1</span>');
    escaped = escaped.replace(COMMENT_PY_RE, '<span style="color:#6A9955;font-style:italic">$1</span>');
    escaped = escaped.replace(
      /\b([a-zA-Z_]\w*)\b/g,
      (word) => PY_KEYWORDS.has(word)
        ? '<span style="color:#569CD6;font-weight:500">' + word + '</span>'
        : word,
    );
    escaped = escaped.replace(NUMBER_RE, '<span style="color:#D19A66">$1</span>');
    return escaped;
  }

  if (['typescript', 'tsx', 'javascript', 'jsx', 'java', 'c', 'cpp', 'csharp', 'go', 'rust', 'swift', 'kotlin', 'php'].includes(lang)) {
    escaped = escaped.replace(STRING_RE_SQ, '<span style="color:#98C379">$1</span>');
    escaped = escaped.replace(STRING_RE_DQ, '<span style="color:#98C379">$1</span>');
    escaped = escaped.replace(COMMENT_JS_RE, '<span style="color:#6A9955;font-style:italic">$1</span>');
    escaped = escaped.replace(
      /\b([a-zA-Z_$]\w*)\b/g,
      (word) => JS_KEYWORDS.has(word)
        ? '<span style="color:#569CD6;font-weight:500">' + word + '</span>'
        : word,
    );
    escaped = escaped.replace(NUMBER_RE, '<span style="color:#D19A66">$1</span>');
    return escaped;
  }

  if (lang === 'css' || lang === 'scss' || lang === 'less') {
    escaped = escaped.replace(/(\/\*[\s\S]*?\*\/)/g, '<span style="color:#6A9955;font-style:italic">$1</span>');
    escaped = escaped.replace(STRING_RE_DQ, '<span style="color:#98C379">$1</span>');
    escaped = escaped.replace(STRING_RE_SQ, '<span style="color:#98C379">$1</span>');
    return escaped;
  }

  if (lang === 'bash' || lang === 'dockerfile' || lang === 'makefile') {
    escaped = escaped.replace(COMMENT_PY_RE, '<span style="color:#6A9955;font-style:italic">$1</span>');
    escaped = escaped.replace(STRING_RE_DQ, '<span style="color:#98C379">$1</span>');
    escaped = escaped.replace(STRING_RE_SQ, '<span style="color:#98C379">$1</span>');
    return escaped;
  }

  if (lang === 'sql') {
    escaped = escaped.replace(/(--.*)$/, '<span style="color:#6A9955;font-style:italic">$1</span>');
    escaped = escaped.replace(STRING_RE_SQ, '<span style="color:#98C379">$1</span>');
    return escaped;
  }

  escaped = escaped.replace(STRING_RE_DQ, '<span style="color:#98C379">$1</span>');
  escaped = escaped.replace(STRING_RE_SQ, '<span style="color:#98C379">$1</span>');
  return escaped;
}

// ══════════════════════════════════════════════════════════════════════════════
// File icon SVGs — simple, consistent line-art style
// ══════════════════════════════════════════════════════════════════════════════

function FileIcon({ lang }: { lang: string }): JSX.Element {
  const cls = 'h-5 w-5 shrink-0';

  if (lang === 'python') return (
    <svg className={cls} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="2" width="18" height="20" rx="2" fill="#306998" opacity="0.15"/>
      <path d="M8 6h8v2l-2 1 2 1v2H8" stroke="#306998" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
    </svg>
  );

  if (lang === 'typescript' || lang === 'tsx') return (
    <svg className={cls} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="2" width="18" height="20" rx="2" fill="#3178C6" opacity="0.15"/>
      <text x="12" y="17" textAnchor="middle" fill="#3178C6" fontSize="10" fontWeight="bold" fontFamily="monospace">TS</text>
    </svg>
  );

  if (lang === 'javascript' || lang === 'jsx') return (
    <svg className={cls} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="2" width="18" height="20" rx="2" fill="#F7DF1E" opacity="0.2"/>
      <text x="12" y="17" textAnchor="middle" fill="#BBA400" fontSize="10" fontWeight="bold" fontFamily="monospace">JS</text>
    </svg>
  );

  if (lang === 'json') return (
    <svg className={cls} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="2" width="18" height="20" rx="2" fill="#5E5E5E" opacity="0.15"/>
      <text x="12" y="14" textAnchor="middle" fill="#5E5E5E" fontSize="6" fontWeight="bold" fontFamily="monospace">{'{ }'}</text>
    </svg>
  );

  if (lang === 'yaml' || lang === 'toml' || lang === 'ini') return (
    <svg className={cls} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="2" width="18" height="20" rx="2" fill="#6366F1" opacity="0.1"/>
      <path d="M8 7h8M8 11h6M8 15h4" stroke="#6366F1" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  );

  if (lang === 'markdown' || lang === 'mdx') return (
    <svg className={cls} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="2" width="18" height="20" rx="2" fill="#6B7280" opacity="0.12"/>
      <text x="12" y="16" textAnchor="middle" fill="#6B7280" fontSize="7" fontWeight="bold" fontFamily="monospace">MD</text>
    </svg>
  );

  if (lang === 'css' || lang === 'scss' || lang === 'less') return (
    <svg className={cls} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="2" width="18" height="20" rx="2" fill="#2563EB" opacity="0.12"/>
      <text x="12" y="16" textAnchor="middle" fill="#2563EB" fontSize="8" fontWeight="bold" fontFamily="monospace">#</text>
    </svg>
  );

  if (lang === 'html') return (
    <svg className={cls} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="2" width="18" height="20" rx="2" fill="#EA580C" opacity="0.12"/>
      <text x="10" y="16" textAnchor="middle" fill="#EA580C" fontSize="7" fontWeight="bold" fontFamily="monospace">&lt;/</text>
      <text x="15" y="16" textAnchor="middle" fill="#EA580C" fontSize="7" fontWeight="bold" fontFamily="monospace">&gt;</text>
    </svg>
  );

  if (lang === 'sql') return (
    <svg className={cls} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="2" width="18" height="20" rx="2" fill="#0EA5E9" opacity="0.12"/>
      <text x="12" y="16" textAnchor="middle" fill="#0EA5E9" fontSize="8" fontWeight="bold" fontFamily="monospace">DB</text>
    </svg>
  );

  if (lang === 'dockerfile' || lang === 'bash') return (
    <svg className={cls} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="2" width="18" height="20" rx="2" fill="#0891B2" opacity="0.12"/>
      <path d="M7 8l3 3-3 3" stroke="#0891B2" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M11 16h5" stroke="#0891B2" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  );

  if (lang === 'go') return (
    <svg className={cls} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="2" width="18" height="20" rx="2" fill="#00ADD8" opacity="0.15"/>
      <text x="12" y="16" textAnchor="middle" fill="#00ADD8" fontSize="13" fontWeight="bold" fontFamily="monospace">G</text>
    </svg>
  );

  if (lang === 'rust') return (
    <svg className={cls} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="2" width="18" height="20" rx="2" fill="#DEA584" opacity="0.2"/>
      <text x="12" y="16" textAnchor="middle" fill="#DEA584" fontSize="8" fontWeight="bold" fontFamily="monospace">RS</text>
    </svg>
  );

  if (lang === 'java') return (
    <svg className={cls} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="2" width="18" height="20" rx="2" fill="#ED8B00" opacity="0.15"/>
      <text x="12" y="16" textAnchor="middle" fill="#ED8B00" fontSize="13" fontWeight="bold" fontFamily="monospace">J</text>
    </svg>
  );

  return (
    <svg className={cls} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="2" width="18" height="20" rx="2" fill="#94A3B8" opacity="0.15"/>
      <path d="M8 7h8M8 11h8M8 15h5" stroke="#94A3B8" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Main Component
// ══════════════════════════════════════════════════════════════════════════════

interface CodeGenResultPanelProps {
  content: string;
}

export { parseCodeGenOutput };
export type { CodeGenFile, ParsedCodeGen };

const CodeGenResultPanel = memo(function CodeGenResultPanel({ content }: CodeGenResultPanelProps): JSX.Element | null {
  const parsed = useMemo(() => parseCodeGenOutput(content), [content]);

  const [expanded, setExpanded] = useState<Set<number>>(() => {
    if (parsed?.files.length) return new Set([0]);
    return new Set();
  });
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [animating, setAnimating] = useState<Set<number>>(new Set());

  const toggle = useCallback((idx: number) => {
    setAnimating((prev) => { const n = new Set(prev); n.add(idx); return n; });
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx); else next.add(idx);
      return next;
    });
    setTimeout(() => setAnimating((prev) => { const n = new Set(prev); n.delete(idx); return n; }), 250);
  }, []);

  const expandAll = useCallback(() => {
    if (!parsed) return;
    const allExpanded = expanded.size === parsed.files.length;
    parsed.files.forEach((_, i) => setAnimating((prev) => { const n = new Set(prev); n.add(i); return n; }));
    if (allExpanded) {
      setExpanded(new Set());
    } else {
      setExpanded(new Set(parsed.files.map((_, i) => i)));
    }
    setTimeout(() => setAnimating(new Set()), 250);
  }, [parsed, expanded.size]);

  async function copyFile(idx: number, text: string): Promise<void> {
    await navigator.clipboard.writeText(text);
    setCopiedId(idx);
    setTimeout(() => setCopiedId(null), 2000);
  }

  if (!parsed) return null;

  const totalLines = parsed.files.reduce((s, f) => s + f.content.split('\n').length, 0);
  const totalSize = parsed.files.reduce((s, f) => s + new Blob([f.content]).size, 0);

  return (
    <div className="codegen-result my-3">
      {/* ── Card shell ──────────────────────────────────────────── */}
      <div
        className="overflow-hidden bg-white"
        style={{
          borderRadius: '12px',
          border: '1px solid #E2E8F0',
          boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)',
        }}
      >
        {/* ── Header ────────────────────────────────────────────── */}
        <div
          className="flex items-center justify-between gap-3 px-5 py-3.5"
          style={{ borderBottom: '1px solid #F1F5F9' }}
        >
          <div className="flex items-center gap-3 min-w-0">
            {/* CodeGen icon */}
            <div
              className="inline-flex h-9 w-9 shrink-0 items-center justify-center"
              style={{ borderRadius: '10px', background: '#EFF6FF' }}
            >
              <svg className="h-4.5 w-4.5" viewBox="0 0 24 24" fill="none" stroke="#3B82F6" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="16 18 22 12 16 6" />
                <polyline points="8 6 2 12 8 18" />
              </svg>
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span style={{ fontSize: '15px', fontWeight: 600, color: '#1E293B', lineHeight: 1.3 }}>
                  CodeGen 已生成 <span style={{ color: '#3B82F6' }}>{parsed.files.length}</span> 个文件
                </span>
              </div>
              <span style={{ fontSize: '13px', color: '#94A3B8', lineHeight: 1 }}>
                {totalLines.toLocaleString()} 行 · {(totalSize / 1024).toFixed(1)} KB
              </span>
            </div>
          </div>
          <button
            className="shrink-0 text-sm font-medium transition-colors hover:underline"
            style={{
              color: '#3B82F6',
              fontSize: '13px',
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              padding: '4px 0',
            }}
            onClick={expandAll}
          >
            {expanded.size === parsed.files.length ? '全部折叠' : '全部展开'}
          </button>
        </div>

        {/* ── File list ─────────────────────────────────────────── */}
        <div style={{ background: '#F8FAFC' }}>
          {parsed.files.map((file, idx) => {
            const lines = file.content.split('\n');
            const lang = file.language || detectLang(file.path);
            const isOpen = expanded.has(idx);
            const fileSize = new Blob([file.content]).size;
            const isAnimating = animating.has(idx);

            return (
              <div
                key={`${file.path}-${idx}`}
                style={{
                  borderBottom: idx < parsed.files.length - 1 ? '1px solid #E2E8F0' : 'none',
                }}
              >
                {/* ── File header (clickable row) ──────────────── */}
                <button
                  onClick={() => toggle(idx)}
                  className="flex w-full items-center gap-3 px-5 py-3 text-left transition-colors"
                  style={{
                    background: 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                  }}
                  onMouseEnter={(e) => { if (!isOpen) (e.currentTarget as HTMLElement).style.background = '#F1F5F9'; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
                >
                  {/* Chevron */}
                  <svg
                    className="h-4 w-4 shrink-0 transition-transform select-none"
                    style={{
                      color: '#94A3B8',
                      transform: isOpen ? 'rotate(90deg)' : 'rotate(0deg)',
                      transitionDuration: '200ms',
                      transitionTimingFunction: 'cubic-bezier(0.4, 0, 0.2, 1)',
                    }}
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M9 18l6-6-6-6" />
                  </svg>

                  {/* File icon */}
                  <FileIcon lang={lang} />

                  {/* File info */}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <code
                        className="truncate"
                        style={{
                          fontSize: '14px',
                          fontWeight: 500,
                          color: '#1E293B',
                          fontFamily: "'JetBrains Mono', 'Cascadia Code', 'Consolas', 'Fira Code', monospace",
                        }}
                      >
                        {file.path}
                      </code>
                      <span
                        className="shrink-0 select-none"
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          borderRadius: '6px',
                          padding: '2px 8px',
                          fontSize: '11px',
                          fontWeight: 600,
                          letterSpacing: '0.02em',
                          background: 'rgba(59, 130, 246, 0.08)',
                          color: '#3B82F6',
                        }}
                      >
                        {langLabel(lang)}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-0.5" style={{ fontSize: '12px', color: '#94A3B8' }}>
                      <span>{lines.length} 行</span>
                      <span>{(fileSize / 1024).toFixed(1)} KB</span>
                      {isOpen && <span style={{ color: '#3B82F6', fontSize: '11px' }}>展开中</span>}
                    </div>
                  </div>

                  {/* Copy button */}
                  <button
                    className={`shrink-0 transition-all duration-200 ${isOpen ? '' : ''}`}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '4px',
                      borderRadius: '8px',
                      padding: '6px 10px',
                      fontSize: '12px',
                      fontWeight: 500,
                      border: 'none',
                      cursor: 'pointer',
                      background: copiedId === idx ? '#ECFDF5' : (isOpen ? 'transparent' : 'transparent'),
                      color: copiedId === idx ? '#059669' : '#94A3B8',
                      opacity: isOpen ? 1 : 0,
                    }}
                    onClick={(e) => { e.stopPropagation(); copyFile(idx, file.content); }}
                    onMouseEnter={(e) => {
                      if (copiedId !== idx) {
                        (e.currentTarget as HTMLElement).style.background = '#F1F5F9';
                        (e.currentTarget as HTMLElement).style.color = '#64748B';
                      }
                    }}
                    onMouseLeave={(e) => {
                      if (copiedId !== idx) {
                        (e.currentTarget as HTMLElement).style.background = 'transparent';
                        (e.currentTarget as HTMLElement).style.color = '#94A3B8';
                      }
                    }}
                  >
                    {copiedId === idx ? (
                      <>
                        <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                        已复制
                      </>
                    ) : (
                      <>
                        <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                        </svg>
                        复制
                      </>
                    )}
                  </button>
                </button>

                {/* ── Code area (collapsible) ──────────────────── */}
                <div
                  style={{
                    overflow: 'hidden',
                    maxHeight: isOpen ? (isAnimating ? '5000px' : 'none') : '0',
                    opacity: isOpen ? 1 : 0,
                    transition: isOpen
                      ? 'max-height 250ms cubic-bezier(0.4, 0, 0.2, 1), opacity 200ms ease'
                      : 'max-height 200ms cubic-bezier(0.4, 0, 0.2, 1), opacity 150ms ease',
                  }}
                >
                  <div
                    style={{
                      background: '#1E1E1E',
                      borderTop: '1px solid #2D2D2D',
                    }}
                  >
                    {/* Code header bar */}
                    <div
                      className="flex items-center justify-between px-5 py-2"
                      style={{ background: '#161616', borderBottom: '1px solid #2D2D2D' }}
                    >
                      <code
                        style={{
                          fontSize: '12px',
                          fontFamily: "'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace",
                          color: '#808080',
                        }}
                      >
                        {file.path}
                      </code>
                      <span style={{ fontSize: '11px', color: '#6A6A6A' }}>{lines.length} lines</span>
                    </div>
                    {/* Code content */}
                    <div className="overflow-x-auto" style={{ maxHeight: '480px', overflowY: 'auto' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <tbody>
                          {lines.slice(0, 300).map((line, li) => (
                            <tr
                              key={li}
                              style={{ background: li % 2 === 0 ? '#1E1E1E' : '#1A1A1A' }}
                              onMouseEnter={(e) => {
                                (e.currentTarget as HTMLElement).style.background = '#2A2A2A';
                              }}
                              onMouseLeave={(e) => {
                                (e.currentTarget as HTMLElement).style.background = li % 2 === 0 ? '#1E1E1E' : '#1A1A1A';
                              }}
                            >
                              {/* Line number */}
                              <td
                                className="select-none"
                                style={{
                                  borderRight: '1px solid #2D2D2D',
                                  background: '#161616',
                                  padding: '0 12px',
                                  textAlign: 'right',
                                  fontSize: '12px',
                                  lineHeight: '24px',
                                  color: '#5A5A5A',
                                  fontFamily: "'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace",
                                  whiteSpace: 'nowrap',
                                  width: '1%',
                                  verticalAlign: 'top',
                                }}
                              >
                                {li + 1}
                              </td>
                              {/* Code line */}
                              <td style={{ padding: '0 16px', verticalAlign: 'top' }}>
                                <pre
                                  style={{
                                    margin: 0,
                                    fontSize: '14px',
                                    lineHeight: '24px',
                                    color: '#D4D4D4',
                                    fontFamily: "'JetBrains Mono', 'Cascadia Code', 'Consolas', 'Fira Code', monospace",
                                    whiteSpace: 'pre',
                                    tabSize: 4,
                                  }}
                                  dangerouslySetInnerHTML={{ __html: highlightLine(line, lang) }}
                                />
                              </td>
                            </tr>
                          ))}
                          {lines.length > 300 && (
                            <tr>
                              <td
                                style={{
                                  borderRight: '1px solid #2D2D2D',
                                  background: '#161616',
                                  padding: '8px 12px',
                                  textAlign: 'right',
                                  fontSize: '12px',
                                  color: '#6A6A6A',
                                  fontFamily: "'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace",
                                }}
                              >
                                …
                              </td>
                              <td style={{ padding: '8px 16px', fontSize: '12px', color: '#808080', fontStyle: 'italic' }}>
                                仅显示前 300 行，共 {lines.length} 行
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {/* ── Bottom bar ────────────────────────────────────────── */}
        <div
          className="flex items-center justify-between px-5 py-2.5"
          style={{ borderTop: '1px solid #F1F5F9', background: '#F8FAFC' }}
        >
          <span style={{ fontSize: '12px', color: '#94A3B8' }}>
            所有文件均可点击展开查看完整内容
          </span>
          <span style={{ fontSize: '12px', color: '#94A3B8' }}>
            共 {parsed.files.length} 个文件 · {totalLines.toLocaleString()} 行
          </span>
        </div>
      </div>

      {/* ── Inline style for hover-row transition ─────────────── */}
      <style>{`
        .codegen-result .hover-row { transition: background 80ms ease; }
      `}</style>
    </div>
  );
});

export default CodeGenResultPanel;

// ══════════════════════════════════════════════════════════════════════════════
// Detection helper
// ══════════════════════════════════════════════════════════════════════════════

export function isCodeGenOutput(text: string): boolean {
  return /\{\s*"files"\s*:\s*\[/.test(text);
}
