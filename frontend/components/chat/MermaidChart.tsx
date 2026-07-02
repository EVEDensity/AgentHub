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
 * Intercept console.error during Mermaid rendering to suppress the noisy
 * "Syntax error in text" messages that Mermaid 11.x logs internally on
 * every failed parse attempt.  Our multi-stage pipeline intentionally
 * tries multiple render strategies — each failed attempt is expected and
 * should not pollute the browser console.
 *
 * Returns a restore function that reinstates the original console.error.
 */
function suppressConsoleErrors(): () => void {
  const original = console.error;
  // Match Mermaid's own error patterns — we don't want to suppress
  // legitimate errors from other libraries or our own code.
  const MERMAID_ERROR_PATTERNS = [
    /Syntax error in text/i,
    /Parse error/i,
    /Lexical error/i,
    /mermaid version/i,
    /Unhandled Rejection/i,
    /Error: Parse error/i,
  ];

  console.error = function (...args: any[]) {
    const msg = args.length > 0 ? String(args[0]) : '';
    const isMermaidNoise = MERMAID_ERROR_PATTERNS.some((p) => p.test(msg));
    if (isMermaidNoise) {
      // Swallow — our error handling below produces user-visible messages
      return;
    }
    original.apply(console, args);
  } as typeof console.error;

  return () => {
    console.error = original;
  };
}

/**
 * Check whether the Mermaid library loaded is version 11.x.
 * Mermaid 11 introduced breaking parser changes and removed
 * `suppressErrorRendering`.  We adapt initialization and error
 * handling accordingly.
 */
function isMermaidV11(mermaid: any): boolean {
  try {
    const ver = mermaid.version || mermaid.mermaidAPI?.getConfig?.()?.version || '';
    return ver.startsWith('11.');
  } catch {
    // If we can't determine the version, assume v11 (stricter handling)
    return true;
  }
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
 * - 渲染过程中抑制 Mermaid 内部的 console.error 噪音
 *
 * Mermaid 版本策略:
 * - 优先加载 Mermaid 11.x（更好的 Unicode 支持 + 错误恢复）
 * - 如果 11.x CDN 不可用，回退到 10.9.x（更宽松的解析器）
 */
// 11.4.1 是 11.x 分支最后一个被广泛验证的稳定版本
// 11.15.0+ 引入了更严格的解析器（裸引号/未引用 subgraph 名称等），频繁触发 Syntax error in text
// 10.9.1 是 10.x 的最终稳定版本，作为兜底
const MERMAID_CDN_PRIMARY = 'https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js';
const MERMAID_CDN_FALLBACK = 'https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js';

/** Singleton: track whether Mermaid 11.x has accumulated too many failures. */
let mermaidV11GlobalFailureCount = 0;
const MERMAID_V11_FAILURE_THRESHOLD = 5;

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
  /** Track per-component render failures to decide on fallback */
  const failureCountRef = useRef(0);

  /** 动态加载 Mermaid 库（支持降级） */
  useEffect(() => {
    if (typeof window === 'undefined') return;

    // Reset global failure count when too high — the user may have
    // navigated to a new conversation with better Mermaid code.
    if (mermaidV11GlobalFailureCount >= MERMAID_V11_FAILURE_THRESHOLD) {
      mermaidV11GlobalFailureCount = 0;
    }

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
      const v11 = isMermaidV11(mermaid);
      try {
        if (v11) {
          // Mermaid 11.x: suppressErrorRendering was removed.
          // Use logLevel: 'fatal' to minimize internal console noise,
          // and set maxTextSize for large diagrams.
          mermaid.initialize({
            startOnLoad: false,
            theme: 'default',
            securityLevel: 'loose',
            fontFamily: 'inherit',
            logLevel: 'fatal' as any, // 0 = fatal only, minimizes console spam
            maxTextSize: 90000,
          });
        } else {
          // Mermaid 10.x fallback
          mermaid.initialize({
            startOnLoad: false,
            theme: 'default',
            securityLevel: 'loose',
            fontFamily: 'inherit',
            maxTextSize: 90000,
          });
        }
      } catch {
        // Last-resort minimal config
        try {
          mermaid.initialize({
            startOnLoad: false,
            theme: 'default',
            securityLevel: 'loose',
          });
        } catch {
          // Give up — will error later
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

  /** 渲染 Mermaid 图表（多级修复管道 + 控制台噪声抑制） */
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
      // Each stage attempts a different sanitization strategy.
      // ALL attempts are wrapped with console.error suppression so
      // Mermaid 11.x's internal "Syntax error in text" logs don't
      // pollute the browser console — we surface actionable errors
      // via the React state, not via raw console noise.
      //
      // Stage 1: Basic sanitization (invisible chars, deprecated keywords, etc.)
      // Stage 2: Progressive repair (force-quote labels, strip non-essentials)
      // Stage 3: Skip (consolidated — stage1 is already sanitized)
      // Stage 4: Raw code as last resort
      // Stage 5: Collect error for display

      const stage1 = sanitizeMermaidCode(code);

      // Helper: try rendering a single stage with console noise suppressed
      const tryRender = async (label: string, mermaidCode: string): Promise<string | null> => {
        if (cancelled) return null;
        const restoreConsole = suppressConsoleErrors();
        try {
          // Pre-validate: use mermaid.parse() (if available) to get a
          // structured error BEFORE attempting render — parse() is lighter
          // and gives better error messages in Mermaid 11.x.
          if (typeof mermaid.parse === 'function') {
            try {
              await mermaid.parse(mermaidCode);
            } catch (parseErr: any) {
              // parse() confirmed the error — but still try render()
              // because some parse errors are false positives
              const parseMsg = parseErr?.message || String(parseErr);
              if (parseMsg && !/syntax error/i.test(parseMsg)) {
                // Non-generic error — keep it for diagnostics
                console.debug(`Mermaid ${label} parse hint:`, parseMsg);
              }
            }
          }

          const { svg } = await mermaid.render(`${id}-${label}`, mermaidCode);
          // Sanity check: Mermaid 11.x may return an error SVG instead of
          // throwing — detect and treat as failure.
          if (svg && svg.includes('error-icon') && svg.includes('Syntax error')) {
            return null; // treat as render failure
          }
          return svg;
        } catch {
          return null; // expected — try next stage
        } finally {
          restoreConsole();
        }
      };

      // Track failures to decide on v11→v10 fallback
      let lastErrorMsg = '';

      // Stage 1: sanitized code
      if (!validateMermaidCode(stage1)) {
        const svg = await tryRender('s1', stage1);
        if (svg) {
          if (!cancelled) {
            setState({ svg, error: null });
            setLoading(false);
          }
          return;
        }
        failureCountRef.current++;
      }

      // Stage 2: Progressive repair
      const { code: repaired, repaired: wasRepaired } = repairMermaidCode(code);
      if (wasRepaired && repaired !== stage1) {
        const svg = await tryRender('s2', repaired);
        if (svg) {
          if (!cancelled) {
            setState({ svg, error: null });
            setLoading(false);
          }
          return;
        }
        failureCountRef.current++;
      }

      // Stage 3: Raw code (only if different from sanitized)
      if (code !== stage1) {
        const svg = await tryRender('s3', code);
        if (svg) {
          if (!cancelled) {
            setState({ svg, error: null });
            setLoading(false);
          }
          return;
        }
        failureCountRef.current++;
      }

      // Stage 4: All attempts failed — collect error message
      // Try one final render to capture the error text
      const restoreFinal = suppressConsoleErrors();
      try {
        await mermaid.render(`${id}-s4`, stage1);
      } catch (finalErr: any) {
        lastErrorMsg = finalErr?.message || String(finalErr);
      } finally {
        restoreFinal();
      }

      // Increment global failure counter for v11→v10 fallback decisions
      mermaidV11GlobalFailureCount += failureCountRef.current;

      if (!cancelled) {
        // Build a more helpful error message
        const friendlyError = lastErrorMsg
          ? formatMermaidError(lastErrorMsg, stage1)
          : 'Mermaid 渲染失败，请检查语法';
        setState({ svg: '', error: friendlyError });
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

  /**
   * Format raw Mermaid error messages into something human-readable.
   *
   * Mermaid 11.x's default error ("Syntax error in text") is unhelpful —
   * this function extracts line/column info and appends a snippet of the
   * offending code when possible.
   */
  function formatMermaidError(rawError: string, codeSnippet: string): string {
    // Extract line number from error message
    const lineMatch = rawError.match(/line\s*(\d+)/i);
    const colMatch = rawError.match(/(?:column|col)\s*(\d+)/i);
    const lineNum = lineMatch ? parseInt(lineMatch[1], 10) : null;
    const colNum = colMatch ? parseInt(colMatch[1], 10) : null;

    let friendly = rawError
      // Clean up verbose Mermaid internal prefixes
      .replace(/^Error:\s*/i, '')
      .replace(/^mermaid\s+version\s+\S+\s*/i, '')
      .trim();

    // If the error is the generic "Syntax error in text", add context
    if (/^syntax error in text$/i.test(friendly) && lineNum) {
      const lines = codeSnippet.split('\n');
      const offender = lines[lineNum - 1] || (lineNum - 2 >= 0 ? lines[lineNum - 2] : '');
      if (offender) {
        friendly = `第 ${lineNum} 行${colNum ? ` 第 ${colNum} 列` : ''}附近语法错误: "${offender.trim().slice(0, 80)}"`;
      } else {
        friendly = `第 ${lineNum} 行附近语法错误`;
      }
    }

    return friendly;
  }

  /** Map common Mermaid error messages to actionable fix hints (Chinese). */
  function getErrorHint(error: string): string | null {
    const msg = error.toLowerCase();
    if (msg.includes('syntax error') || msg.includes('parse error') || msg.includes('语法错误')) {
      return '标签文本含特殊字符（如冒号、括号、引号）时须用双引号包裹。节点ID仅限英文+数字+下划线';
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
    if (msg.includes('ambiguity') || msg.includes('ambiguous')) {
      return '图表定义存在歧义，尝试为每个节点标签添加双引号包裹';
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
            <details className="text-xs" open={true}>
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
