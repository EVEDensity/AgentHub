'use client';

import { type JSX } from 'react';

// ── Keyword sets for inline highlighting ─────────────────────────────

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

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// ── Inline code highlighter ─────────────────────────────────────────

interface InlineHighlightedCodeProps {
  value: string;
  language: string;
}

export default function InlineHighlightedCode({
  value,
  language,
}: InlineHighlightedCodeProps): JSX.Element {
  let escaped = escapeHtml(value);

  // Python
  if (language === 'python') {
    escaped = escaped.replace(/'[^']*'/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/"[^"]*"/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/(#.*)$/, '<span style="color:#6A9955;font-style:italic">$1</span>');
    escaped = escaped.replace(
      /\b([a-zA-Z_]\w*)\b/g,
      (word) =>
        PY_KEYWORDS.has(word)
          ? `<span style="color:#569CD6;font-weight:500">${word}</span>`
          : word,
    );
    escaped = escaped.replace(/\b(\d+\.?\d*)\b/g, '<span style="color:#D19A66">$1</span>');
  }
  // TypeScript / JavaScript / C-family
  else if (['typescript', 'tsx', 'javascript', 'jsx', 'java', 'c', 'cpp', 'csharp', 'go', 'rust', 'swift', 'kotlin', 'php'].includes(language)) {
    escaped = escaped.replace(/'[^']*'/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/"[^"]*"/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/(\/\/.*)$/, '<span style="color:#6A9955;font-style:italic">$1</span>');
    escaped = escaped.replace(
      /\b([a-zA-Z_$]\w*)\b/g,
      (word) =>
        JS_KEYWORDS.has(word)
          ? `<span style="color:#569CD6;font-weight:500">${word}</span>`
          : word,
    );
    escaped = escaped.replace(/\b(\d+\.?\d*)\b/g, '<span style="color:#D19A66">$1</span>');
  }
  // CSS / SCSS
  else if (language === 'css' || language === 'scss' || language === 'less') {
    escaped = escaped.replace(/"[^"]*"/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/'[^']*'/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/(\/\*[\s\S]*?\*\/)/g, '<span style="color:#6A9955;font-style:italic">$1</span>');
  }
  // Shell / Dockerfile
  else if (language === 'bash' || language === 'dockerfile' || language === 'makefile') {
    escaped = escaped.replace(/(#.*)$/, '<span style="color:#6A9955;font-style:italic">$1</span>');
    escaped = escaped.replace(/"[^"]*"/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/'[^']*'/g, '<span style="color:#98C379">$&</span>');
  }
  // SQL
  else if (language === 'sql') {
    escaped = escaped.replace(/(--.*)$/, '<span style="color:#6A9955;font-style:italic">$1</span>');
    escaped = escaped.replace(/'[^']*'/g, '<span style="color:#98C379">$&</span>');
  }
  // JSON
  else if (language === 'json') {
    escaped = escaped.replace(/"[^"]*"/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/\b(true|false|null)\b/g, '<span style="color:#569CD6">$1</span>');
    escaped = escaped.replace(/\b(\d+\.?\d*)\b/g, '<span style="color:#D19A66">$1</span>');
  }
  // Generic fallback
  else {
    escaped = escaped.replace(/"[^"]*"/g, '<span style="color:#98C379">$&</span>');
    escaped = escaped.replace(/'[^']*'/g, '<span style="color:#98C379">$&</span>');
  }

  return <span dangerouslySetInnerHTML={{ __html: escaped }} />;
}
