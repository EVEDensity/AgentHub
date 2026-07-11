'use client';

import { useEffect, useState, type JSX } from 'react';
import { useKnowledgeStore } from '../../stores/knowledgeStore';
import { KBDocumentUploader } from './KBDocumentUploader';
import { KBRetrievalTester } from './KBRetrievalTester';
import { KBChunkPreview } from './KBChunkPreview';
import { KBCollectionManager } from './KBCollectionManager';

interface Props {
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
}

export default function KnowledgeBaseModule({ authHeaders: _authHeaders, setNotice: _setNotice }: Props): JSX.Element {
  const collections = useKnowledgeStore((s) => s.collections);
  const documents = useKnowledgeStore((s) => s.documents);
  const activeDocumentId = useKnowledgeStore((s) => s.activeDocumentId);
  const activeDocumentChunks = useKnowledgeStore((s) => s.activeDocumentChunks);
  const collectionsLoading = useKnowledgeStore((s) => s.collectionsLoading);
  const documentsLoading = useKnowledgeStore((s) => s.documentsLoading);
  const chunksLoading = useKnowledgeStore((s) => s.chunksLoading);
  const uploadStatus = useKnowledgeStore((s) => s.uploadStatus);

  const loadCollections = useKnowledgeStore((s) => s.loadCollections);
  const loadDocuments = useKnowledgeStore((s) => s.loadDocuments);
  const loadChunks = useKnowledgeStore((s) => s.loadChunks);
  const deleteDocument = useKnowledgeStore((s) => s.deleteDocument);
  const setActiveDocumentId = useKnowledgeStore((s) => s.setActiveDocumentId);

  const [activeCollection, setActiveCollection] = useState<string>('docs');
  const [subTab, setSubTab] = useState<'detail' | 'retrieval' | 'manage'>('detail');
  const [showUpload, setShowUpload] = useState(false);

  useEffect(() => {
    void loadCollections();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (activeCollection) {
      void loadDocuments(activeCollection);
    }
  }, [activeCollection]); // eslint-disable-line react-hooks/exhaustive-deps

  const activeDoc = documents.find((d) => d.source_id === activeDocumentId);

  return (
    <div className="flex flex-col h-full gap-3">
      {/* Stats bar */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <select
            value={activeCollection}
            onChange={(e) => { setActiveCollection(e.target.value); setActiveDocumentId(null); }}
            className="input-field text-sm py-1.5 w-40"
          >
            {(collections.length > 0 ? collections : [{ name: 'docs', points_count: 0 }, { name: 'code', points_count: 0 }, { name: 'memory', points_count: 0 }, { name: 'artifacts', points_count: 0 }]).map((c) => (
              <option key={c.name} value={c.name}>{c.name} ({c.points_count})</option>
            ))}
          </select>
          <span className="text-xs text-warm-400">
            {documents.length} 个文档 · {documents.reduce((sum, d) => sum + d.chunk_count, 0)} 个分块
          </span>
        </div>
        <div className="flex-1" />
        <div className="flex items-center gap-2">
          {/* Sub-tabs */}
          <button
            className={`btn-ghost text-xs ${subTab === 'detail' ? 'bg-primary-50 text-primary-600' : ''}`}
            onClick={() => setSubTab('detail')}
          >
            详情
          </button>
          <button
            className={`btn-ghost text-xs ${subTab === 'retrieval' ? 'bg-primary-50 text-primary-600' : ''}`}
            onClick={() => setSubTab('retrieval')}
          >
            检索测试
          </button>
          <button
            className={`btn-ghost text-xs ${subTab === 'manage' ? 'bg-primary-50 text-primary-600' : ''}`}
            onClick={() => setSubTab('manage')}
          >
            集合管理
          </button>
          <div className="w-px h-5 bg-warm-200" />
          <button className="btn-primary text-xs" onClick={() => setShowUpload(true)}>
            <span className="material-symbols-outlined text-[14px]">upload</span>
            上传文档
          </button>
        </div>
      </div>

      {/* Upload modal */}
      {showUpload && (
        <KBDocumentUploader
          collection={activeCollection}
          onClose={() => setShowUpload(false)}
          onSuccess={() => { setShowUpload(false); void loadDocuments(activeCollection); void loadCollections(); }}
        />
      )}

      {/* Body */}
      {subTab === 'manage' ? (
        <KBCollectionManager />
      ) : (
        <div className="grid grid-cols-[320px_1fr] gap-4 flex-1 min-h-0">
          {/* Left: Document list */}
          <div className="card overflow-y-auto">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-warm-700">文档列表</h3>
              {documentsLoading && (
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
              )}
            </div>
            {documents.length === 0 && !documentsLoading ? (
              <div className="text-center py-8 text-xs text-warm-400">
                <span className="material-symbols-outlined text-2xl mb-2 block">description</span>
                暂无文档<br />
                <button className="text-primary-500 hover:underline mt-2" onClick={() => setShowUpload(true)}>
                  上传第一篇文档
                </button>
              </div>
            ) : (
              <div className="space-y-1">
                {documents.map((doc) => (
                  <div
                    key={doc.source_id}
                    className={`flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ${
                      activeDocumentId === doc.source_id
                        ? 'bg-primary-50 border border-primary-200'
                        : 'hover:bg-warm-50 border border-transparent'
                    }`}
                    onClick={() => { setActiveDocumentId(doc.source_id); void loadChunks(activeCollection, doc.source_id); setSubTab('detail'); }}
                  >
                    <span className="material-symbols-outlined text-[16px] text-warm-400 shrink-0">
                      {doc.file_type === 'application/pdf' ? 'picture_as_pdf' :
                       doc.file_type?.includes('word') ? 'article' :
                       doc.file_type?.includes('presentation') ? 'slideshow' : 'description'}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="text-xs font-medium text-warm-700 truncate">{doc.source_id}</div>
                      <div className="text-[10px] text-warm-400">
                        {doc.chunk_count} 分块{doc.file_type ? ` · ${doc.file_type}` : ''}
                      </div>
                    </div>
                    <button
                      className="shrink-0 text-warm-300 hover:text-danger-500"
                      onClick={(e) => { e.stopPropagation(); void deleteDocument(activeCollection, doc.source_id); }}
                      title="删除文档"
                    >
                      <span className="material-symbols-outlined text-[14px]">delete</span>
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Right: Detail / Retrieval */}
          <div className="card overflow-y-auto min-h-0">
            {subTab === 'retrieval' ? (
              <KBRetrievalTester collection={activeCollection} />
            ) : (
              <>
                {!activeDocumentId ? (
                  <div className="text-center py-12 text-sm text-warm-400">
                    <span className="material-symbols-outlined text-3xl mb-3 block">folder_open</span>
                    选择一个文档查看详情
                  </div>
                ) : chunksLoading ? (
                  <div className="flex justify-center py-12">
                    <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
                  </div>
                ) : (
                  <>
                    {/* Document header */}
                    <div className="flex items-center justify-between mb-4 pb-3 border-b border-warm-100">
                      <div>
                        <h3 className="text-sm font-semibold text-warm-700 truncate max-w-md">
                          {activeDoc?.source_id || activeDocumentId}
                        </h3>
                        <div className="flex items-center gap-2 mt-1 text-[10px] text-warm-400">
                          <span>{activeDoc?.chunk_count || activeDocumentChunks?.length || 0} 分块</span>
                          {activeDoc?.file_type && <span>· {activeDoc.file_type}</span>}
                          {activeDoc?.created_at && <span>· {new Date(activeDoc.created_at).toLocaleDateString()}</span>}
                        </div>
                      </div>
                      <button
                        className="btn-ghost text-xs text-danger-500"
                        onClick={() => { void deleteDocument(activeCollection, activeDocumentId); }}
                      >
                        <span className="material-symbols-outlined text-[14px]">delete</span>
                        删除
                      </button>
                    </div>

                    {/* Chunk list */}
                    <KBChunkPreview chunks={activeDocumentChunks || []} />
                  </>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
