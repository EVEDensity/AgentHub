import { useCallback, useMemo, useRef, useState, type JSX } from 'react';
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
  const [iframeHeight, setIframeHeight] = useState(600);

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

  /** iframe onLoad: 自适应高度 */
  const handleIframeLoad = useCallback(() => {
    try {
      if (iframeRef.current?.contentDocument?.body) {
        const h = iframeRef.current.contentDocument.body.scrollHeight;
        if (h > 100) setIframeHeight(Math.min(h + 40, 4000));
      }
    } catch {
      // cross-origin 限制下忽略
    }
  }, []);

  const lineCount = useMemo(() => code.split('\n').length, [code]);

  return (
    <div
      className={`my-4 overflow-hidden rounded-xl border border-warm-200 bg-white shadow-sm transition-shadow hover:shadow-md ${
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
                ? 'bg-white text-warm-800 shadow-sm ring-1 ring-warm-200'
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
                ? 'bg-white text-warm-800 shadow-sm ring-1 ring-warm-200'
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
      <div className={isFullscreen ? 'flex-1 min-h-0 overflow-hidden' : ''}>
        {/* 代码视图 */}
        {activeTab === 'code' && (
          <div className="overflow-auto" style={{ maxHeight: isFullscreen ? undefined : 600 }}>
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
            className="bg-[#f7f8fa]"
            style={{
              height: isFullscreen ? '100%' : Math.min(iframeHeight, 800),
              minHeight: isFullscreen ? undefined : 400,
              overflow: 'auto',
            }}
          >
            {/* 预览容器：白色圆角卡片 */}
            <div className="p-4">
              <div className="overflow-hidden rounded-xl bg-white shadow-md">
                <iframe
                  ref={iframeRef}
                  srcDoc={code}
                  sandbox="allow-scripts allow-same-origin"
                  title={`HTML Preview ${index}`}
                  className="w-full border-0"
                  style={{
                    height: isFullscreen ? '100%' : iframeHeight,
                    minHeight: isFullscreen ? '100%' : 400,
                    display: 'block',
                  }}
                  onLoad={handleIframeLoad}
                />
              </div>
            </div>

            {/* 提示信息 */}
            {!isFullDocument && (
              <div className="mx-4 mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-700">
                ⚠️ 此 HTML 片段可能不是完整文档（缺少 DOCTYPE/html/head/body 结构），预览效果可能不完整。
                建议生成完整的单文件 HTML（含 DOCTYPE + head 内嵌 CSS + body）。
              </div>
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
