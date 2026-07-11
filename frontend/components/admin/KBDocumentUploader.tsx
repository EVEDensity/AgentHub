'use client';

import { useState, useRef, useCallback, type DragEvent, type JSX } from 'react';
import { useKnowledgeStore } from '../../stores/knowledgeStore';

interface Props {
  collection: string;
  onClose: () => void;
  onSuccess: () => void;
}

const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50MB

export function KBDocumentUploader({ collection, onClose, onSuccess }: Props): JSX.Element {
  const uploadDocument = useKnowledgeStore((s) => s.uploadDocument);
  const uploadStatus = useKnowledgeStore((s) => s.uploadStatus);
  const uploadProgress = useKnowledgeStore((s) => s.uploadProgress);
  const uploadError = useKnowledgeStore((s) => s.uploadError);

  const [dragOver, setDragOver] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback((file: File) => {
    if (file.size > MAX_FILE_SIZE) {
      alert(`文件 ${file.name} 超过 50MB 限制`);
      return;
    }
    setSelectedFile(file);
  }, []);

  const handleDrop = useCallback((e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  }, [handleFile]);

  const handleUpload = useCallback(async () => {
    if (!selectedFile) return;
    await uploadDocument(selectedFile, collection);
    onSuccess();
  }, [selectedFile, collection, uploadDocument, onSuccess]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="card w-full max-w-md shadow-modal"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-warm-700">上传文档到 {collection}</h3>
          <button className="text-warm-400 hover:text-warm-600" onClick={onClose}>
            <span className="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>

        {/* Drop zone */}
        <div
          className={`border-2 border-dashed rounded-xl p-8 text-center transition-colors cursor-pointer ${
            dragOver ? 'border-primary-400 bg-primary-50/50' : 'border-warm-200 hover:border-primary-300'
          } ${uploadStatus === 'uploading' ? 'pointer-events-none opacity-60' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          {selectedFile ? (
            <div className="space-y-2">
              <span className="material-symbols-outlined text-2xl text-primary-500">
                {selectedFile.type === 'application/pdf' ? 'picture_as_pdf' :
                 selectedFile.name.endsWith('.docx') ? 'article' :
                 selectedFile.name.endsWith('.pptx') ? 'slideshow' : 'upload_file'}
              </span>
              <div className="text-sm font-medium text-warm-700">{selectedFile.name}</div>
              <div className="text-xs text-warm-400">
                {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <span className="material-symbols-outlined text-3xl text-warm-300">cloud_upload</span>
              <div className="text-sm text-warm-500">
                拖拽文件到此处或<span className="text-primary-500">点击选择</span>
              </div>
              <div className="text-xs text-warm-400">
                支持 PDF、DOCX、PPTX、TXT、Markdown · 最大 50MB
              </div>
            </div>
          )}
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            accept=".pdf,.docx,.pptx,.txt,.md,.csv,.json,.xml"
            onChange={(e) => { if (e.target.files?.[0]) handleFile(e.target.files[0]); }}
          />
        </div>

        {/* Progress */}
        {uploadStatus === 'uploading' && (
          <div className="mt-4 space-y-2">
            <div className="flex items-center gap-2 text-xs text-primary-600">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
              上传中... 文件将自动分块并向量化
            </div>
            <div className="w-full bg-warm-100 rounded-full h-1.5 overflow-hidden">
              <div
                className="bg-primary-500 h-full rounded-full transition-all duration-300"
                style={{ width: `${uploadProgress || 50}%` }}
              />
            </div>
          </div>
        )}

        {uploadStatus === 'error' && (
          <div className="mt-3 text-xs text-danger-500 bg-danger-50 rounded-lg px-3 py-2">
            {uploadError || '上传失败，请重试'}
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 mt-4 pt-3 border-t border-warm-100">
          <button className="btn-ghost text-xs" onClick={onClose} disabled={uploadStatus === 'uploading'}>
            取消
          </button>
          <button
            className="btn-primary text-xs"
            onClick={handleUpload}
            disabled={!selectedFile || uploadStatus === 'uploading'}
          >
            <span className="material-symbols-outlined text-[14px]">upload</span>
            开始上传
          </button>
        </div>
      </div>
    </div>
  );
}
