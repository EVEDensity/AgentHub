import { useEffect, useRef, useState, type JSX } from 'react';

export interface MermaidChartProps {
  /** Mermaid 图表代码 */
  code: string;
  /** 唯一标识符（用于下载时命名） */
  chartId?: string;
  /** 额外的容器 className */
  className?: string;
}

interface MermaidState {
  svg: string;
  error: string | null;
}

/**
 * MermaidChart - Mermaid 图表渲染组件
 *
 * 功能：
 * - 异步加载 Mermaid 库（通过 CDN 动态导入）
 * - 渲染 Mermaid 图表代码为 SVG
 * - 提供复制代码、下载 SVG/PNG 按钮
 * - 错误状态友好提示
 */
export default function MermaidChart({
  code,
  chartId = 'chart',
  className = '',
}: MermaidChartProps): JSX.Element {
  const [state, setState] = useState<MermaidState>({ svg: '', error: null });
  const [loading, setLoading] = useState(true);
  const [mermaidReady, setMermaidReady] = useState(false);
  const [copySuccess, setCopySuccess] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const uniqueIdRef = useRef(`mermaid-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);

  /** 动态加载 Mermaid 库 */
  useEffect(() => {
    if (typeof window === 'undefined') return;

    // 检查是否已加载
    if ((window as any).mermaid) {
      setMermaidReady(true);
      return;
    }

    // 动态加载 Mermaid CDN
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/mermaid@10.6.1/dist/mermaid.min.js';
    script.async = true;
    script.onload = () => {
      if ((window as any).mermaid) {
        (window as any).mermaid.initialize({
          startOnLoad: false,
          theme: 'default',
          securityLevel: 'loose',
          fontFamily: 'inherit',
        });
        setMermaidReady(true);
      }
    };
    script.onerror = () => {
      setState({ svg: '', error: 'Mermaid 库加载失败，请检查网络' });
      setLoading(false);
    };
    document.head.appendChild(script);

    return () => {
      // 清理
      if (script.parentNode) {
        script.parentNode.removeChild(script);
      }
    };
  }, []);

  /** 渲染 Mermaid 图表 */
  useEffect(() => {
    if (!mermaidReady || !code.trim()) return;

    let cancelled = false;
    setLoading(true);
    setState({ svg: '', error: null });

    const renderChart = async () => {
      try {
        const mermaid = (window as any).mermaid;
        if (!mermaid) {
          throw new Error('Mermaid 未加载');
        }

        const id = uniqueIdRef.current;
        const { svg } = await mermaid.render(id, code);

        if (!cancelled) {
          setState({ svg, error: null });
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          const errorMsg = err instanceof Error ? err.message : '渲染失败';
          setState({ svg: '', error: errorMsg });
          setLoading(false);
        }
      }
    };

    void renderChart();

    return () => {
      cancelled = true;
    };
  }, [code, mermaidReady]);

  /** 复制 Mermaid 代码 */
  async function handleCopyCode(): Promise<void> {
    try {
      await navigator.clipboard.writeText(code);
      setCopySuccess(true);
      setTimeout(() => setCopySuccess(false), 2000);
    } catch (err) {
      console.error('复制失败:', err);
    }
  }

  /** 下载 SVG */
  function handleDownloadSVG(): void {
    if (!state.svg) return;
    const blob = new Blob([state.svg], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${chartId}-${Date.now()}.svg`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  /** 下载 PNG */
  function handleDownloadPNG(): void {
    if (!state.svg) return;
    try {
      // 创建 canvas 将 SVG 转为 PNG
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        // fallback: 下载 SVG
        handleDownloadSVG();
        return;
      }

      const img = new Image();
      const svgBlob = new Blob([state.svg], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(svgBlob);

      img.onload = () => {
        canvas.width = img.width || 800;
        canvas.height = img.height || 600;
        ctx.fillStyle = '#FFFFFF';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0);
        URL.revokeObjectURL(url);

        canvas.toBlob((blob) => {
          if (blob) {
            const pngUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = pngUrl;
            a.download = `${chartId}-${Date.now()}.png`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(pngUrl);
          }
        }, 'image/png');
      };

      img.onerror = () => {
        URL.revokeObjectURL(url);
        handleDownloadSVG();
      };

      img.src = url;
    } catch (err) {
      console.error('PNG 下载失败:', err);
      handleDownloadSVG();
    }
  }

  /** 在新窗口打开 SVG */
  function handleOpenInNewTab(): void {
    if (!state.svg) return;
    const blob = new Blob([state.svg], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    window.open(url, '_blank');
  }

  // 加载中
  if (loading) {
    return (
      <div className={`my-4 rounded-2xl border border-warm-150 bg-white ${className}`}>
        <div className="flex items-center justify-between border-b border-warm-150 px-4 py-2">
          <div className="flex items-center gap-2">
            <span className="rounded bg-primary-50 px-2 py-0.5 text-[11px] font-medium text-primary-600">
              Mermaid
            </span>
            <span className="text-xs text-warm-500">图表加载中...</span>
          </div>
        </div>
        <div className="flex items-center justify-center p-8">
          <div className="flex flex-col items-center gap-2">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-warm-200 border-t-primary-500" />
            <span className="text-sm text-warm-500">正在渲染图表...</span>
          </div>
        </div>
      </div>
    );
  }

  // 错误状态
  if (state.error) {
    return (
      <div className={`my-4 rounded-2xl border border-red-200 bg-red-50 ${className}`}>
        <div className="flex items-center justify-between border-b border-red-200 px-4 py-2">
          <div className="flex items-center gap-2">
            <span className="rounded bg-red-100 px-2 py-0.5 text-[11px] font-medium text-red-600">
              Mermaid 错误
            </span>
            <span className="text-xs text-red-500">{state.error}</span>
          </div>
        </div>
        <div className="p-4">
          <pre className="overflow-auto rounded-lg bg-white p-3 text-xs text-warm-700">
            <code>{code}</code>
          </pre>
        </div>
      </div>
    );
  }

  // 成功渲染
  return (
    <div className={`my-4 rounded-2xl border border-warm-150 bg-white ${className}`}>
      {/* 工具栏 */}
      <div className="flex items-center justify-between border-b border-warm-150 px-4 py-2">
        <div className="flex items-center gap-2">
          <span className="rounded bg-primary-50 px-2 py-0.5 text-[11px] font-medium text-primary-600">
            Mermaid
          </span>
          <span className="text-xs text-warm-500">图表</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => void handleCopyCode()}
            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-warm-500 transition-colors hover:bg-warm-50 hover:text-warm-700"
            title="复制 Mermaid 代码"
          >
            {copySuccess ? (
              <>
                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
                <span>已复制</span>
              </>
            ) : (
              <>
                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                </svg>
                <span>复制代码</span>
              </>
            )}
          </button>
          <button
            type="button"
            onClick={handleDownloadSVG}
            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-warm-500 transition-colors hover:bg-warm-50 hover:text-warm-700"
            title="下载 SVG"
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
            <span>SVG</span>
          </button>
          <button
            type="button"
            onClick={handleDownloadPNG}
            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-warm-500 transition-colors hover:bg-warm-50 hover:text-warm-700"
            title="下载 PNG"
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
            <span>PNG</span>
          </button>
          <button
            type="button"
            onClick={handleOpenInNewTab}
            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-warm-500 transition-colors hover:bg-warm-50 hover:text-warm-700"
            title="在新窗口打开"
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <polyline points="15 3 21 3 21 9" />
              <line x1="10" y1="14" x2="21" y2="3" />
            </svg>
          </button>
        </div>
      </div>

      {/* 图表内容 */}
      <div
        ref={containerRef}
        className="mermaid-container overflow-auto p-4 text-center"
        dangerouslySetInnerHTML={{ __html: state.svg }}
        style={{ maxHeight: '70vh' }}
      />
    </div>
  );
}
