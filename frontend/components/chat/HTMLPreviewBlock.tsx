import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { Check, Code, Copy, Eye, Maximize2, Minimize2 } from 'lucide-react';

export interface HTMLPreviewBlockProps {
  /** 完整 HTML 源码 */
  code: string;
  /** 代码块序号 */
  index?: number;
  /** 是否默认显示预览（否则显示代码） */
  defaultTab?: 'code' | 'preview';
  /** 额外的 className */
  className?: string;
}

/**
 * 注入到 srcDoc 头部的一段 CSS, 用来中和"内容用 100vh 把 body 撑成视口高"的问题
 * 关键: 把 viewport 单位强制替换为固定像素, 避免 iframe 高度 ↔ body 高度 反馈循环
 */
const IFRAME_RESET_CSS = `
<style id="__agenthub_iframe_reset__">
  /* 把 viewport 单位全部重写为固定像素, 避免和 iframe 高度形成自反馈
     (比如: .hero { min-height: 100vh } -> .hero { min-height: 600px }) */
  html { --__vh__: 600px; }
  *, *::before, *::after {
    --__vh-fallback__: 600px;
  }
  /* 用 calc + CSS 变量把 100vh/100svh/100lvh/100dvh 都改写成固定值
     注意: 这只对 CSS 变量声明和直接属性生效, 不影响 [style*="100vh"] 内联 */
  html, body { height: auto !important; min-height: 0 !important; }
  body { margin: 0; overflow: visible !important; }
  /* 兜底: 把所有 min-height: 100vh 类的选择器降级为 auto, 防止级联 */
  [class*="hero"], [class*="banner"], [class*="section-full"], [class*="fullscreen"] {
    min-height: 480px !important;
  }
</style>
`;

/**
 * 改写 src 字符串中的 viewport 单位 (100vh, 100svh 等) -> 固定像素
 * 因为 [style="min-height: 100vh"] 这种内联样式无法用 CSS 覆盖, 必须在源码层改写
 */
function neutralizeViewportUnits(code: string): string {
  return code
    // 100vh / 100svh / 100lvh / 100dvh / 100vw 等 -> 600px / 800px
    .replace(/(\d*\.?\d+)\s*vh\b/gi, '600px')
    .replace(/(\d*\.?\d+)\s*svh\b/gi, '600px')
    .replace(/(\d*\.?\d+)\s*lvh\b/gi, '600px')
    .replace(/(\d*\.?\d+)\s*dvh\b/gi, '600px')
    .replace(/(\d*\.?\d+)\s*vw\b/gi, '800px')
    // calc(...vh ...) -> calc(600px ...)
    .replace(/calc\(([^)]*?)(\d*\.?\d+)\s*vh([^)]*?)\)/gi, 'calc($1600px$3)')
    .replace(/calc\(([^)]*?)(\d*\.?\d+)\s*vw([^)]*?)\)/gi, 'calc($1800px$3)');
}

function buildSrcDoc(code: string): string {
  const rewritten = neutralizeViewportUnits(code);
  const lower = rewritten.toLowerCase();
  if (lower.includes('<head')) {
    return rewritten.replace(/<head([^>]*)>/i, (m) => `${m}\n${IFRAME_RESET_CSS}`);
  }
  if (lower.includes('<html')) {
    return rewritten.replace(/<html([^>]*)>/i, (m) => `${m}\n<head>${IFRAME_RESET_CSS}</head>`);
  }
  return `<head>${IFRAME_RESET_CSS}</head>\n${rewritten}`;
}

/**
 * HTMLPreviewBlock - 双区块 HTML 展示组件
 *
 * 在对话消息中渲染 HTML 代码块时，自动提供「代码 / 预览」双标签切换：
 * - 代码视图：完整 HTML 源码 + 语法高亮 + 复制按钮
 * - 预览视图：sandbox iframe 即时渲染，白色圆角卡片容器
 */
export default function HTMLPreviewBlock({
  code,
  index = 0,
  defaultTab = 'preview',
  className = '',
}: HTMLPreviewBlockProps): JSX.Element {
  const [activeTab, setActiveTab] = useState<'code' | 'preview'>(defaultTab);
  const [copied, setCopied] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  // iframe 的实际高度 = 跟随内容 + 上下 padding, 不再写死 600
  const [iframeHeight, setIframeHeight] = useState(600);
  const [contentKey, setContentKey] = useState(0); // 切到预览时强制重挂载, 避免切换 tab 后旧高度残留

  const srcDoc = useMemo(() => buildSrcDoc(code), [code]);

  /** 测量 iframe 内容真实高度 (单次快照, 不订阅 ResizeObserver 以避免自反馈) */
  const measureIframe = useCallback(() => {
    const iframe = iframeRef.current;
    if (!iframe) return;
    const doc = iframe.contentDocument;
    if (!doc) return;
    const html = doc.documentElement;
    const body = doc.body;
    if (!html || !body) return;
    const h = Math.max(
      html.scrollHeight,
      body.scrollHeight,
      html.offsetHeight,
      body.offsetHeight
    );
    if (h > 50) {
      // 留 16px padding, 硬上限 2400px (防止反馈循环或超长页面)
      // 超出 2400 的部分由 iframe 自身的内部滚动条处理
      const next = Math.min(h + 16, 2400);
      setIframeHeight((prev) => (Math.abs(prev - next) > 4 ? next : prev));
    }
  }, []);

  /** 切到预览时强制重挂 iframe (srcDoc 重新执行) */
  useEffect(() => {
    if (activeTab === 'preview') {
      setContentKey((k) => k + 1);
    }
  }, [activeTab]);

  /** 检测 HTML 内容是否完整（含 DOCTYPE/html/head/body） */
  const isFullDocument = useMemo(() => {
    const lower = code.trim().toLowerCase();
    return lower.includes('<!doctype') || lower.includes('<html');
  }, [code]);

  /** 复制代码到剪贴板 */
  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      const textarea = document.createElement('textarea');
      textarea.value = code;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }, [code]);

  /** 下载 HTML 文件 */
  const handleDownload = useCallback(() => {
    const blob = new Blob([code], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `page-${index || Date.now()}.html`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [code, index]);

  /**
   * iframe onLoad: 多时机快照测量 (0/300ms/1s/2.5s/5s), 捕获图片/字体/异步内容
   * 关键: 不订阅 ResizeObserver, 因为 .hero { min-height: 100vh } 会在
   * "iframe 高度变化 -> 100vh 变化 -> body 变化" 之间形成自反馈循环
   *
   * FIX: React onLoad 不接受 cleanup 返回值。改用 ref 追踪 timer IDs，
   * useEffect cleanup 在组件卸载或 contentKey 变化时清除所有 pending timers。
   */
  const measureTimersRef = useRef<number[]>([]);

  useEffect(() => {
    return () => {
      measureTimersRef.current.forEach((t) => clearTimeout(t));
      measureTimersRef.current = [];
    };
  }, [contentKey]);

  const handleIframeLoad = useCallback(() => {
    measureIframe();
    measureTimersRef.current.forEach((t) => clearTimeout(t));
    measureTimersRef.current = [];
    [300, 1000, 2500, 5000].forEach((t) => {
      measureTimersRef.current.push(window.setTimeout(measureIframe, t));
    });
  }, [measureIframe, contentKey]);

  const lineCount = useMemo(() => code.split('\n').length, [code]);

  return (
    <div
      className={`my-4 overflow-hidden rounded-xl border border-warm-200 bg-warm-100 shadow-sm transition-shadow hover:shadow-md ${
        isFullscreen ? 'fixed inset-4 z-50 flex flex-col' : ''
      } ${className}`}
      style={isFullscreen ? { margin: 0 } : undefined}
    >
      {/* ── 顶部标签栏 ── */}
      <div className="flex items-center justify-between border-b border-warm-150 bg-warm-50/80 px-3 py-1.5">
        {/* 左侧：代码/预览 标签切换 */}
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={() => setActiveTab('code')}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-all ${
              activeTab === 'code'
                ? 'bg-warm-100 text-warm-800 shadow-sm ring-1 ring-warm-200'
                : 'text-warm-500 hover:text-warm-700 hover:bg-warm-100'
            }`}
          >
            <Code className="h-3.5 w-3.5" />
            代码
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('preview')}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-all ${
              activeTab === 'preview'
                ? 'bg-warm-100 text-warm-800 shadow-sm ring-1 ring-warm-200'
                : 'text-warm-500 hover:text-warm-700 hover:bg-warm-100'
            }`}
          >
            <Eye className="h-3.5 w-3.5" />
            预览
          </button>
        </div>

        {/* 右侧：操作按钮组 */}
        <div className="flex items-center gap-0.5">
          {/* 下载按钮 */}
          <button
            type="button"
            onClick={handleDownload}
            className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium text-warm-500 transition hover:bg-warm-100 hover:text-warm-700"
            title="下载 HTML 文件"
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
            下载
          </button>

          {/* 复制按钮 */}
          <button
            type="button"
            onClick={handleCopy}
            className={`flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium transition ${
              copied
                ? 'text-green-600 bg-green-50'
                : 'text-warm-500 hover:bg-warm-100 hover:text-warm-700'
            }`}
            title="复制代码"
          >
            {copied ? (
              <>
                <Check className="h-3.5 w-3.5" />
                已复制
              </>
            ) : (
              <>
                <Copy className="h-3.5 w-3.5" />
                复制
              </>
            )}
          </button>

          {/* 全屏切换 */}
          <button
            type="button"
            onClick={() => setIsFullscreen((v) => !v)}
            className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-medium text-warm-500 transition hover:bg-warm-100 hover:text-warm-700"
            title={isFullscreen ? '退出全屏' : '全屏预览'}
          >
            {isFullscreen ? (
              <Minimize2 className="h-3.5 w-3.5" />
            ) : (
              <Maximize2 className="h-3.5 w-3.5" />
            )}
          </button>
        </div>
      </div>

      {/* ── 内容区域 ── */}
      <div className={isFullscreen ? 'flex-1 min-h-0 overflow-hidden flex flex-col' : ''}>
        {/* 代码视图 */}
        {activeTab === 'code' && (
          <div className={isFullscreen ? 'flex-1 min-h-0 overflow-auto' : 'overflow-auto'} style={{ maxHeight: isFullscreen ? undefined : 600 }}>
            <SyntaxHighlighter
              language="html"
              style={oneDark}
              customStyle={{
                margin: 0,
                padding: '1.25rem 1rem',
                background: '#282C34',
                borderRadius: 0,
                fontSize: '13px',
                lineHeight: '1.65',
                // 全屏时代码区域至少撑满容器
                ...(isFullscreen ? { minHeight: '100%' } : {}),
              }}
              codeTagProps={{
                style: {
                  fontFamily:
                    '"JetBrains Mono", "Fira Code", "Cascadia Code", Consolas, monospace',
                },
              }}
              showLineNumbers={lineCount > 10}
              lineNumberStyle={{
                minWidth: '2.5em',
                paddingRight: '1em',
                color: '#5C6370',
                userSelect: 'none',
              }}
            >
              {code}
            </SyntaxHighlighter>
          </div>
        )}

        {/* 预览视图 */}
        {activeTab === 'preview' && (
          <div
            className={`bg-[#f7f8fa] ${isFullscreen ? 'flex-1 min-h-0 flex flex-col' : ''}`}
          >
            {/* 非全屏: 白色卡片 + 自动高度; 全屏: flex-1 撑满 + 内部滚动 */}
            {isFullscreen ? (
              <>
                <div className="flex-1 min-h-0 p-4 pb-2">
                  <div className="h-full flex flex-col overflow-hidden rounded-xl bg-warm-100 shadow-md">
                    <iframe
                      key={contentKey}
                      ref={iframeRef}
                      srcDoc={srcDoc}
                      sandbox="allow-scripts allow-same-origin"
                      title={`HTML Preview ${index}`}
                      className="w-full flex-1 border-0"
                      style={{ minHeight: 300, display: 'block' }}
                      onLoad={handleIframeLoad}
                    />
                  </div>
                </div>
                {!isFullDocument && (
                  <div className="mx-4 mb-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-700 shrink-0">
                    ⚠️ 此 HTML 片段可能不是完整文档（缺少 DOCTYPE/html/head/body 结构），预览效果可能不完整。
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="p-4">
                  <div className="overflow-hidden rounded-xl bg-warm-100 shadow-md">
                    <iframe
                      key={contentKey}
                      ref={iframeRef}
                      srcDoc={srcDoc}
                      sandbox="allow-scripts allow-same-origin"
                      title={`HTML Preview ${index}`}
                      className="w-full border-0 block"
                      style={{
                        height: iframeHeight,
                        minHeight: 300,
                        display: 'block',
                      }}
                      onLoad={handleIframeLoad}
                    />
                  </div>
                </div>
                {!isFullDocument && (
                  <div className="mx-4 mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-700">
                    ⚠️ 此 HTML 片段可能不是完整文档（缺少 DOCTYPE/html/head/body 结构），预览效果可能不完整。
                    建议生成完整的单文件 HTML（含 DOCTYPE + head 内嵌 CSS + body）。
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* ── 底部元信息栏 ── */}
      <div className="flex items-center justify-between border-t border-warm-150 bg-warm-50/60 px-4 py-1.5">
        <span className="text-[11px] text-warm-400">
          HTML · {lineCount} 行 · {(code.length / 1024).toFixed(1)} KB
        </span>
        <span className="text-[11px] text-warm-400">
          {isFullDocument ? '完整文档' : 'HTML 片段'} · 单文件可独立运行
        </span>
      </div>

      {/* 全屏模式关闭层 */}
      {isFullscreen && (
        <div
          className="fixed inset-0 -z-10 bg-black/50"
          onClick={() => setIsFullscreen(false)}
        />
      )}
    </div>
  );
}
