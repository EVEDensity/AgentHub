import { useCallback, useMemo, useState, type JSX } from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { Check, ChevronDown, ChevronUp, Code, Copy, Play } from 'lucide-react';

/** 支持的编程语言列表（可自行扩展） */
const LANGUAGES: { label: string; value: string }[] = [
  { label: 'Python', value: 'python' },
  { label: 'TypeScript', value: 'typescript' },
  { label: 'JavaScript', value: 'javascript' },
  { label: 'TSX', value: 'tsx' },
  { label: 'JSX', value: 'jsx' },
  { label: 'Bash', value: 'bash' },
  { label: 'SQL', value: 'sql' },
  { label: 'JSON', value: 'json' },
  { label: 'YAML', value: 'yaml' },
  { label: 'CSS', value: 'css' },
  { label: 'HTML', value: 'html' },
  { label: 'Markdown', value: 'markdown' },
  { label: 'Go', value: 'go' },
  { label: 'Rust', value: 'rust' },
  { label: 'Java', value: 'java' },
  { label: 'C++', value: 'cpp' },
  { label: 'C', value: 'c' },
  { label: 'Dockerfile', value: 'dockerfile' },
  { label: 'Plain Text', value: 'text' },
];

export interface InteractiveCodeBlockProps {
  /** 代码内容 */
  code: string;
  /** 代码语言（如 python、typescript、bash） */
  language: string;
  /** 代码块用途描述，显示在交互栏左侧 */
  description?: string;
  /** 代码片段序号，用于多个片段依次编号 */
  index?: number;
  /** 是否默认折叠代码区域 */
  defaultCollapsed?: boolean;
  /** 运行按钮回调，传入当前代码和语言；不传则隐藏运行按钮 */
  onRun?: (code: string, language: string) => void;
  /** 运行按钮是否处于加载中 */
  isRunning?: boolean;
  /** 顶部交互栏额外的 className */
  className?: string;
}

/**
 * InteractiveCodeBlock - 带交互栏的代码块组件
 *
 * 功能：
 * - 语言标签 + 下拉切换选择器（切换后语法高亮自动更新）
 * - 复制按钮（navigator.clipboard，2 秒后显示「已复制」）
 * - 运行按钮（可选，对接后端沙箱执行 API）
 * - 代码展开/收起按钮
 * - 语法高亮展示（Prism + oneDark 主题）
 * - 支持传入自定义描述文案与序号
 */
export default function InteractiveCodeBlock({
  code,
  language: initialLanguage,
  description,
  index,
  defaultCollapsed = false,
  onRun,
  isRunning = false,
  className = '',
}: InteractiveCodeBlockProps): JSX.Element {
  const [lang, setLang] = useState<string>(initialLanguage || 'text');
  const [copied, setCopied] = useState(false);
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const [langMenuOpen, setLangMenuOpen] = useState(false);

  /** 当前语言对应的显示标签 */
  const currentLangLabel = useMemo(
    () => LANGUAGES.find((l) => l.value === lang)?.label || lang.toUpperCase(),
    [lang],
  );

  /** 复制代码到剪贴板 */
  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // fallback：某些非安全上下文下 clipboard API 不可用
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

  /** 切换语言 */
  const handleLangChange = useCallback((newLang: string) => {
    setLang(newLang);
    setLangMenuOpen(false);
  }, []);

  /** 运行代码 */
  const handleRun = useCallback(() => {
    onRun?.(code, lang);
  }, [code, lang, onRun]);

  return (
    <div
      className={`my-4 overflow-hidden rounded-xl border border-warm-200 bg-[#282C34] shadow-sm transition-shadow hover:shadow-md ${className}`}
    >
      {/* ── 顶部交互栏 ── */}
      <div className="flex items-center justify-between gap-2 border-b border-white/10 bg-[#21252B] px-4 py-2.5">
        {/* 左侧：语言标签 */}
        <div className="flex min-w-0 items-center gap-3">

          {/* 语言下拉切换器 */}
          <div className="relative shrink-0">
            <button
              type="button"
              className="flex items-center gap-1.5 rounded-full border border-white/15 bg-white/8 px-2.5 py-1 text-[11px] font-medium text-warm-100 transition hover:bg-white/15"
              onClick={() => setLangMenuOpen((v) => !v)}
              title="切换语言高亮"
            >
              <Code className="h-3 w-3 text-warm-300" />
              {currentLangLabel}
              <ChevronDown className="h-3 w-3 text-warm-400" />
            </button>

            {langMenuOpen && (
              <>
                {/* 点击遮罩关闭 */}
                <div
                  className="fixed inset-0 z-10"
                  onClick={() => setLangMenuOpen(false)}
                />
                <div className="absolute left-0 top-full z-20 mt-1 max-h-48 w-36 overflow-auto rounded-lg border border-warm-200 bg-warm-100 py-1 shadow-modal">
                  {LANGUAGES.map((item) => (
                    <button
                      key={item.value}
                      type="button"
                      className={`block w-full px-3 py-1.5 text-left text-xs transition ${
                        lang === item.value
                          ? 'bg-primary-50 font-medium text-primary-600'
                          : 'text-warm-600 hover:bg-warm-50'
                      }`}
                      onClick={() => handleLangChange(item.value)}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>

        {/* 右侧：操作按钮组 */}
        <div className="flex shrink-0 items-center gap-1">
          {/* 运行按钮 */}
          {onRun && (
            <button
              type="button"
              className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-green-300 transition hover:bg-white/10 disabled:opacity-50"
              onClick={handleRun}
              disabled={isRunning}
              title="运行代码"
            >
              {isRunning ? (
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-green-300 border-t-transparent" />
              ) : (
                <Play className="h-3.5 w-3.5" />
              )}
              {isRunning ? '执行中…' : '运行'}
            </button>
          )}

          {/* 复制按钮 */}
          <button
            type="button"
            className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition hover:bg-white/10 ${
              copied ? 'text-green-300' : 'text-warm-300'
            }`}
            onClick={handleCopy}
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

          {/* 展开/收起按钮 */}
          <button
            type="button"
            className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-medium text-warm-300 transition hover:bg-white/10"
            onClick={() => setCollapsed((v) => !v)}
            title={collapsed ? '展开代码' : '收起代码'}
          >
            {collapsed ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronUp className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>

      {/* ── 代码区域 ── */}
      {!collapsed && (
        <div className="overflow-auto">
          <SyntaxHighlighter
            language={lang}
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
            showLineNumbers={code.split('\n').length > 3}
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

      {/* 收起状态下显示占位提示 */}
      {collapsed && (
        <div
          className="flex cursor-pointer items-center gap-2 px-4 py-3 text-xs text-warm-400 transition hover:bg-white/[0.03]"
          onClick={() => setCollapsed(false)}
        >
          <Code className="h-3.5 w-3.5" />
          代码已折叠，点击展开 ({code.split('\n').length} 行)
        </div>
      )}
    </div>
  );
}
