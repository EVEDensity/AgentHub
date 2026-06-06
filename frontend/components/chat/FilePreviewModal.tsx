import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AlertCircle, ChevronUp, Download, Eye, FileText, Loader2, X } from 'lucide-react';

// pdfjs-dist 在 Next.js SSR 下需要动态加载 (使用 window) - 通过 useEffect 引入
type PdfJsModule = typeof import('pdfjs-dist');

// Worker 通过 npm postinstall 复制到 /public/pdf.worker.min.mjs,
// 这里直接引用静态资源, 避免 webpack 解析 .mjs worker 时报错
const PDF_WORKER_URL = '/pdf.worker.min.mjs';

async function loadPdfJs(): Promise<PdfJsModule> {
  const mod = await import('pdfjs-dist');
  if (typeof window !== 'undefined') {
    mod.GlobalWorkerOptions.workerSrc = PDF_WORKER_URL;
  }
  return mod;
}

// ══════════════════════════════════════════════════════════════════════════════
// Types
// ══════════════════════════════════════════════════════════════════════════════

export interface FilePreviewTarget {
  fileId?: string;
  name: string;
  size?: number;
  category?: string;
  // MIME type for image inline content (e.g. "image/png")
  type?: string;
  // Optional inline content (already-known text, e.g. small code uploaded as
  // base64 / data URI). When provided the modal skips the network fetch.
  inlineContent?: string;
  inlineKind?: 'text' | 'markdown' | 'code' | 'image';
}

interface PreviewResponse {
  fileId: string;
  name: string;
  ext: string;
  size: number;
  category: string;
  kind: 'text' | 'markdown' | 'docx' | 'pptx' | 'pdf' | 'image' | 'binary';
  state: 'ok' | 'too_large' | 'binary' | 'missing';
  content?: string;
  contentType?: 'text' | 'html';
  truncated?: boolean;
  totalChars?: number;
  textLength?: number;
  imageCount?: number;
  slideCount?: number;
  error?: string;
  mimeType?: string;
  pageCount?: number;
  extractedText?: string;
  textTruncated?: boolean;
  textError?: string;
}

interface FilePreviewModalProps {
  file: FilePreviewTarget | null;
  onClose: () => void;
  authToken?: string;
}

// ══════════════════════════════════════════════════════════════════════════════
// Helpers
// ══════════════════════════════════════════════════════════════════════════════

const EXT_LANG: Record<string, string> = {
  ts: 'typescript', tsx: 'tsx', js: 'javascript', jsx: 'jsx',
  py: 'python', pyi: 'python',
  rs: 'rust', go: 'go', java: 'java', kt: 'kotlin', swift: 'swift',
  c: 'c', cpp: 'cpp', cc: 'cpp', cxx: 'cpp', h: 'c', hpp: 'cpp',
  cs: 'csharp', fs: 'fsharp', vb: 'vb',
  rb: 'ruby', php: 'php', pl: 'perl', sh: 'bash', bash: 'bash',
  zsh: 'bash', ps1: 'powershell', bat: 'batch', cmd: 'batch',
  sql: 'sql', graphql: 'graphql', gql: 'graphql',
  html: 'html', htm: 'html', css: 'css', scss: 'scss', less: 'less',
  json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'toml', ini: 'ini',
  md: 'markdown', mdx: 'mdx', txt: 'text', cfg: 'ini', conf: 'ini',
  vue: 'vue', svelte: 'svelte', astro: 'astro',
  xml: 'xml', log: 'text', csv: 'text', tsv: 'text',
};

function languageFromName(name: string): string {
  const lower = (name || '').toLowerCase();
  const base = lower.split('/').pop() || lower;
  if (base === 'dockerfile') return 'dockerfile';
  if (base === 'makefile') return 'makefile';
  const parts = base.split('.');
  if (parts.length >= 2) {
    for (let i = parts.length - 1; i >= 0; i--) {
      const ext = parts.slice(i).join('.');
      if (ext in EXT_LANG) return EXT_LANG[ext];
    }
  }
  return 'text';
}

function isMarkdownExt(name: string): boolean {
  const lang = languageFromName(name);
  return lang === 'markdown' || lang === 'mdx';
}

function isCodeExt(name: string): boolean {
  const lang = languageFromName(name);
  return lang !== 'text' && lang !== 'markdown' && lang !== 'mdx';
}

function formatSize(bytes?: number): string {
  if (!bytes || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

// Simple in-memory cache so re-opening the same file is instant.
const _cache = new Map<string, { result: PreviewResponse; ts: number }>();
const CACHE_TTL_MS = 60_000;
function cacheGet(key: string): PreviewResponse | null {
  const entry = _cache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.ts > CACHE_TTL_MS) {
    _cache.delete(key);
    return null;
  }
  return entry.result;
}
function cacheSet(key: string, result: PreviewResponse): void {
  _cache.set(key, { result, ts: Date.now() });
  // Bound the cache to 32 entries to avoid unbounded growth.
  if (_cache.size > 32) {
    const firstKey = _cache.keys().next().value;
    if (firstKey) _cache.delete(firstKey);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Content body — switches on kind
// ══════════════════════════════════════════════════════════════════════════════

function CodeBody({ content, language, fileName }: {
  content: string;
  language: string;
  fileName: string;
}): JSX.Element {
  return (
    <div className="bg-[#1E1E1E] text-sm font-mono leading-relaxed">
      <SyntaxHighlighter
        language={language || 'text'}
        style={oneDark}
        showLineNumbers
        customStyle={{
          margin: 0,
          padding: '1rem 1.25rem',
          background: 'transparent',
          fontSize: '13px',
          lineHeight: '1.55',
        }}
        lineNumberStyle={{
          color: '#5A5A5A',
          paddingRight: '1rem',
          minWidth: '2.5rem',
          userSelect: 'none',
        }}
        codeTagProps={{ style: { fontFamily: "'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace" } }}
      >
        {content}
      </SyntaxHighlighter>
      <div className="px-4 py-1.5 text-[11px] text-warm-400 border-t border-warm-200/40 bg-warm-50/40">
        语言: {language || 'text'} · 文件: {fileName}
      </div>
    </div>
  );
}

function MarkdownBody({ content }: { content: string }): JSX.Element {
  return (
    <div className="px-6 py-5 prose prose-sm max-w-none prose-headings:text-warm-800 prose-p:text-warm-700 prose-a:text-primary-600 prose-code:bg-warm-100 prose-code:px-1 prose-code:rounded">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

function PlainTextBody({ content, fileName }: { content: string; fileName: string }): JSX.Element {
  return (
    <pre
      className="px-6 py-5 text-[13px] leading-relaxed font-mono text-warm-700 whitespace-pre-wrap break-words"
      style={{ fontFamily: "'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace" }}
    >
      {content}
    </pre>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// PDF 渲染 (使用 pdfjs-dist 把每页画到 canvas, 支持滚动浏览全部页面)
// ══════════════════════════════════════════════════════════════════════════════

interface PdfPageData {
  pageNumber: number;
  dataUrl: string; // PNG dataURL
  width: number;
  height: number;
}

function base64ToUint8Array(b64: string): Uint8Array {
  const binary = atob(b64);
  const len = binary.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

async function renderPdfPages(b64: string, scale: number): Promise<PdfPageData[]> {
  const pdfjs = await loadPdfJs();
  const data = base64ToUint8Array(b64);
  const loadingTask = pdfjs.getDocument({ data });
  const pdf = await loadingTask.promise;
  const out: PdfPageData[] = [];
  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i);
    const viewport = page.getViewport({ scale });
    const canvas = document.createElement('canvas');
    canvas.width = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas 2D context 不可用');
    await page.render({ canvasContext: ctx, viewport }).promise;
    out.push({
      pageNumber: i,
      dataUrl: canvas.toDataURL('image/png'),
      width: canvas.width,
      height: canvas.height,
    });
    // 主动清理, 避免多页时内存爆掉
    page.cleanup();
  }
  await pdf.destroy();
  return out;
}

function PdfBody({ content, pageCount, fileName }: {
  content: string;
  pageCount?: number;
  fileName: string;
}): JSX.Element {
  const [pages, setPages] = useState<PdfPageData[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scale, setScale] = useState(1.4);
  const [rendering, setRendering] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setPages(null);
    setError(null);
    setRendering(true);
    (async () => {
      try {
        const data = await renderPdfPages(content, scale);
        if (!cancelled) setPages(data);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setRendering(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [content, scale]);

  return (
    <div className="bg-warm-100/50">
      {/* Toolbar */}
      <div className="sticky top-0 z-10 flex items-center justify-between gap-2 px-4 py-2 bg-white/95 border-b border-warm-200 backdrop-blur-sm">
        <div className="text-[11px] text-warm-500 truncate">
          {fileName} · {pageCount ? `${pageCount} 页` : ''}
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setScale((s) => Math.max(0.5, s - 0.25))}
            className="inline-flex h-6 w-6 items-center justify-center rounded border border-warm-200 bg-white text-warm-600 hover:border-primary-300 hover:text-primary-600 text-xs"
            title="缩小"
            disabled={scale <= 0.5}
          >
            −
          </button>
          <span className="text-[11px] text-warm-600 tabular-nums w-10 text-center">
            {Math.round(scale * 100)}%
          </span>
          <button
            type="button"
            onClick={() => setScale((s) => Math.min(3, s + 0.25))}
            className="inline-flex h-6 w-6 items-center justify-center rounded border border-warm-200 bg-white text-warm-600 hover:border-primary-300 hover:text-primary-600 text-xs"
            title="放大"
            disabled={scale >= 3}
          >
            +
          </button>
        </div>
      </div>

      {/* Pages */}
      {error ? (
        <ErrorBody title="PDF 渲染失败" message={error} />
      ) : rendering || !pages ? (
        <div className="flex flex-col items-center justify-center py-20 px-6">
          <Loader2 className="h-7 w-7 text-primary-500 animate-spin mb-3" />
          <div className="text-sm text-warm-600">正在渲染 PDF 页面…</div>
          {pageCount ? <div className="mt-1 text-[11px] text-warm-400">共 {pageCount} 页</div> : null}
        </div>
      ) : (
        <div className="flex flex-col items-center gap-3 px-4 py-4">
          {pages.map((p) => (
            <div
              key={p.pageNumber}
              className="rounded-md overflow-hidden border border-warm-200 shadow-sm bg-white"
            >
              <div className="px-3 py-1 text-[10px] text-warm-400 bg-warm-50 border-b border-warm-200 text-center">
                第 {p.pageNumber} / {pages.length} 页
              </div>
              <img
                src={p.dataUrl}
                alt={`第 ${p.pageNumber} 页`}
                className="block max-w-full h-auto"
                style={{ width: `${p.width}px`, maxWidth: '100%' }}
                draggable={false}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ImageBody({ content, mimeType, fileName }: {
  content: string;
  mimeType?: string;
  fileName: string;
}): JSX.Element {
  // 支持两种格式:
  //   1. 内联 data URL (FileReader.readAsDataURL 的结果, 以 "data:" 开头)
  //   2. 后端返回的 raw base64 (不带 data: 前缀)
  const dataUrl: string = content.startsWith('data:')
    ? content
    : `data:${mimeType || 'application/octet-stream'};base64,${content}`;
  return (
    <div className="flex items-center justify-center bg-warm-100/40 p-4 min-h-[200px]">
      <img
        src={dataUrl}
        alt={fileName}
        className="max-w-full max-h-[80vh] object-contain rounded shadow-sm"
        onError={(e) => {
          // 如果图片加载失败，显示占位符
          const el = e.currentTarget;
          if (!el.dataset.retried) {
            el.dataset.retried = '1';
            // 可能是 mimeType 不对，尝试去掉 mimeType 直接用原始内容
            if (!content.startsWith('data:')) {
              el.src = content; // 可能是完整 data URL 但没被识别
            }
          }
        }}
      />
    </div>
  );
}

function DocxHtmlBody({ content, fileName }: {
  content: string;
  fileName: string;
}): JSX.Element {
  return (
    <div className="bg-white" style={{ minHeight: '300px' }}>
      <iframe
        srcDoc={content}
        title={`预览: ${fileName}`}
        sandbox="allow-scripts allow-same-origin"
        className="w-full border-0"
        style={{ minHeight: '400px', height: '70vh' }}
        onLoad={(e) => {
          // Auto-resize iframe to fit content height
          try {
            const doc = (e.currentTarget as HTMLIFrameElement).contentDocument;
            if (doc) {
              const h = doc.documentElement.scrollHeight;
              if (h > 300) {
                (e.currentTarget as HTMLIFrameElement).style.height = `${h}px`;
              }
            }
          } catch {
            // Cross-origin restriction — keep default height
          }
        }}
      />
    </div>
  );
}

function PptBody({ content, fileName, slideCount }: {
  content: string;
  fileName: string;
  slideCount?: number;
}): JSX.Element {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [iframeH, setIframeH] = useState(600);
  const [showScrollTop, setShowScrollTop] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Auto-height the iframe after load
  const measure = useCallback(() => {
    try {
      const doc = iframeRef.current?.contentDocument;
      if (doc) {
        const h = Math.max(
          doc.documentElement.scrollHeight,
          doc.body.scrollHeight,
          doc.documentElement.offsetHeight,
          doc.body.offsetHeight
        );
        if (h > 100) setIframeH(h + 16);
      }
    } catch { /* cross-origin */ }
  }, []);

  const handleLoad = useCallback(() => {
    measure();
    setTimeout(measure, 300);
    setTimeout(measure, 1000);
    setTimeout(measure, 2500);
  }, [measure]);

  return (
    <div className="bg-[#f0f2f5]" style={{ minHeight: '300px' }}>
      {/* Toolbar */}
      <div className="sticky top-0 z-10 flex items-center justify-between gap-2 px-4 py-2 bg-white/95 border-b border-warm-200 backdrop-blur-sm">
        <div className="text-[11px] text-warm-500 truncate">
          {fileName}{slideCount ? ` · ${slideCount} 页` : ''}
        </div>
      </div>

      {/* Scrollable iframe wrapper */}
      <div
        ref={bodyRef}
        className="overflow-auto"
        style={{ maxHeight: '75vh' }}
        onScroll={(e) => setShowScrollTop(e.currentTarget.scrollTop > 200)}
      >
        <iframe
          ref={iframeRef}
          srcDoc={content}
          title={`PPT 预览: ${fileName}`}
          sandbox="allow-scripts allow-same-origin"
          className="w-full border-0 block"
          style={{ height: iframeH, minHeight: 400 }}
          onLoad={handleLoad}
        />
      </div>
    </div>
  );
}

function BinaryBody({ fileName, size, hint }: {
  fileName: string;
  size?: number;
  hint?: string;
}): JSX.Element {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
      <div className="h-14 w-14 rounded-full bg-warm-100 flex items-center justify-center mb-4">
        <FileText className="h-7 w-7 text-warm-400" />
      </div>
      <div className="text-sm font-semibold text-warm-700 mb-1">暂不支持预览</div>
      <div className="text-xs text-warm-500 max-w-sm">
        {hint || '该文件类型暂不支持在线预览。你可以下载文件后在本地打开。'}
      </div>
      {size ? (
        <div className="mt-3 text-[11px] text-warm-400">文件大小: {formatSize(size)}</div>
      ) : null}
      <div className="mt-4 text-[11px] text-warm-400">文件: {fileName}</div>
    </div>
  );
}

function ErrorBody({ title, message, onRetry }: {
  title: string;
  message: string;
  onRetry?: () => void;
}): JSX.Element {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
      <div className="h-14 w-14 rounded-full bg-danger-50 flex items-center justify-center mb-4">
        <AlertCircle className="h-7 w-7 text-danger-500" />
      </div>
      <div className="text-sm font-semibold text-warm-700 mb-1">{title}</div>
      <div className="text-xs text-warm-500 max-w-md whitespace-pre-wrap">{message}</div>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 inline-flex items-center gap-1.5 rounded-md border border-warm-200 bg-white px-3 py-1.5 text-xs font-medium text-warm-700 hover:border-primary-300 hover:text-primary-600 transition-colors"
        >
          重试
        </button>
      ) : null}
    </div>
  );
}

function LoadingBody({ fileName }: { fileName: string }): JSX.Element {
  return (
    <div className="flex flex-col items-center justify-center py-20 px-6">
      <Loader2 className="h-7 w-7 text-primary-500 animate-spin mb-3" />
      <div className="text-sm text-warm-600">正在加载 {fileName}…</div>
      <div className="mt-1 text-[11px] text-warm-400">首次打开可能需要几秒</div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Main modal
// ══════════════════════════════════════════════════════════════════════════════

export default function FilePreviewModal({
  file,
  onClose,
  authToken,
}: FilePreviewModalProps): JSX.Element | null {
  const [result, setResult] = useState<PreviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [showScrollTop, setShowScrollTop] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  const ext = useMemo(
    () => (file?.name ? (file.name.split('.').pop() || '').toLowerCase() : ''),
    [file?.name],
  );
  const language = useMemo(
    () => (file?.name ? languageFromName(file.name) : 'text'),
    [file?.name],
  );
  const cacheKey = file ? `preview:${file.fileId || 'inline'}` : '';

  // ── Fetch preview content ───────────────────────────────────────────
  useEffect(() => {
    if (!file) {
      setResult(null);
      setError(null);
      return;
    }
    let cancelled = false;

    async function run() {
      // Inline fast-path — 内容已在内存中，无需网络请求
      if (file!.inlineContent !== undefined) {
        const kind = file!.inlineKind
          || (isMarkdownExt(file!.name) ? 'markdown'
            : (isCodeExt(file!.name) ? 'code'
              : (file!.category === 'image' ? 'image' : 'text')));
        setResult({
          fileId: file!.fileId || 'inline',
          name: file!.name,
          ext: ext,
          size: file!.size || 0,
          category: file!.category || 'document',
          kind: kind === 'code' ? 'text' : (kind as PreviewResponse['kind']),
          state: 'ok',
          content: file!.inlineContent,
          truncated: false,
          // 对于内联图片, 传递 MIME 类型以便 ImageBody 正确处理 data URL
          mimeType: kind === 'image' ? (file!.type || 'image/png') : undefined,
        });
        setLoading(false);
        setError(null);
        return;
      }

      if (!file!.fileId) {
        setError('该文件尚未上传完成，无法预览');
        setLoading(false);
        return;
      }

      const cached = cacheGet(cacheKey);
      if (cached) {
        setResult(cached);
        setLoading(false);
        setError(null);
        return;
      }

      setLoading(true);
      setError(null);
      try {
        const token = authToken || (typeof window !== 'undefined' ? localStorage.getItem('agenthub_token') : '') || '';
        const url = `/api/files/preview/${encodeURIComponent(file!.fileId)}`;
        const res = await fetch(url, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) {
          let detail = '';
          try {
            const j = await res.json();
            detail = j.detail || '';
          } catch { /* ignore */ }
          throw new Error(detail || `HTTP ${res.status} ${res.statusText || ''}`.trim());
        }
        const data = (await res.json()) as PreviewResponse;
        if (cancelled) return;
        cacheSet(cacheKey, data);
        setResult(data);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        setResult(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    run();
    return () => {
      cancelled = true;
    };
  }, [file, cacheKey, reloadKey, ext, authToken]);

  // ── Reset scroll on file change ────────────────────────────────────
  useEffect(() => {
    if (bodyRef.current) {
      bodyRef.current.scrollTop = 0;
    }
    setShowScrollTop(false);
  }, [file?.fileId, file?.name]);

  // ── ESC to close + focus trap (light) ──────────────────────────────
  useEffect(() => {
    if (!file) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    }
    document.addEventListener('keydown', onKey);
    // Lock body scroll while modal is open
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [file, onClose]);

  const handleRetry = useCallback(() => {
    // Drop the cache entry and re-run the fetch effect
    if (cacheKey) _cache.delete(cacheKey);
    setReloadKey((k) => k + 1);
  }, [cacheKey]);

  const handleDownload = useCallback(() => {
    const fileId = file?.fileId;
    if (!fileId) return;
    const token = authToken || (typeof window !== 'undefined' ? localStorage.getItem('agenthub_token') : '') || '';
    const a = document.createElement('a');
    a.href = `/api/files/download/${encodeURIComponent(fileId)}` + (token ? `?token=${encodeURIComponent(token)}` : '');
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.download = file.name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }, [file, authToken]);

  if (!file) return null;

  // Decide which body to render
  let body: JSX.Element;
  if (loading) {
    body = <LoadingBody fileName={file.name} />;
  } else if (error) {
    body = (
      <ErrorBody
        title="加载失败"
        message={error}
        onRetry={file.fileId ? handleRetry : undefined}
      />
    );
  } else if (!result) {
    body = <LoadingBody fileName={file.name} />;
  } else if (result.state === 'missing') {
    body = (
      <ErrorBody
        title="文件已不存在"
        message={`文件 "${file.name}" 在服务器上找不到。可能上传未完成，或文件已被清理。`}
        onRetry={handleRetry}
      />
    );
  } else if (result.state === 'too_large') {
    body = (
      <BinaryBody
        fileName={file.name}
        size={result.size}
        hint="文件超过 5 MB，暂不支持在线预览。请下载到本地查看。"
      />
    );
  } else if (result.state === 'binary' || result.kind === 'binary') {
    body = (
      <BinaryBody
        fileName={file.name}
        size={result.size}
        hint={result.error ? `后端解析失败: ${result.error}` : '该文件类型暂不支持在线预览（可能是图片、压缩包、二进制等）。'}
      />
    );
  } else if (result.kind === 'markdown' && result.content !== undefined) {
    body = <MarkdownBody content={result.content} />;
  } else if (result.kind === 'text' && result.content !== undefined) {
    // code file → syntax highlight; otherwise plain text
    if (isCodeExt(file.name) && !isMarkdownExt(file.name)) {
      body = <CodeBody content={result.content} language={language} fileName={file.name} />;
    } else {
      body = <PlainTextBody content={result.content} fileName={file.name} />;
    }
  } else if (result.kind === 'docx' && result.content !== undefined) {
    body = (result.contentType === 'html')
      ? <DocxHtmlBody content={result.content} fileName={file.name} />
      : <PlainTextBody content={result.content} fileName={file.name} />;
  } else if (result.kind === 'pptx' && result.content !== undefined) {
    body = <PptBody content={result.content} fileName={file.name} slideCount={result.slideCount} />;
  } else if (result.kind === 'pdf' && result.content !== undefined) {
    body = <PdfBody content={result.content} pageCount={result.pageCount} fileName={file.name} />;
  } else if (result.kind === 'image' && result.content !== undefined) {
    body = <ImageBody content={result.content} mimeType={result.mimeType} fileName={file.name} />;
  } else {
    body = (
      <ErrorBody
        title="无法渲染"
        message="后端返回了意料之外的数据格式。"
        onRetry={handleRetry}
      />
    );
  }

  const truncated = result?.truncated;
  const totalChars = result?.totalChars;
  const fileSize = result?.size ?? file.size;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`预览文件: ${file.name}`}
      // 外层用 overflow-y-auto, 保证内容过多时整个 modal 可滚动
      className="fixed inset-0 z-[1000] overflow-y-auto"
      onMouseDown={(e) => {
        // Click on backdrop (not on the dialog) closes the modal
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/55 backdrop-blur-sm" aria-hidden="true" />

      {/* Dialog wrapper - 保证高度不超过视口, 内容多时整体上下滚动 */}
      <div
        className="relative z-10 min-h-full flex items-start sm:items-center justify-center p-3 sm:p-6"
        onMouseDown={(e) => e.stopPropagation()}
      >
      <div
        ref={dialogRef}
        className="relative w-full max-w-5xl max-h-[90vh] flex flex-col rounded-xl bg-white shadow-2xl border border-warm-200 overflow-hidden"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <header className="flex items-center gap-3 px-4 sm:px-5 py-3 border-b border-warm-200 bg-warm-50/70 shrink-0">
          <div className="h-8 w-8 rounded-lg bg-primary-50 flex items-center justify-center shrink-0">
            <Eye className="h-4 w-4 text-primary-600" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-warm-800 truncate" title={file.name}>
              {file.name}
            </div>
            <div className="text-[11px] text-warm-500 truncate">
              {fileSize ? `${formatSize(fileSize)} · ` : ''}{ext ? ext.toUpperCase().replace('.', '') || '文件' : '文件'}
              {result?.kind ? ` · ${result.kind}` : ''}
              {result?.slideCount ? ` · ${result.slideCount} 页` : ''}
              {result?.imageCount ? ` · ${result.imageCount} 张图片` : ''}
              {result?.textLength ? ` · ${result.textLength.toLocaleString()} 字` : ''}
              {truncated && totalChars ? ` · 已截断 (${totalChars.toLocaleString()} 字符)` : ''}
              {result?.state && result.state !== 'ok' ? ` · ${result.state}` : ''}
            </div>
          </div>

          {file.fileId ? (
            <button
              type="button"
              onClick={handleDownload}
              className="shrink-0 inline-flex items-center gap-1.5 rounded-md border border-warm-200 bg-white px-2.5 py-1.5 text-xs font-medium text-warm-700 hover:border-primary-300 hover:text-primary-600 transition-colors"
              title="下载原文件"
            >
              <Download className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">下载</span>
            </button>
          ) : null}

          <button
            type="button"
            onClick={onClose}
            className="shrink-0 inline-flex h-8 w-8 items-center justify-center rounded-md text-warm-500 hover:bg-warm-100 hover:text-warm-800 transition-colors"
            aria-label="关闭预览"
            title="关闭 (Esc)"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        {/* Body */}
        <div
          ref={bodyRef}
          className="flex-1 min-h-0 overflow-y-auto bg-white"
          style={{
            scrollbarWidth: 'thin',
            scrollbarColor: 'rgba(0,0,0,0.25) transparent',
            // iOS 弹性滚动
            WebkitOverflowScrolling: 'touch',
          }}
          onScroll={(e) => {
            const el = e.currentTarget;
            setShowScrollTop(el.scrollTop > 200);
          }}
        >
          {body}
        </div>

        {/* 滚动到顶部悬浮按钮 (内容很多时) */}
        {showScrollTop && (
          <button
            type="button"
            onClick={() => bodyRef.current?.scrollTo({ top: 0, behavior: 'smooth' })}
            className="absolute right-4 bottom-12 z-20 inline-flex h-8 w-8 items-center justify-center rounded-full bg-white/95 border border-warm-200 shadow-md text-warm-600 hover:text-primary-600 hover:border-primary-300 transition-colors"
            title="回到顶部"
            aria-label="回到顶部"
          >
            <ChevronUp className="h-4 w-4" />
          </button>
        )}

        {/* Footer hint */}
        <footer className="px-4 sm:px-5 py-2 border-t border-warm-200 bg-warm-50/50 text-[11px] text-warm-500 flex items-center justify-between shrink-0">
          <span>按 Esc 关闭 · 点击背景也可关闭</span>
          <span className="hidden sm:inline">AgentHub 文件预览</span>
        </footer>
      </div>
      </div>
    </div>
  );
}
