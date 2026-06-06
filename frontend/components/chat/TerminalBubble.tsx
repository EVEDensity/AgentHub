'use client';

import { useEffect, useRef, useState, useCallback, type JSX } from 'react';
import { Copy, Check, ChevronDown, ChevronUp, Terminal } from 'lucide-react';

interface TerminalBubbleProps {
  content: string;
  isStreaming?: boolean;
  language?: string;
}

export default function TerminalBubble({
  content,
  isStreaming,
  language = 'bash',
}: TerminalBubbleProps): JSX.Element {
  const [copied, setCopied] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when new content streams in
  useEffect(() => {
    if (isStreaming && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [content, isStreaming]);

  const handleCopy = useCallback(async () => {
    // Strip ANSI escape codes before copying
    const cleanText = content.replace(/\x1b\[[0-9;]*m/g, '');
    await navigator.clipboard.writeText(cleanText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [content]);

  const lineCount = content.split('\n').length;

  return (
    <div className="mt-2 overflow-hidden rounded-xl border border-gray-600 bg-[#1a1a2e] shadow-lg">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-700/60 bg-[#16213e] px-4 py-2">
        <div className="flex items-center gap-2 text-xs font-medium text-gray-300">
          <Terminal className="h-3.5 w-3.5 text-green-400" />
          <span>终端输出</span>
          <span className="rounded bg-gray-700/50 px-1.5 py-0.5 text-[10px] text-gray-400">
            {language}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={handleCopy}
            className="rounded px-2 py-1 text-xs text-gray-400 hover:bg-white/10 hover:text-white transition"
            title="复制输出"
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-green-400" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </button>
          {lineCount > 20 && (
            <button
              onClick={() => setCollapsed((v) => !v)}
              className="rounded px-2 py-1 text-xs text-gray-400 hover:bg-white/10 hover:text-white transition"
              title={collapsed ? '展开' : '折叠'}
            >
              {collapsed ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronUp className="h-3.5 w-3.5" />
              )}
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      {!collapsed && (
        <div className="max-h-96 overflow-auto bg-[#0d1117] p-4 font-mono text-sm leading-relaxed text-green-300">
          <pre className="whitespace-pre-wrap break-all">{content}</pre>
          {isStreaming && (
            <span className="ml-0.5 inline-block h-4 w-2 animate-pulse bg-green-400 align-middle" />
          )}
          <div ref={bottomRef} />
        </div>
      )}

      {/* Collapsed state */}
      {collapsed && (
        <div
          onClick={() => setCollapsed(false)}
          className="flex cursor-pointer items-center gap-2 px-4 py-2.5 text-xs text-gray-400 hover:bg-white/[0.03] transition"
        >
          <Terminal className="h-3.5 w-3.5" />
          <span>
            终端输出已折叠 ({lineCount} 行)，点击展开
          </span>
        </div>
      )}
    </div>
  );
}
