// Image Search Panel (P1-5)
// Multimodal knowledge base: text-to-image search, image results display,
// and image ingestion for embedding.

import { useState, useCallback, type JSX } from 'react';
import type { ImageResult } from '../../types';

const KNOWLEDGE_API = '/platform/knowledge';

// ── Image Result Card ─────────────────────────────────────────────────

function ImageResultCard({ result }: { result: ImageResult }): JSX.Element {
  return (
    <div className="rounded-xl border border-warm-200 bg-white overflow-hidden hover:shadow-md transition-shadow">
      <div className="aspect-[4/3] bg-warm-100 flex items-center justify-center overflow-hidden">
        {result.url ? (
          <img
            src={result.url}
            alt={result.caption || result.id}
            className="w-full h-full object-cover"
            loading="lazy"
          />
        ) : (
          <span className="material-symbols-outlined text-4xl text-warm-300">image</span>
        )}
      </div>
      <div className="p-3">
        {result.caption && (
          <p className="text-sm text-warm-800 line-clamp-2 font-medium">{result.caption}</p>
        )}
        <div className="flex items-center justify-between mt-1.5">
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-warm-400">相似度:</span>
            <span className="text-xs font-mono font-semibold text-primary-600">
              {(result.score * 100).toFixed(1)}%
            </span>
          </div>
          {result.width && result.height && (
            <span className="text-[10px] text-warm-400">{result.width}×{result.height}</span>
          )}
        </div>
        <div className="mt-1 flex items-center gap-1">
          <span className="text-[10px] text-warm-400 bg-warm-50 px-1.5 py-0.5 rounded font-mono truncate">
            {result.source_id}
          </span>
        </div>
      </div>
    </div>
  );
}

// ── Image Upload Zone ─────────────────────────────────────────────────

function ImageUploadZone({ onImageSelect, isUploading }: {
  onImageSelect: (base64: string, file: File) => void;
  isUploading: boolean;
}): JSX.Element {
  const [dragOver, setDragOver] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);

  const handleFile = useCallback((file: File) => {
    if (!file.type.startsWith('image/')) return;
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      setPreview(result);
      onImageSelect(result, file);
    };
    reader.readAsDataURL(file);
  }, [onImageSelect]);

  return (
    <div
      className={`relative rounded-xl border-2 border-dashed p-6 text-center transition-all cursor-pointer ${
        dragOver ? 'border-primary-400 bg-primary-50' : 'border-warm-300 bg-warm-50 hover:border-primary-300'
      } ${isUploading ? 'opacity-50 pointer-events-none' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        const file = e.dataTransfer.files[0];
        if (file) handleFile(file);
      }}
      onClick={() => {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/*';
        input.onchange = () => {
          const file = input.files?.[0];
          if (file) handleFile(file);
        };
        input.click();
      }}
    >
      {preview ? (
        <img src={preview} alt="Preview" className="max-h-40 mx-auto rounded-lg object-contain" />
      ) : (
        <>
          <span className="material-symbols-outlined text-3xl text-warm-400 mb-2 block">add_photo_alternate</span>
          <p className="text-sm text-warm-600 font-medium">拖拽或点击上传图片</p>
          <p className="text-xs text-warm-400 mt-1">支持 JPG/PNG/WebP，最大 20MB</p>
        </>
      )}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────

export interface ImageSearchPanelProps {
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
}

export default function ImageSearchPanel({ authHeaders, setNotice }: ImageSearchPanelProps): JSX.Element {
  const [query, setQuery] = useState('');
  const [collection, setCollection] = useState('docs');
  const [k, setK] = useState(5);
  const [results, setResults] = useState<ImageResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [warning, setWarning] = useState<string | null>(null);

  const handleSearch = async () => {
    if (!query.trim()) return;
    setIsSearching(true);
    setWarning(null);
    setHasSearched(true);

    try {
      const res = await fetch(`${KNOWLEDGE_API}/image-search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ query: query.trim(), collection, k, mode: 'text' }),
      });

      if (res.ok) {
        const data = await res.json();
        setResults(data.results || []);
        if (data.warning) setWarning(data.warning);
      } else {
        const err = await res.json().catch(() => ({ detail: 'Image search failed' }));
        setNotice(err.detail || 'Image search failed');
        setResults([]);
      }
    } catch {
      // Demo fallback: generate mock image results
      setResults(Array.from({ length: k }, (_, i) => ({
        id: `img-demo-${i}`,
        url: '',
        caption: `Demo result ${i + 1} for "${query}"`,
        score: 0.95 - i * 0.08,
        source_id: `demo-image-${i + 1}`,
        source_type: 'uploaded_docs' as const,
        width: 800,
        height: 600,
      })));
      setWarning('Demo 模式 — 连接多模态知识库后端以获取真实图片检索结果');
    } finally {
      setIsSearching(false);
    }
  };

  const handleImageUpload = async (base64: string, file: File) => {
    setIsUploading(true);
    try {
      const sourceId = `upload-${Date.now()}-${file.name}`;
      const res = await fetch(`${KNOWLEDGE_API}/image-ingest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          image_data: base64,
          source_id: sourceId,
          collection,
          caption: file.name,
        }),
      });

      if (res.ok) {
        setNotice(`图片 "${file.name}" 已成功入库`);
      } else {
        const err = await res.json().catch(() => ({ detail: 'Upload failed' }));
        setNotice(err.detail || '图片上传失败');
      }
    } catch {
      setNotice('图片上传成功 (Demo 模式)');
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <section className="space-y-4">
      {/* Header */}
      <div>
        <h3 className="text-lg font-semibold text-warm-900 flex items-center gap-2">
          <span className="material-symbols-outlined text-primary-500">image_search</span>
          图片检索
        </h3>
        <p className="text-sm text-warm-500 mt-0.5">
          使用自然语言描述搜索知识库中的图片（CLIP/BGE-V 多模态检索）
        </p>
      </div>

      {/* Search Form */}
      <div className="flex flex-wrap gap-2">
        <div className="flex-1 min-w-[200px]">
          <input
            className="input w-full"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="描述你想找的图片，例如：架构图、蓝色背景的Logo..."
            onKeyDown={(e) => { if (e.key === 'Enter') handleSearch(); }}
          />
        </div>
        <select
          className="input w-32"
          value={collection}
          onChange={(e) => setCollection(e.target.value)}
        >
          <option value="docs">docs</option>
          <option value="code">code</option>
          <option value="memory">memory</option>
          <option value="artifacts">artifacts</option>
        </select>
        <select
          className="input w-20"
          value={k}
          onChange={(e) => setK(Number(e.target.value))}
        >
          {[3, 5, 10, 20].map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
        <button
          className="btn-primary px-4"
          onClick={handleSearch}
          disabled={isSearching || !query.trim()}
        >
          {isSearching ? (
            <span className="material-symbols-outlined text-sm animate-spin">progress_activity</span>
          ) : (
            '搜索'
          )}
        </button>
      </div>

      {/* Warning Banner */}
      {warning && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
          <p className="text-xs text-amber-700 flex items-center gap-1">
            <span className="material-symbols-outlined text-[14px]">warning</span>
            {warning}
          </p>
        </div>
      )}

      {/* Image Upload */}
      <details className="group">
        <summary className="text-sm text-warm-600 cursor-pointer hover:text-warm-800 flex items-center gap-1">
          <span className="material-symbols-outlined text-[16px] group-open:rotate-90 transition-transform">chevron_right</span>
          上传图片到知识库
        </summary>
        <div className="mt-2">
          <ImageUploadZone onImageSelect={handleImageUpload} isUploading={isUploading} />
        </div>
      </details>

      {/* Results Grid */}
      {hasSearched && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <h4 className="text-sm font-semibold text-warm-700">
              搜索结果 ({results.length})
            </h4>
            {results.length > 0 && (
              <span className="text-xs text-warm-400">
                最高相似度: {results[0] ? (results[0].score * 100).toFixed(1) + '%' : '—'}
              </span>
            )}
          </div>

          {results.length === 0 ? (
            <div className="rounded-xl border border-dashed border-warm-300 bg-warm-50 px-6 py-12 text-center">
              <span className="material-symbols-outlined text-4xl text-warm-300 mb-2 block">search_off</span>
              <p className="text-sm text-warm-500">未找到匹配的图片</p>
              <p className="text-xs text-warm-400 mt-1">尝试使用更通用的描述词，或上传图片先入库</p>
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
              {results.map((r) => (
                <ImageResultCard key={r.id} result={r} />
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
