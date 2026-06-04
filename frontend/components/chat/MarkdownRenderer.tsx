import { useState, type JSX, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import InteractiveCodeBlock from './InteractiveCodeBlock';
import HTMLPreviewBlock from './HTMLPreviewBlock';
import MermaidChart from './MermaidChart';
import DataTable, { type DataTableColumn } from './DataTable';
import type { FileReference } from '../../types';

/** 被引用的行高亮颜色（在 markdown 视图里） */
const REFERENCED_HIGHLIGHT = 'bg-amber-50 ring-1 ring-amber-300/60 rounded-md -mx-2 px-2';

/**
 * 计算一个 markdown block 是否落在任意 [lineStart, lineEnd] 引用区间内
 */
function findEnclosingReference(
  refs: FileReference[] | undefined,
  startLine: number,
  endLine: number,
): FileReference | undefined {
  if (!refs) return undefined;
  return refs.find((r) => {
    if (!r.lineStart) return false;
    const s = r.lineStart;
    const e = r.lineEnd ?? r.lineStart;
    // 有重叠即算命中
    return s <= endLine && e >= startLine;
  });
}

export interface MarkdownRendererProps {
  /** Markdown 原始文本 */
  content: string;
  /** 代码运行回调，传入代码和语言；不传则所有代码块隐藏运行按钮 */
  onRunCode?: (code: string, language: string) => void;
  /** 正在运行的代码片段标识，用于显示 loading 状态 */
  runningCodeKey?: string | null;
  /** 额外的容器 className */
  className?: string;
  /**
   * 当前 Markdown 文档对应的所有引用（来自 ChatInput 的 fileReferences）。
   * 命中的 block 会加高亮回显，并且会给行号节点写入 data-reference-id。
   */
  references?: FileReference[];
}

/**
 * 从 React 子节点中提取纯文本
 */
function extractText(node: ReactNode): string {
  if (node === null || node === undefined) return '';
  if (typeof node === 'string') return node;
  if (typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(extractText).join('');
  if (typeof node === 'object' && 'props' in node) {
    return extractText((node as { props: { children?: ReactNode } }).props.children);
  }
  return '';
}

/**
 * 从 th 子节点中提取表头文本数组
 */
function extractHeaders(children: ReactNode): string[] {
  const arr = Array.isArray(children) ? children : [children];
  return arr.map(extractText).map((s) => s.trim());
}

/**
 * 从 tr 子节点中提取单元格数据
 */
function extractCells(rowChildren: ReactNode): string[] {
  const arr = Array.isArray(rowChildren) ? rowChildren : [rowChildren];
  return arr
    .filter((c): c is { props: { children?: ReactNode } } =>
      typeof c === 'object' && c !== null && 'props' in c
    )
    .map((c) => extractText(c.props.children).trim());
}

/**
 * 表格上下文 - 在 thead/tbody 之间共享解析结果
 */
interface TableContext {
  columns: DataTableColumn[];
  rows: Record<string, unknown>[];
  hasParsed: boolean;
}

/**
 * MarkdownRenderer - 基于 react-markdown 的自定义 Markdown 渲染器
 *
 * 功能特性：
 * - 代码块：InteractiveCodeBlock（含语言标签、复制、运行、折叠）
 * - Mermaid：MermaidChart（图表渲染、复制、下载）
 * - 表格：DataTable（识别 Markdown 表格并提供复制/下载/新窗口打开）
 * - 标题/段落/列表/引用/链接/代码高亮等
 */
export default function MarkdownRenderer({
  content,
  onRunCode,
  runningCodeKey,
  className = '',
  references,
}: MarkdownRendererProps): JSX.Element {
  /** 代码片段计数器 */
  let codeIndex = 0;
  const nextCodeIndex = () => {
    codeIndex += 1;
    return codeIndex;
  };

  const runKeyFor = (language: string, idx: number) => `${language}-${idx}`;

  /**
   * react-markdown 给每个 component 注入的 node.position 包含起止行号（1-based）。
   * 返回要 spread 到 block 元素上的 props：行号 + 可选 id + 命中引用时的高亮 class。
   */
  const blockProps = (node: any, extraClass = ''): {
    'data-line-number'?: number;
    'data-line-end'?: number;
    'data-reference-id'?: string;
    id?: string;
    className?: string;
  } => {
    const startLine: number | undefined = node?.position?.start?.line;
    const endLine: number | undefined = node?.position?.end?.line;
    if (!startLine) return {};
    const ref = findEnclosingReference(references, startLine, endLine ?? startLine);
    const highlight = ref ? REFERENCED_HIGHLIGHT : '';
    return {
      'data-line-number': startLine,
      'data-line-end': endLine ?? startLine,
      'data-reference-id': ref?.id,
      id: ref ? `md-ref-${ref.id}` : undefined,
      className: extraClass ? `${extraClass} ${highlight}`.trim() : highlight || undefined,
    };
  };

  return (
    <div className={`markdown-renderer ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          /**
           * 代码节点
           */
          code({ className, children, ...rest }: any) {
            const match = /language-(\w+)/.exec(className || '');
            const codeString = String(children).replace(/\n$/, '');

            if (match) {
              const language = match[1];

              // Mermaid 图表
              if (language === 'mermaid') {
                return <MermaidChart code={codeString} chartId={`mermaid-${codeIndex}`} />;
              }

              const idx = nextCodeIndex();

              // HTML 代码块 → 双区块展示（代码 + 预览）
              if (language === 'html' || language === 'htm') {
                return (
                  <HTMLPreviewBlock
                    code={codeString}
                    index={idx}
                    defaultTab="preview"
                  />
                );
              }

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

          /**
           * 表格 - 整个 table 节点整体渲染为 DataTable
           * react-markdown 会将 Markdown 表格解析为 table 节点，
           * 其 children 是 [thead, tbody] 结构
           */
          table({ children }: any) {
            const arr = Array.isArray(children) ? children : [children];
            let columns: DataTableColumn[] = [];
            const rows: Record<string, unknown>[] = [];

            for (const child of arr) {
              if (!child || typeof child !== 'object' || !('props' in child)) continue;
              const childProps = (child as { props: { children?: ReactNode } }).props;
              const childChildren = childProps.children;

              // thead - 提取列定义
              if (child.type === 'thead' || (child as any).type?.name === 'thead') {
                const headerRows = Array.isArray(childChildren) ? childChildren : [childChildren];
                for (const headerRow of headerRows) {
                  if (!headerRow || typeof headerRow !== 'object' || !('props' in headerRow)) continue;
                  const headerCells = (headerRow as { props: { children?: ReactNode } }).props.children;
                  const headers = extractHeaders(headerCells);
                  columns = headers.map((h, i) => ({
                    key: `col_${i}`,
                    title: h || `列 ${i + 1}`,
                  }));
                  break;
                }
              }

              // tbody - 提取数据行
              if (child.type === 'tbody' || (child as any).type?.name === 'tbody') {
                const bodyRows = Array.isArray(childChildren) ? childChildren : [childChildren];
                for (const bodyRow of bodyRows) {
                  if (!bodyRow || typeof bodyRow !== 'object' || !('props' in bodyRow)) continue;
                  const bodyRowChildren = (bodyRow as { props: { children?: ReactNode } }).props.children;
                  const cells = extractCells(bodyRowChildren);
                  if (cells.length === 0) continue;
                  const row: Record<string, unknown> = {};
                  columns.forEach((col, i) => {
                    row[col.key] = cells[i] ?? '';
                  });
                  rows.push(row);
                }
              }
            }

            if (columns.length === 0) {
              return (
                <div className="my-4 rounded-2xl border border-warm-150 bg-white p-4 text-warm-500">
                  表格数据解析失败
                </div>
              );
            }

            return (
              <DataTable
                title="表格"
                data={rows}
                columns={columns}
                tableName={`table-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`}
              />
            );
          },

          /** 段落 */
          p({ children, node }: any) {
            const bp = blockProps(node, 'mb-3 leading-7 text-warm-700 last:mb-0');
            return <p {...bp}>{children}</p>;
          },

          /** 标题 */
          h1({ children, node }: any) {
            const bp = blockProps(node, 'mb-4 mt-6 text-2xl font-semibold text-warm-900 first:mt-0');
            return <h1 {...bp}>{children}</h1>;
          },
          h2({ children, node }: any) {
            const bp = blockProps(node, 'mb-3 mt-5 text-xl font-semibold text-warm-900 first:mt-0');
            return <h2 {...bp}>{children}</h2>;
          },
          h3({ children, node }: any) {
            const bp = blockProps(node, 'mb-2 mt-4 text-lg font-semibold text-warm-800 first:mt-0');
            return <h3 {...bp}>{children}</h3>;
          },

          /** 无序列表 */
          ul({ children, node }: any) {
            const bp = blockProps(node, 'mb-3 list-disc space-y-1 pl-6 text-warm-700');
            return <ul {...bp}>{children}</ul>;
          },

          /** 有序列表 */
          ol({ children, node }: any) {
            const bp = blockProps(node, 'mb-3 list-decimal space-y-1 pl-6 text-warm-700');
            return <ol {...bp}>{children}</ol>;
          },

          /** 列表项 */
          li({ children, node }: any) {
            const bp = blockProps(node, 'leading-7');
            return <li {...bp}>{children}</li>;
          },

          /** 引用块 */
          blockquote({ children, node }: any) {
            const bp = blockProps(node, 'mb-3 border-l-4 border-primary-300 bg-primary-50/50 py-2 pl-4 italic text-warm-600');
            return <blockquote {...bp}>{children}</blockquote>;
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
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
