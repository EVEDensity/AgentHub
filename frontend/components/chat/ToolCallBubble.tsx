import { memo, useState } from 'react';
import type { ToolCallItem, ToolResultItem } from '../../types';

interface ToolCallBubbleProps {
  calls?: ToolCallItem[];
  results?: ToolResultItem[];
  isStreaming?: boolean;
  /** 重试失败的工具调用（携带工具名和参数） */
  onRetryTool?: (toolName: string, args: Record<string, unknown>) => void;
}

const CATEGORY_ICONS: Record<string, string> = {
  search: '[search]',
  file: '[file]',
  code: '[code]',
  memory: '[brain]',
  system: '[gear]',
  integration: '[plug]',
};

function getIcon(toolName: string): string {
  if (toolName.includes('search')) return '[search]';
  if (toolName.includes('file_read')) return '[doc]';
  if (toolName.includes('file_write')) return '[edit]';
  if (toolName.includes('code')) return '[code]';
  if (toolName.includes('memory')) return '[brain]';
  return '[tool]';
}

const ToolCallBubble = memo(function ToolCallBubble({
  calls,
  results,
  isStreaming,
  onRetryTool,
}: ToolCallBubbleProps) {
  const [expandedArg, setExpandedArg] = useState<string | null>(null);
  const [expandedResult, setExpandedResult] = useState<string | null>(null);

  const allItems: Array<{
    key: string;
    name: string;
    status: 'queued' | 'executing' | 'calling' | 'success' | 'error';
    arguments?: Record<string, unknown>;
    result?: unknown;
    error?: string;
    progress?: {
      progressType?: string;
      message?: string;
      percentage?: number;
    };
  }> = [];

  if (calls) {
    for (const c of calls) {
      const key = `${c.name}-${JSON.stringify(c.arguments)}`;
      const matching = results?.find(
        (r) => r.tool_name === c.name
      );
      allItems.push({
        key,
        name: c.name,
        status: matching ? (matching.success ? 'success' : 'error') : (c.status as any),
        arguments: c.arguments,
        result: matching?.result,
        error: matching?.error,
        progress: (c as any).progress,
      });
    }
  }

  if (results && !calls) {
    for (const r of results) {
      allItems.push({
        key: `${r.tool_name}-result`,
        name: r.tool_name,
        status: r.success ? 'success' : 'error',
        result: r.result,
        error: r.error,
      });
    }
  }

  if (allItems.length === 0) return null;

  return (
    <div className="mb-3 flex flex-col gap-2">
      {allItems.map((item) => {
        const isExpandedArg = expandedArg === item.key;
        const isExpandedResult = expandedResult === item.key;

        return (
          <div
            key={item.key}
            className={`rounded-xl border px-4 py-3 text-sm transition ${
              item.status === 'queued'
                ? 'border-gray-200 bg-gray-50/60'
                : item.status === 'executing' || item.status === 'calling'
                ? 'border-blue-200 bg-blue-50/60'
                : item.status === 'error'
                ? 'border-red-200 bg-red-50/60'
                : 'border-green-200 bg-green-50/60'
            }`}
          >
            {/* Header */}
            <div className="flex items-center gap-2.5">
              <span className="text-base">{getIcon(item.name)}</span>
              <span className="font-medium text-warm-800">{item.name}</span>
              {item.status === 'queued' ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
                  <span className="inline-block h-2 w-2 rounded-full bg-gray-400" />
                  等待中
                </span>
              ) : item.status === 'executing' ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-700">
                  <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-blue-500" />
                  执行中...
                </span>
              ) : item.status === 'calling' && !isStreaming ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-700">
                  <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-blue-500" />
                  调用中...
                </span>
              ) : item.status === 'error' ? (
                <span className="flex items-center gap-1.5">
                  <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs text-red-700">
                    失败
                  </span>
                  {/* ★ 方案4: 重试按钮 */}
                  {onRetryTool && item.arguments && (
                    <button
                      className="inline-flex items-center gap-0.5 rounded-full bg-red-50 border border-red-200 px-1.5 py-0.5 text-[10px] text-red-600 hover:bg-red-100 transition-colors"
                      onClick={() => onRetryTool(item.name, item.arguments!)}
                      title={`重试 ${item.name}`}
                    >
                      <svg className="h-2.5 w-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="1 4 1 10 7 10" />
                        <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
                      </svg>
                      重试
                    </button>
                  )}
                </span>
              ) : (
                <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-700">
                  完成
                </span>
              )}
              {item.arguments && (
                <button
                  className="ml-auto text-xs text-warm-400 hover:text-warm-600 transition"
                  onClick={() =>
                    setExpandedArg(isExpandedArg ? null : item.key)
                  }
                >
                  {isExpandedArg ? '收起参数 [up]' : '查看参数 [down]'}
                </button>
              )}
              {(item.result !== undefined || item.error) && (
                <button
                  className="text-xs text-warm-400 hover:text-warm-600 transition"
                  onClick={() =>
                    setExpandedResult(isExpandedResult ? null : item.key)
                  }
                >
                  {isExpandedResult ? '收起结果 [up]' : '查看结果 [down]'}
                </button>
              )}
            </div>

            {/* Arguments (expandable) */}
            {isExpandedArg && item.arguments && (
              <div className="mt-2 rounded-lg bg-white/70 px-3 py-2 font-mono text-xs text-warm-600 max-h-32 overflow-y-auto">
                <pre className="whitespace-pre-wrap break-all">
                  {JSON.stringify(item.arguments, null, 2)}
                </pre>
              </div>
            )}

            {/* Result (expandable) */}
            {isExpandedResult && (item.result !== undefined || item.error) && (
              <div className="mt-2 rounded-lg bg-white/70 px-3 py-2 text-xs text-warm-700 max-h-48 overflow-y-auto">
                {item.error ? (
                  <span className="text-red-600">错误: {item.error}</span>
                ) : typeof item.result === 'string' ? (
                  <pre className="whitespace-pre-wrap break-all font-mono">{item.result}</pre>
                ) : (
                  <pre className="whitespace-pre-wrap break-all font-mono">
                    {JSON.stringify(item.result, null, 2)}
                  </pre>
                )}
              </div>
            )}

            {/* Progress bar for executing tools with percentage */}
            {item.status === 'executing' && item.progress?.percentage !== undefined && (
              <div className="mt-2">
                <div className="flex items-center justify-between text-xs text-warm-500 mb-1">
                  <span>{item.progress?.message || '执行中...'}</span>
                  <span>{Math.round((item.progress?.percentage || 0) * 100)}%</span>
                </div>
                <div className="h-1.5 w-full rounded-full bg-blue-100 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-blue-500 transition-all duration-300"
                    style={{ width: `${(item.progress?.percentage || 0) * 100}%` }}
                  />
                </div>
              </div>
            )}

            {/* Auto-expand on calling/executing (show a brief hint) */}
            {(item.status === 'executing' || item.status === 'calling') && item.arguments && !isExpandedArg && (
              <div className="mt-1.5 text-xs text-warm-400 truncate">
                {Object.entries(item.arguments)
                  .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
                  .join(', ')
                  .slice(0, 80)}
              </div>
            )}

            {/* Queued hint */}
            {item.status === 'queued' && item.arguments && !isExpandedArg && (
              <div className="mt-1.5 text-xs text-gray-400 truncate">
                {Object.entries(item.arguments)
                  .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
                  .join(', ')
                  .slice(0, 80)}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
});

export default ToolCallBubble;
