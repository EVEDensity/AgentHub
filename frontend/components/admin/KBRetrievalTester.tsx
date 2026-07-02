'use client';

import { type JSX } from 'react';
import { useKnowledgeStore } from '../../stores/knowledgeStore';

interface Props {
  collection: string;
}

export function KBRetrievalTester({ collection: _collection }: Props): JSX.Element {
  const retrievalQuery = useKnowledgeStore((s) => s.retrievalQuery);
  const retrievalResults = useKnowledgeStore((s) => s.retrievalResults);
  const retrievalK = useKnowledgeStore((s) => s.retrievalK);
  const retrievalCollection = useKnowledgeStore((s) => s.retrievalCollection);
  const retrievalLoading = useKnowledgeStore((s) => s.retrievalLoading);

  const setRetrievalQuery = useKnowledgeStore((s) => s.setRetrievalQuery);
  const setRetrievalK = useKnowledgeStore((s) => s.setRetrievalK);
  const setRetrievalCollection = useKnowledgeStore((s) => s.setRetrievalCollection);
  const runRetrievalTest = useKnowledgeStore((s) => s.runRetrievalTest);

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-warm-700">检索测试</h3>

      {/* Input area */}
      <div className="space-y-3">
        <div>
          <label className="text-xs text-warm-500 block mb-1">查询语句</label>
          <textarea
            className="input-field w-full text-sm"
            rows={3}
            placeholder="输入检索查询，例如：数据库设计方案有哪些？"
            value={retrievalQuery}
            onChange={(e) => setRetrievalQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void runRetrievalTest(); } }}
          />
        </div>
        <div className="flex items-center gap-3">
          <div>
            <label className="text-xs text-warm-500 block mb-1">目标集合</label>
            <select
              className="input-field text-xs py-1.5"
              value={retrievalCollection}
              onChange={(e) => setRetrievalCollection(e.target.value)}
            >
              <option value="docs">docs</option>
              <option value="code">code</option>
              <option value="memory">memory</option>
              <option value="artifacts">artifacts</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-warm-500 block mb-1">Top-K</label>
            <input
              type="number"
              className="input-field text-xs w-16 py-1.5"
              min={1} max={20}
              value={retrievalK}
              onChange={(e) => setRetrievalK(Number(e.target.value))}
            />
          </div>
          <div className="flex-1" />
          <button
            className="btn-primary text-xs self-end"
            onClick={() => void runRetrievalTest()}
            disabled={!retrievalQuery.trim() || retrievalLoading}
          >
            {retrievalLoading ? (
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white inline-block" />
            ) : (
              <span className="material-symbols-outlined text-[14px]">search</span>
            )}
            检索
          </button>
        </div>
      </div>

      {/* Results */}
      {retrievalResults !== null && (
        <div className="space-y-2">
          <div className="text-xs text-warm-400">
            共 {retrievalResults.length} 个结果
          </div>
          {retrievalResults.length === 0 ? (
            <div className="text-center py-8 text-xs text-warm-400">
              未找到相关结果，尝试调整查询语句或切换集合
            </div>
          ) : (
            <div className="space-y-2">
              {retrievalResults.map((r, i) => (
                <div key={r.id} className="border border-warm-100 rounded-lg p-3 hover:border-primary-200 transition-colors">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${
                      r.score > 0.8 ? 'bg-success-50 text-success-600' :
                      r.score > 0.5 ? 'bg-warning-50 text-warning-600' :
                      'bg-warm-100 text-warm-500'
                    }`}>
                      相似度 {(r.score * 100).toFixed(0)}%
                    </span>
                    <span className="text-[10px] text-warm-400">#{i + 1}</span>
                    <span className="text-[10px] text-warm-300 truncate max-w-[200px]">
                      来源: {r.source_id} · 分块 #{r.chunk_index}
                    </span>
                  </div>
                  <div className="text-xs text-warm-600 leading-relaxed whitespace-pre-wrap line-clamp-5">
                    {r.content}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
