'use client';

import { useEffect, type JSX } from 'react';
import { useKnowledgeStore } from '../../stores/knowledgeStore';

export function KBCollectionManager(): JSX.Element {
  const collections = useKnowledgeStore((s) => s.collections);
  const collectionsLoading = useKnowledgeStore((s) => s.collectionsLoading);
  const loadCollections = useKnowledgeStore((s) => s.loadCollections);

  useEffect(() => {
    void loadCollections();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const totalPoints = collections.reduce((sum, c) => sum + c.points_count, 0);

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-warm-700">集合管理</h3>
        <div className="flex items-center gap-2">
          <button className="btn-ghost text-xs" onClick={() => void loadCollections()}>
            <span className="material-symbols-outlined text-[14px]">refresh</span>
            刷新
          </button>
        </div>
      </div>

      {collectionsLoading ? (
        <div className="flex justify-center py-12">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
        </div>
      ) : (
        <div className="space-y-3">
          {/* Summary */}
          <div className="flex items-center gap-6 text-xs text-warm-500 bg-warm-50 rounded-lg px-4 py-3">
            <div>
              <span className="font-semibold text-warm-700">{collections.length}</span> 个集合
            </div>
            <div>
              <span className="font-semibold text-warm-700">{totalPoints.toLocaleString()}</span> 个向量点
            </div>
          </div>

          {/* Collection cards */}
          <div className="grid grid-cols-2 gap-3">
            {collections.map((c) => (
              <div key={c.name} className="border border-warm-100 rounded-lg p-4 hover:border-primary-200 transition-colors">
                <div className="flex items-center gap-2 mb-2">
                  <span className="material-symbols-outlined text-[18px] text-primary-500">
                    {c.name === 'docs' ? 'description' :
                     c.name === 'code' ? 'code' :
                     c.name === 'memory' ? 'memory' :
                     'folder'}
                  </span>
                  <span className="text-sm font-medium text-warm-700">{c.name}</span>
                </div>
                <div className="flex items-center gap-4 text-xs text-warm-400">
                  <span>{c.points_count.toLocaleString()} 个向量</span>
                  <span className={`status-dot ${c.points_count > 0 ? 'status-dot-active' : 'status-dot-idle'}`}>
                    {c.points_count > 0 ? '活跃' : '空闲'}
                  </span>
                </div>
              </div>
            ))}
          </div>

          {collections.length === 0 && (
            <div className="text-center py-8 text-xs text-warm-400">
              暂无集合，上传文档后将自动创建
            </div>
          )}

          {/* Note */}
          <div className="bg-warm-50 rounded-lg px-4 py-3 text-xs text-warm-400">
            <span className="material-symbols-outlined text-[14px] align-bottom mr-1">info</span>
            集合由离线知识库服务自动管理。删除文档会移除对应向量点，但集合不会删除。
            如需重建集合（如切换 embedding 模型），集合将自动检测维度变化并重建。
          </div>
        </div>
      )}
    </div>
  );
}
