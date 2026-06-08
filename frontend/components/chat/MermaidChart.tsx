import { useEffect, useRef, useState, type JSX } from 'react';
import { sanitizeMermaidCode, validateMermaidCode, repairMermaidCode } from '../../utils/mermaidSanitizer';

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
 * - 异步加载 Mermaid 库（通过 CDN 动态导入，优先使用 11.x）
 * - 多级修复管道清洗 LLM 生成的代码
 * - 渲染失败自动降级：sanitize → repair → raw → plain-text fallback
 * - 提供复制代码、下载 SVG/PNG 按钮
 * - 错误状态友好提示 + 修复建议
 *
 * Mermaid 版本策略:
 * - 优先加载 Mermaid 11.x（更好的 Unicode 支持 + 错误恢复）
 * - 如果 11.x CDN 不可用，回退到 10.9.x
 */
const MERMAID_CDN_PRIMARY = 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js';
const MERMAID_CDN_FALLBACK = 'https://cdn.jsdelivr.net/npm/mermaid@10.9.0/dist/mermaid.min.js';

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

  /** 动态加载 Mermaid 库（支持降级） */
  useEffect(() => {
    if (typeof window === 'undefined') return;

    // 检查是否已加载
    if ((window as any).mermaid) {
      setMermaidReady(true);
      return;
    }

    let primaryScript: HTMLScriptElement | null = null;
    let fallbackScript: HTMLScriptElement | null = null;
    let cancelled = false;

    const setupMermaid = () => {
      const mermaid = (window as any).mermaid;
      if (!mermaid) return;
      try {
        mermaid.initialize({
          startOnLoad: false,
          theme: 'default',
          securityLevel: 'loose',
          fontFamily: 'inherit',
          // Mermaid 11.x options — silently ignored by 10.x
          suppressErrorRendering: true,
          maxTextSize: 50000,
        });
      } catch {
        // Older versions may not support all options — safe to ignore
        try {
          mermaid.initialize({
            startOnLoad: false,
            theme: 'default',
            securityLevel: 'loose',
          });
        } catch {
          // Last resort
        }
      }
    };

    const loadFallback = () => {
      if (cancelled) return;
      fallbackScript = document.createElement('script');
      fallbackScript.src = MERMAID_CDN_FALLBACK;
      fallbackScript.async = true;
      fallbackScript.onload = () => {
        if (!cancelled && (window as any).mermaid) {
          setupMermaid();
          setMermaidReady(true);
        }
      };
      fallbackScript.onerror = () => {
        if (!cancelled) {
          setState({ svg: '', error: 'Mermaid 库加载失败，请检查网络连接' });
          setLoading(false);
        }
      };
      document.head.appendChild(fallbackScript);
    };

    // Try primary CDN (Mermaid 11.x) first
    primaryScript = document.createElement('script');
    primaryScript.src = MERMAID_CDN_PRIMARY;
    primaryScript.async = true;
    primaryScript.onload = () => {
      if (!cancelled && (window as any).mermaid) {
        setupMermaid();
        setMermaidReady(true);
      } else if (!cancelled) {
        // Script loaded but mermaid not on window — try fallback
        loadFallback();
      }
    };
    primaryScript.onerror = () => {
      // Primary CDN failed — try fallback
      if (!cancelled) loadFallback();
    };
    document.head.appendChild(primaryScript);

    return () => {
      cancelled = true;
      if (primaryScript?.parentNode) primaryScript.parentNode.removeChild(primaryScript);
      if (fallbackScript?.parentNode) fallbackScript.parentNode.removeChild(fallbackScript);
    };
  }, []);

  /** 渲染 Mermaid 图表（多级修复管道） */
  useEffect(() => {
    if (!mermaidReady || !code.trim()) return;

    let cancelled = false;
    setLoading(true);
    setState({ svg: '', error: null });

    const renderChart = async () => {
      const mermaid = (window as any).mermaid;
      if (!mermaid) {
        if (!cancelled) {
          setState({ svg: '', error: 'Mermaid 未加载' });
          setLoading(false);
        }
        return;
      }

      const id = uniqueIdRef.current;

      // ── Multi-stage repair pipeline ────────────────────────────
      // Stage 1: Basic sanitization (invisible chars, deprecated keywords, etc.)
      // Stage 2: Validate + progressive repair
      // Stage 3: Force-quote all labels
      // Stage 4: Last resort — try raw code
      // Stage 5: Plain-text code block fallback

      const stage1 = sanitizeMermaidCode(code);

      // Try Stage 1: sanitized code
      const preCheck = validateMermaidCode(stage1);
      if (!preCheck) {
        try {
          const { svg } = await mermaid.render(`${id}-s1`, stage1);
          if (!cancelled) {
            setState({ svg, error: null });
            setLoading(false);
            return;
          }
        } catch (err1) {
          // Stage 1 failed — continue to stage 2
          console.debug('Mermaid stage1 (sanitized) failed:', (err1 as Error).message);
        }
      }

      // Stage 2: Progressive repair
      const { code: repaired, repaired: wasRepaired } = repairMermaidCode(code);
      if (wasRepaired) {
        try {
          const { svg } = await mermaid.render(`${id}-s2`, repaired);
          if (!cancelled) {
            setState({ svg, error: null });
            setLoading(false);
            return;
          }
        } catch (err2) {
          console.debug('Mermaid stage2 (repaired) failed:', (err2 as Error).message);
        }
      }

      // Stage 3: Try the sanitized version if different from repaired
      if (stage1 !== repaired && stage1 !== code.trim()) {
        try {
          const { svg } = await mermaid.render(`${id}-s3`, stage1);
          if (!cancelled) {
            setState({ svg, error: null });
            setLoading(false);
            return;
          }
        } catch (err3) {
          console.debug('Mermaid stage3 (sanitized retry) failed:', (err3 as Error).message);
        }
      }

      // Stage 4: Last resort — try raw, unmodified code
      if (stage1 !== code) {
        try {
          const { svg: rawSvg } = await mermaid.render(`${id}-s4`, code);
          if (!cancelled) {
            setState({ svg: rawSvg, error: null });
            setLoading(false);
            return;
          }
        } catch (err4) {
          console.debug('Mermaid stage4 (raw) failed:', (err4 as Error).message);
        }
      }

      // Stage 5: All rendering attempts failed
      // Collect the most informative error
      let lastError = '';
      try {
        await mermaid.render(`${id}-s5`, stage1);
      } catch (finalErr) {
        lastError = finalErr instanceof Error ? finalErr.message : '渲染失败';
      }

      if (!cancelled) {
        setState({ svg: '', error: lastError || 'Mermaid 渲染失败，请检查语法' });
        setLoading(false);
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
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      if (!ctx) {
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

  /** Map common Mermaid error messages to actionable fix hints (Chinese). */
  function getErrorHint(error: string): string | null {
    const msg = error.toLowerCase();
    if (msg.includes('syntax error in text') || msg.includes('parse error')) {
      return '检查节点ID是否包含空格或特殊字符（如中文标点），尝试用引号包裹标签文本';
    }
    if (msg.includes('lexical error')) {
      return '图表代码中包含无法识别的字符，检查是否有未转义的特殊符号（如引号、括号）';
    }
    if (msg.includes('expecting')) {
      const expecting = error.match(/expecting\s+'([^']+)'/i);
      if (expecting) {
        return `解析器期望找到 "${expecting[1]}"，检查该位置是否缺少必要的语法元素`;
      }
      return '语法不完整，检查是否缺少分号、换行或闭合标签';
    }
    if (msg.includes('unclosed') || msg.includes('end')) {
      return '检查 subgraph/end 是否正确配对，确保每个 subgraph 都有对应的 end';
    }
    if (msg.includes('invalid')) {
      return '图表类型或语法无效，第一行应为 flowchart/sequenceDiagram/classDiagram 等';
    }
    return '请检查 Mermaid 语法，确保节点ID无空格、标签用引号包裹、箭头语法正确';
  }

  // ── Loading state ─────────────────────────────────────────────────────
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

  // ── Error state — with code preview + fix hints ───────────────────────
  if (state.error) {
    const lineMatch = state.error.match(/line\s*(\d+)/i);
    const errorLine = lineMatch ? parseInt(lineMatch[1], 10) : null;
    const errorHint = getErrorHint(state.error);

    // Check if we should show the raw code as a fallback
    const lines = code.split('\n');
    const showCodeFallback = lines.length <= 30; // Only show for shorter diagrams

    return (
      <div className={`my-4 rounded-2xl border border-red-200 bg-red-50 ${className}`}>
        <div className="flex items-center justify-between border-b border-red-200 px-4 py-2">
          <div className="flex items-center gap-2">
            <span className="rounded bg-red-100 px-2 py-0.5 text-[11px] font-medium text-red-600">
              Mermaid 语法错误
            </span>
            <span className="text-xs text-red-500 truncate max-w-md" title={state.error}>
              {state.error.length > 80 ? state.error.slice(0, 80) + '...' : state.error}
            </span>
          </div>
          <button
            type="button"
            onClick={() => void handleCopyCode()}
            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-red-500 transition-colors hover:bg-red-100"
            title="复制原始代码"
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
            </svg>
            <span>复制</span>
          </button>
        </div>
        <div className="p-4 space-y-3">
          {errorHint && (
            <div className="rounded-lg bg-amber-50 border border-amber-200 p-2 text-xs text-amber-700">
              <span className="font-medium">💡 修复建议：</span>{errorHint}
            </div>
          )}

          {/* ── Plain-text code block fallback ───────────────────── */}
          {showCodeFallback && (
            <details className="text-xs" open={lines.length <= 10}>
              <summary className="cursor-pointer text-warm-500 hover:text-warm-700">
                查看图表代码{errorLine ? `（错误在第 ${errorLine} 行附近）` : ''}（共 {lines.length} 行）
              </summary>
              <pre className="mt-2 overflow-auto rounded-lg bg-white p-3 text-xs text-warm-700 max-h-60">
                <code>{code}</code>
              </pre>
            </details>
          )}

          {!showCodeFallback && (
            <details className="text-xs">
              <summary className="cursor-pointer text-warm-500 hover:text-warm-700">
                查看图表代码{errorLine ? `（错误在第 ${errorLine} 行附近）` : ''}
              </summary>
              <pre className="mt-2 overflow-auto rounded-lg bg-white p-3 text-xs text-warm-700 max-h-40">
                <code>{code}</code>
              </pre>
            </details>
          )}
        </div>
      </div>
    );
  }

  // ── Success state ─────────────────────────────────────────────────────
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
