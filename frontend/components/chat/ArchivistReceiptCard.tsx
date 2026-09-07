'use client';

import { memo, type JSX } from 'react';
import { Search, FileText, Box, CheckCircle2, XCircle, Clock } from 'lucide-react';
import type { ArchivistReceipt, ArchivistInfo } from '../../types';

interface ArchivistReceiptCardProps {
  info: ArchivistInfo;
}

function truncate(str: string | undefined, max = 80): string {
  if (!str) return '';
  return str.length > max ? `${str.slice(0, max)}…` : str;
}

function verdictBadgeColor(verdict: string | undefined): string {
  const v = (verdict || '').toLowerCase();
  if (v.includes('pass') || v.includes('succ') || v.includes('ok')) return 'bg-emerald-100 text-emerald-700 border-emerald-200';
  if (v.includes('fail') || v.includes('error') || v.includes('bad')) return 'bg-red-100 text-red-700 border-red-200';
  if (v.includes('warn') || v.includes('partial')) return 'bg-amber-100 text-amber-700 border-amber-200';
  return 'bg-slate-100 text-slate-600 border-slate-200';
}

function statusBadgeColor(status: string | undefined): string {
  const s = (status || '').toUpperCase();
  if (s.includes('SUCCEEDED') || s.includes('COMPLETED')) return 'bg-emerald-200 text-emerald-700';
  if (s.includes('FAILED') || s.includes('ERROR')) return 'bg-red-200 text-red-700';
  if (s.includes('RUNNING') || s.includes('CREATED')) return 'bg-blue-100 text-blue-700';
  return 'bg-slate-200 text-slate-600';
}

const ArchivistReceiptCard = memo(function ArchivistReceiptCard({ info }: ArchivistReceiptCardProps): JSX.Element {
  const { query, receipts, receiptsCount } = info;

  return (
    <div className="mb-3 flex justify-start">
      <div className="max-w-[90%] rounded-2xl px-4 py-3 bg-gradient-to-br from-indigo-50 to-violet-50 border border-indigo-200 shadow-sm">
        {/* Header */}
        <div className="mb-2 flex items-center gap-2">
          <div className="flex h-6 w-6 items-center justify-center rounded-full bg-indigo-500 text-white">
            <Search className="h-3.5 w-3.5" />
          </div>
          <span className="font-semibold text-indigo-700 text-sm">📚 @archivist 搜索结果</span>
          <span className="rounded bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-600">
            {receiptsCount} 条历史任务
          </span>
        </div>

        {/* Query */}
        <div className="mb-3 text-xs text-indigo-600 italic">
          查询: "{query}"
        </div>

        {receipts.length === 0 ? (
          <div className="text-sm text-warm-500 py-2 px-3 rounded-lg bg-white/60 border border-indigo-100">
            没有找到匹配的历史任务记录。Agent 会基于当前上下文回答。
          </div>
        ) : (
          <ul className="space-y-2">
            {receipts.map((receipt: ArchivistReceipt, idx: number) => (
              <li
                key={`${receipt.mission_id || idx}`}
                className="rounded-lg bg-white/70 border border-indigo-100 px-3 py-2 hover:bg-white transition-colors"
              >
                {/* Top row: mission_id + status */}
                <div className="flex items-center justify-between mb-1">
                  <code className="text-xs font-mono text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded">
                    {truncate(receipt.mission_id, 20) || 'mission-?'}
                  </code>
                  <div className="flex items-center gap-1.5">
                    {receipt.verdict && (
                      <span className={`text-xs font-medium px-1.5 py-0.5 rounded border ${verdictBadgeColor(receipt.verdict)}`}>
                        {(receipt.verdict || '').toUpperCase()}
                      </span>
                    )}
                    {receipt.status && (
                      <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${statusBadgeColor(receipt.status)}`}>
                        {truncate(receipt.status, 12)}
                      </span>
                    )}
                  </div>
                </div>

                {/* Objective */}
                {receipt.objective && (
                  <div className="text-xs text-warm-700 leading-relaxed mb-1.5">
                    {truncate(receipt.objective, 120)}
                  </div>
                )}

                {/* Evidence summary */}
                {receipt.evidence && receipt.evidence.length > 0 && (
                  <div className="mb-1.5">
                    <div className="flex items-center gap-1 text-xs text-warm-500 mb-0.5">
                      <FileText className="h-3 w-3" />
                      <span>证据 ({receipt.evidence.length})</span>
                    </div>
                    <ul className="space-y-0.5">
                      {receipt.evidence.slice(0, 3).map((e, ei) => (
                        <li key={ei} className="text-xs text-warm-600 flex items-start gap-1">
                          {e.verdict?.toLowerCase().includes('pass')
                            ? <CheckCircle2 className="h-3 w-3 shrink-0 text-emerald-500 mt-0.5" />
                            : e.verdict?.toLowerCase().includes('fail')
                              ? <XCircle className="h-3 w-3 shrink-0 text-red-500 mt-0.5" />
                              : <Clock className="h-3 w-3 shrink-0 text-warm-400 mt-0.5" />
                          }
                          <span>{truncate(e.summary, 80)}</span>
                        </li>
                      ))}
                      {receipt.evidence.length > 3 && (
                        <li className="text-xs text-warm-400">+{receipt.evidence.length - 3} 条更多证据</li>
                      )}
                    </ul>
                  </div>
                )}

                {/* Artifacts summary */}
                {receipt.artifacts && receipt.artifacts.length > 0 && (
                  <div>
                    <div className="flex items-center gap-1 text-xs text-warm-500 mb-0.5">
                      <Box className="h-3 w-3" />
                      <span>产出物 ({receipt.artifacts.length})</span>
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {receipt.artifacts.slice(0, 4).map((a, ai) => (
                        <span
                          key={ai}
                          className="text-xs px-1.5 py-0.5 rounded bg-violet-50 text-violet-600 border border-violet-100"
                          title={a.content_address || a.title || ''}
                        >
                          {truncate(a.title || a.content_address, 24)}
                        </span>
                      ))}
                      {receipt.artifacts.length > 4 && (
                        <span className="text-xs text-warm-400">+{receipt.artifacts.length - 4}</span>
                      )}
                    </div>
                  </div>
                )}

                {/* Timestamp */}
                {receipt.updated_at && (
                  <div className="mt-1 text-[10px] text-warm-400">
                    {new Date(receipt.updated_at).toLocaleString()}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
});

export default ArchivistReceiptCard;
