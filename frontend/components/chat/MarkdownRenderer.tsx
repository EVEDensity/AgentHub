import { type JSX } from 'react';
import ReactMarkdown from 'react-markdown';
import InteractiveCodeBlock from './InteractiveCodeBlock';

export interface MarkdownRendererProps {
  /** Markdown 原始文本 */
  content: string;
  /** 代码运行回调，传入代码和语言；不传则所有代码块隐藏运行按钮 */
  onRunCode?: (code: string, language: string) => void;
  /** 正在运行的代码片段标识，用于显示 loading 状态 */
  runningCodeKey?: string | null;
  /** 额外的容器 className */
  className?: string;
}

/**
 * MarkdownRenderer - 基于 react-markdown 的自定义 Markdown 渲染器
 *
 * 替换默认的 code 节点为 InteractiveCodeBlock，提供：
 * - 语言标签 + 下拉切换 / 复制 / 运行 / 折叠交互按钮
 * - Prism 语法高亮（oneDark 主题）
 *
 * 同时自定义段落、标题、列表、引用、链接、表格等节点样式，
 * 匹配 AgentHub warm 色调设计系统。
 */
export default function MarkdownRenderer({
  content,
  onRunCode,
  runningCodeKey,
  className = '',
}: MarkdownRendererProps): JSX.Element {
  /** 代码片段计数器，每次渲染从 1 开始 */
  let codeIndex = 0;
  const nextCodeIndex = () => {
    codeIndex += 1;
    return codeIndex;
  };

  const runKeyFor = (language: string, idx: number) => `${language}-${idx}`;

  return (
    <div className={`markdown-renderer ${className}`}>
      <ReactMarkdown
        components={{
          /**
           * 代码节点：
           * - className 含 "language-xxx" → 围栏代码块 → InteractiveCodeBlock
           * - 否则 → 内联代码 → 原生 <code> 样式
           */
          code({ className, children, ...rest }: any) {
            const match = /language-(\w+)/.exec(className || '');

            // 围栏代码块
            if (match) {
              const language = match[1];
              const codeString = String(children).replace(/\n$/, '');
              const idx = nextCodeIndex();
              const runKey = runKeyFor(language, idx);

              return (
                <InteractiveCodeBlock
                  code={codeString}
                  language={language}
                  index={idx}
                  onRun={onRunCode ? (c, l) => onRunCode(c, l) : undefined}
                  isRunning={runningCodeKey === runKey}
                />
              );
            }

            // 内联代码
            return (
              <code
                className="rounded bg-amber-50 px-1.5 py-0.5 text-[13px] font-medium text-amber-700"
                {...rest}
              >
                {children}
              </code>
            );
          },

          /** 段落 */
          p({ children }: any) {
            return <p className="mb-3 leading-7 text-warm-700 last:mb-0">{children}</p>;
          },

          /** 标题 */
          h1({ children }: any) {
            return <h1 className="mb-4 mt-6 text-2xl font-semibold text-warm-900 first:mt-0">{children}</h1>;
          },
          h2({ children }: any) {
            return <h2 className="mb-3 mt-5 text-xl font-semibold text-warm-900 first:mt-0">{children}</h2>;
          },
          h3({ children }: any) {
            return <h3 className="mb-2 mt-4 text-lg font-semibold text-warm-800 first:mt-0">{children}</h3>;
          },

          /** 无序列表 */
          ul({ children }: any) {
            return <ul className="mb-3 list-disc space-y-1 pl-6 text-warm-700">{children}</ul>;
          },

          /** 有序列表 */
          ol({ children }: any) {
            return <ol className="mb-3 list-decimal space-y-1 pl-6 text-warm-700">{children}</ol>;
          },

          /** 列表项 */
          li({ children }: any) {
            return <li className="leading-7">{children}</li>;
          },

          /** 引用块 */
          blockquote({ children }: any) {
            return (
              <blockquote className="mb-3 border-l-4 border-primary-300 bg-primary-50/50 py-2 pl-4 italic text-warm-600">
                {children}
              </blockquote>
            );
          },

          /** 链接 */
          a({ href, children, ...rest }: any) {
            return (
              <a
                className="text-primary-500 underline decoration-primary-300 underline-offset-2 transition hover:text-primary-600"
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                {...rest}
              >
                {children}
              </a>
            );
          },

          /** 水平分割线 */
          hr() {
            return <hr className="my-6 border-t border-warm-150" />;
          },

          /** 强调/加粗 */
          strong({ children }: any) {
            return <strong className="font-semibold text-warm-900">{children}</strong>;
          },

          /** 表格 */
          table({ children }: any) {
            return (
              <div className="mb-4 overflow-auto rounded-lg border border-warm-150">
                <table className="w-full text-sm">{children}</table>
              </div>
            );
          },
          thead({ children }: any) {
            return <thead className="bg-warm-50 text-left">{children}</thead>;
          },
          th({ children }: any) {
            return <th className="px-4 py-2 font-semibold text-warm-700">{children}</th>;
          },
          td({ children }: any) {
            return <td className="border-t border-warm-100 px-4 py-2 text-warm-600">{children}</td>;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
