'use client';

import { useState, useRef, type JSX } from 'react';
import { useTemplateStore } from '../../stores/templateStore';

interface Props {
  onClose: () => void;
}

export function TemplateImportModal({ onClose }: Props): JSX.Element {
  const importTemplate = useTemplateStore((s) => s.importTemplate);
  const importError = useTemplateStore((s) => s.importError);
  const setImportError = useTemplateStore((s) => s.setImportError);

  const [jsonText, setJsonText] = useState('');
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleImport = async () => {
    if (!jsonText.trim()) return;
    setImporting(true);
    const ok = await importTemplate(jsonText);
    setImporting(false);
    if (ok) onClose();
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setJsonText(reader.result as string);
      setImportError('');
    };
    reader.onerror = () => setImportError('文件读取失败');
    reader.readAsText(file);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="card w-full max-w-lg shadow-modal" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-warm-700">导入模板</h3>
          <button className="text-warm-400 hover:text-warm-600" onClick={onClose}>
            <span className="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>

        <div className="space-y-3">
          {/* File upload */}
          <div
            className="border-2 border-dashed border-warm-200 rounded-lg p-6 text-center cursor-pointer hover:border-primary-300 transition-colors"
            onClick={() => fileInputRef.current?.click()}
          >
            <span className="material-symbols-outlined text-2xl text-warm-300">upload_file</span>
            <div className="text-xs text-warm-500 mt-1">
              点击上传 JSON 文件或粘贴下方
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              className="hidden"
              onChange={handleFileUpload}
            />
          </div>

          {/* JSON textarea */}
          <div>
            <label className="text-xs text-warm-500 block mb-1">JSON 内容</label>
            <textarea
              className="input-field w-full text-xs font-mono"
              rows={10}
              placeholder='{"id": "my-template", "name": "My Template", ...}'
              value={jsonText}
              onChange={(e) => { setJsonText(e.target.value); setImportError(''); }}
            />
          </div>

          {importError && (
            <div className="text-xs text-danger-500 bg-danger-50 rounded-lg px-3 py-2">
              {importError}
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-3 border-t border-warm-100">
            <button className="btn-ghost text-xs" onClick={onClose} disabled={importing}>
              取消
            </button>
            <button
              className="btn-primary text-xs"
              onClick={handleImport}
              disabled={!jsonText.trim() || importing}
            >
              {importing ? (
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white inline-block" />
              ) : (
                <span className="material-symbols-outlined text-[14px]">file_upload</span>
              )}
              导入
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
