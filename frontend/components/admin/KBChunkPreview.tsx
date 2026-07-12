'use client';

import { type JSX } from 'react';
import type { ChunkDetail } from '../../stores/knowledgeStore';

interface Props {
  chunks: ChunkDetail[];
}

export function KBChunkPreview({ chunks }: Props): JSX.Element {
  if (chunks.length === 0) {
    return (
      <div className="text-center py-8 text-xs text-warm-400">
        暂无分块数据
      </div>
    );
  }

  const total = chunks[0]?.total || chunks.length;

  return (
    <div className="space-y-2">
      <div className="text-xs text-warm-400 mb-2">
        共 {chunks.length} / {total} 个分块
      </div>
      {chunks.map((chunk, i) => (
        <div key={chunk.id} className="border border-warm-100 rounded-lg overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2 bg-warm-50/50 border-b border-warm-100">
            <span className="text-[10px] font-medium text-warm-500">分块 #{i + 1}</span>
            {chunk.total > 0 && (
              <span className="text-[10px] text-warm-400">
                / {chunk.total}
              </span>
            )}
            <div className="flex-1" />
            <span className="text-[10px] text-warm-300">
              {chunk.content.length} 字符
            </span>
            <span className="text-[10px] text-warm-300">
              [{chunk.start_offset}:{chunk.end_offset}]
            </span>
          </div>
          <div className="px-3 py-2">
            <div className="text-xs text-warm-600 leading-relaxed whitespace-pre-wrap max-h-48 overflow-y-auto">
              {chunk.content}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
