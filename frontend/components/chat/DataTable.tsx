import { useState, type JSX, type ReactNode } from 'react';

export interface DataTableColumn<T = Record<string, unknown>> {
  key: string;
  title: string;
  align?: 'left' | 'center' | 'right';
  width?: string;
  render?: (value: unknown, row: T, index: number) => ReactNode;
}

export interface DataTableProps<T = Record<string, unknown>> {
  /** 表格标题 */
  title?: string;
  /** 表格数据 */
  data: T[];
  /** 列定义 */
  columns: DataTableColumn<T>[];
  /** 唯一的表名（用于下载文件命名） */
  tableName?: string;
  /** 额外的容器 className */
  className?: string;
}

/**
 * DataTable - 可复制和下载的数据表格组件
 *
 * 功能：
 * - 表格数据展示
 * - 复制为 CSV/TSV
 * - 下载 CSV 文件
 * - 新窗口打开（适合大表格）
 */
export default function DataTable<T = Record<string, unknown>>({
  title = '表格',
  data,
  columns,
  tableName = 'table',
  className = '',
}: DataTableProps<T>): JSX.Element {
  const [copySuccess, setCopySuccess] = useState(false);
  const [downloadSuccess, setDownloadSuccess] = useState(false);

  /** 转义 CSV 字段 */
  function escapeCsv(value: unknown): string {
    if (value === null || value === undefined) return '';
    const str = String(value);
    if (str.includes(',') || str.includes('"') || str.includes('\n')) {
      return `"${str.replace(/"/g, '""')}"`;
    }
    return str;
  }

  /** 复制为 TSV 格式（适合粘贴到 Excel/表格软件） */
  async function handleCopy(): Promise<void> {
    try {
      const header = columns.map((c) => c.title).join('\t');
      const rows = data.map((row) =>
        columns.map((c) => {
          const value = (row as Record<string, unknown>)[c.key];
          return value === null || value === undefined ? '' : String(value);
        }).join('\t')
      );
      const content = [header, ...rows].join('\n');
      await navigator.clipboard.writeText(content);
      setCopySuccess(true);
      setTimeout(() => setCopySuccess(false), 2000);
    } catch (err) {
      console.error('复制失败:', err);
    }
  }

  /** 下载 CSV */
  function handleDownload(): void {
    const header = columns.map((c) => escapeCsv(c.title)).join(',');
    const rows = data.map((row) =>
      columns.map((c) => {
        const value = (row as Record<string, unknown>)[c.key];
        return escapeCsv(value);
      }).join(',')
    );
    // 添加 UTF-8 BOM 以便 Excel 正确显示中文
    const bom = '\uFEFF';
    const content = bom + [header, ...rows].join('\n');
    const blob = new Blob([content], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${tableName}-${Date.now()}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    setDownloadSuccess(true);
    setTimeout(() => setDownloadSuccess(false), 2000);
  }

  /** 新窗口打开 */
  function handleOpenNewTab(): void {
    const header = columns.map((c) => c.title);
    const rowsHtml = data.map((row) =>
      '<tr>' + columns.map((c) => {
        const value = (row as Record<string, unknown>)[c.key];
        return `<td>${value === null || value === undefined ? '' : String(value)}</td>`;
      }).join('') + '</tr>'
    ).join('');

    const html = `
      <!DOCTYPE html>
      <html>
      <head>
        <meta charset="UTF-8">
        <title>${title}</title>
        <style>
          body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; padding: 20px; }
          h1 { color: #1f2937; }
          table { border-collapse: collapse; width: 100%; margin-top: 16px; }
          th, td { border: 1px solid #e5e7eb; padding: 8px 12px; text-align: left; }
          th { background-color: #f9fafb; font-weight: 600; }
        </style>
      </head>
      <body>
        <h1>${title}</h1>
        <table>
          <thead><tr>${header.map((h) => `<th>${h}</th>`).join('')}</tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </body>
      </html>
    `;
    const newWindow = window.open('', '_blank');
    if (newWindow) {
      newWindow.document.write(html);
      newWindow.document.close();
    }
  }

  return (
    <div className={`my-4 rounded-2xl border border-warm-150 bg-white ${className}`}>
      {/* 工具栏 - 完全参照图二样式 */}
      <div className="flex items-center justify-between px-4 py-2.5">
        <div className="flex items-center gap-2">
          <span className="text-sm text-warm-700">{title}</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => void handleCopy()}
            className="rounded p-1.5 text-warm-500 transition-colors hover:bg-warm-50 hover:text-warm-700"
            title="复制"
          >
            {copySuccess ? (
              <svg className="h-4 w-4 text-green-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            ) : (
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
              </svg>
            )}
          </button>
          <button
            type="button"
            onClick={handleDownload}
            className="rounded p-1.5 text-warm-500 transition-colors hover:bg-warm-50 hover:text-warm-700"
            title="下载"
          >
            {downloadSuccess ? (
              <svg className="h-4 w-4 text-green-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            ) : (
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
            )}
          </button>
          <button
            type="button"
            onClick={handleOpenNewTab}
            className="rounded p-1.5 text-warm-500 transition-colors hover:bg-warm-50 hover:text-warm-700"
            title="新窗口打开"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <polyline points="15 3 21 3 21 9" />
              <line x1="10" y1="14" x2="21" y2="3" />
            </svg>
          </button>
        </div>
      </div>

      {/* 表格内容 - 参照图二样式 */}
      <div className="overflow-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              {columns.map((col) => (
                <th
                  key={col.key}
                  className="border-b border-warm-200 bg-white px-6 py-3 text-left text-sm font-semibold text-warm-700"
                  style={{
                    textAlign: col.align || 'left',
                    width: col.width,
                  }}
                >
                  {col.title}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length}
                  className="px-6 py-8 text-center text-sm text-warm-400"
                >
                  暂无数据
                </td>
              </tr>
            ) : (
              data.map((row, rowIdx) => (
                <tr
                  key={rowIdx}
                  className="transition-colors hover:bg-warm-50/30"
                >
                  {columns.map((col) => {
                    const value = (row as Record<string, unknown>)[col.key];
                    return (
                      <td
                        key={col.key}
                        className="border-b border-warm-100 px-6 py-3 text-sm text-warm-700"
                        style={{ textAlign: col.align || 'left' }}
                      >
                        {col.render ? col.render(value, row, rowIdx) : (value === null || value === undefined ? '' : String(value))}
                      </td>
                    );
                  })}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
