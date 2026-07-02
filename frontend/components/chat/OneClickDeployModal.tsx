'use client';

import { memo, useState, useCallback, useEffect, useRef, type JSX } from 'react';
import {
  X, Globe, Server, Code, Download, Rocket,
  Copy, Check, ExternalLink, RefreshCw, FileText,
  Key, Link, Terminal, Package, Monitor, FolderGit2
} from 'lucide-react';

// ── Types ───────────────────────────────────────────────────────────

type DeployTarget = 'webapp' | 'api' | 'embed' | 'package';

interface DeployOption {
  id: DeployTarget;
  label: string;
  desc: string;
  icon: typeof Globe;
  actionLabel: string;
}

const DEPLOY_OPTIONS: DeployOption[] = [
  {
    id: 'webapp',
    label: '在线网页应用',
    desc: '部署为独立网页链接，可直接分享访问',
    icon: Monitor,
    actionLabel: '生成链接',
  },
  {
    id: 'api',
    label: 'API 接口服务',
    desc: '生成标准调用 API、接口地址与密钥，支持第三方系统对接',
    icon: Server,
    actionLabel: '获取 API',
  },
  {
    id: 'embed',
    label: '嵌入组件',
    desc: '生成 iframe / 代码片段，可嵌入网站、文档、平台页面',
    icon: Code,
    actionLabel: '复制代码',
  },
  {
    id: 'package',
    label: '本地部署包',
    desc: '下载完整项目包，支持本地服务器、虚拟机离线部署',
    icon: Package,
    actionLabel: '下载安装包',
  },
];

// ── Mock projects ──────────────────────────────────────────────────

const MOCK_PROJECTS = [
  { id: 'proj-1', name: 'agenthub-frontend', defaultBranch: 'main', defaultDomain: 'agenthub' },
  { id: 'proj-2', name: 'agenthub-blog', defaultBranch: 'main', defaultDomain: 'blog' },
] as const;

// ── Validation ────────────────────────────────────────────────────────────

const BRANCH_REGEX = /^[a-zA-Z0-9._/-]+$/;
const DOMAIN_REGEX = /^[a-z0-9-]+$/;

function validateBranch(v: string): string | null {
  if (!v.trim()) return '请输入分支名称';
  if (v.startsWith('/')) return '分支不能以 / 开头';
  if (/\s/.test(v)) return '分支不能包含空格';
  if (!BRANCH_REGEX.test(v)) return '分支包含非法字符';
  return null;
}

function validateDomain(v: string): string | null {
  if (!v.trim()) return '请输入域名';
  if (!DOMAIN_REGEX.test(v)) return '域名只能包含小写字母、数字和连字符';
  return null;
}

// ── Mock generated results per target ──────────────────────────────

function generateMockResult(target: DeployTarget, projectId: string, domain: string) {
  const slug = projectId.replace('proj-', '');
  const subdomain = domain || 'preview';
  switch (target) {
    case 'webapp':
      return {
        url: `https://${subdomain}.agenthub.app/deploy/${slug}-` + Math.random().toString(36).slice(2, 8),
        status: 'running',
        expiresAt: new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString(),
        config: { framework: 'Next.js', region: 'ap-southeast-1', runtime: 'Node.js 20' },
      };
    case 'api':
      return {
        endpoint: `https://api.${subdomain}.agenthub.app/v1/chat/` + Math.random().toString(36).slice(2, 8),
        apiKey: 'ah-' + Array.from({ length: 32 }, () => Math.random().toString(36)[2]).join(''),
        method: 'POST',
        docsUrl: 'https://docs.agenthub.app/api-reference',
        config: { rateLimit: '100 req/min', model: 'claude-opus-4-8', authType: 'Bearer Token' },
      };
    case 'embed':
      return {
        iframe: `<iframe
  src="https://${subdomain}.agenthub.app/embed/${Math.random().toString(36).slice(2, 8)}"
  width="100%"
  height="600"
  frameborder="0"
  style="border-radius:12px;border:1px solid #e5e7eb;"
  title="AgentHub Chat"
></iframe>`,
        scriptTag: `<script
  src="https://cdn.agenthub.app/widget.js"
  data-agent-id="${Math.random().toString(36).slice(2, 8)}"
  data-theme="warm"
  async
></script>`,
        config: { width: '100%', height: '600px', theme: 'auto' },
      };
    case 'package':
      return {
        packageName: `agenthub-${slug}-deploy-` + Math.random().toString(36).slice(2, 8) + '.tar.gz',
        size: Math.floor(Math.random() * 50 + 10) + 'MB',
        nodeVersion: '>=20.0.0',
        startCommand: 'npm install && npm run build && npm start',
        envVars: ['AGENTHUB_API_KEY', 'DATABASE_URL', 'REDIS_URL'],
        config: { bundler: 'esbuild', target: 'node20', includeSource: true },
      };
  }
}

// ── Component ──────────────────────────────────────────────────────

interface OneClickDeployModalProps {
  open: boolean;
  sessionId: string;
  sessionName: string;
  onClose: () => void;
}

const OneClickDeployModal = memo(function OneClickDeployModal({
  open,
  sessionId,
  sessionName,
  onClose,
}: OneClickDeployModalProps): JSX.Element | null {
  const [selectedTarget, setSelectedTarget] = useState<DeployTarget>('webapp');
  const [generating, setGenerating] = useState(false);
  const [generated, setGenerated] = useState<Record<string, any> | null>(null);
  const [copiedField, setCopiedField] = useState<string | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // ── Project / Branch / Domain state ────────────────────────────
  const [projectId, setProjectId] = useState('proj-1');
  const [branch, setBranch] = useState('main');
  const [domain, setDomain] = useState('');
  const [branchTouched, setBranchTouched] = useState(false);
  const [domainTouched, setDomainTouched] = useState(false);

  const branchError = branchTouched ? validateBranch(branch) : null;
  const domainError = domainTouched ? validateDomain(domain) : null;
  const configValid = projectId !== '' && branch.trim() !== '' && branchError === null && domain.trim() !== '' && domainError === null;

  const overlayRef = useRef<HTMLDivElement>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Sync branch & domain when project changes ──────────────────
  useEffect(() => {
    const project = MOCK_PROJECTS.find((p) => p.id === projectId);
    if (project) {
      setBranch(project.defaultBranch);
      setDomain(project.defaultDomain);
      setBranchTouched(false);
      setDomainTouched(false);
    }
  }, [projectId]);

  // ── Reset state when modal opens ────────────────────────────────
  useEffect(() => {
    if (open) {
      setSelectedTarget('webapp');
      setGenerated(null);
      setGenerating(false);
      setCopiedField(null);
      setToastMessage(null);
      setProjectId('proj-1');
      setBranch('main');
      setDomain('agenthub');
      setBranchTouched(false);
      setDomainTouched(false);
    }
  }, [open]);

  // ── Cleanup timers on unmount ───────────────────────────────────
  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    };
  }, []);

  // ── Escape key ──────────────────────────────────────────────────
  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onClose]);

  // ── Overlay click ───────────────────────────────────────────────
  function handleOverlayClick(e: React.MouseEvent) {
    if (e.target === overlayRef.current) onClose();
  }

  // ── Copy helper ─────────────────────────────────────────────────
  const copyToClipboard = useCallback(async (text: string, field: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedField(field);
      setToastMessage('复制成功');
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopiedField(null), 2000);
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
      toastTimerRef.current = setTimeout(() => setToastMessage(null), 2000);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      setCopiedField(field);
      setToastMessage('复制成功');
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopiedField(null), 2000);
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
      toastTimerRef.current = setTimeout(() => setToastMessage(null), 2000);
    }
  }, []);

  // ── Handle target selection ─────────────────────────────────────
  function handleSelectTarget(target: DeployTarget) {
    if (generating) return;
    setSelectedTarget(target);
    setGenerated(null);
  }

  // ── Handle main action (generate / copy / download) ─────────────
  function handleMainAction() {
    if (generating) return;

    const option = DEPLOY_OPTIONS.find((o) => o.id === selectedTarget);
    if (!option) return;

    // If already generated, perform the copy/download action
    if (generated) {
      switch (selectedTarget) {
        case 'webapp':
          copyToClipboard(generated.url, 'url');
          break;
        case 'api':
          copyToClipboard(generated.apiKey, 'apiKey');
          break;
        case 'embed':
          copyToClipboard(generated.iframe, 'iframe');
          break;
        case 'package':
          setToastMessage('下载开始');
          if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
          toastTimerRef.current = setTimeout(() => setToastMessage(null), 2500);
          const a = document.createElement('a');
          a.href = '#';
          a.download = generated.packageName;
          a.click();
          break;
      }
      return;
    }

    // Simulate generation
    setGenerating(true);
    setTimeout(() => {
      const result = generateMockResult(selectedTarget, projectId, domain);
      setGenerated(result);
      setGenerating(false);

      // Auto-copy after generation for API and embed
      if (selectedTarget === 'api') {
        setTimeout(() => copyToClipboard(String((result as any).apiKey ?? ''), 'apiKey'), 300);
      } else if (selectedTarget === 'embed') {
        setTimeout(() => copyToClipboard(String((result as any).iframe ?? ''), 'iframe'), 300);
      }
    }, 1500 + Math.random() * 1000);
  }

  // ── Handle reset ────────────────────────────────────────────────
  function handleReset() {
    setSelectedTarget('webapp');
    setGenerated(null);
    setCopiedField(null);
    setProjectId('proj-1');
    setBranch('main');
    setDomain('agenthub');
    setBranchTouched(false);
    setDomainTouched(false);
  }

  // ── Get current option ──────────────────────────────────────────
  const currentOption = DEPLOY_OPTIONS.find((o) => o.id === selectedTarget)!;
  const IconComponent = currentOption.icon;

  if (!open) return null;

  return (
    <>
      {/* ── Overlay ────────────────────────────────────────────────── */}
      <div
        ref={overlayRef}
        onClick={handleOverlayClick}
        className="fixed inset-0 z-[110] flex items-center justify-center bg-black/40 backdrop-blur-sm p-4 animate-in fade-in duration-150"
      >
        {/* ── Modal container ──────────────────────────────────────── */}
        <div
          className="w-full max-w-[560px] bg-white rounded-2xl shadow-modal border border-warm-150 overflow-hidden animate-in zoom-in-95 duration-150 flex flex-col max-h-[85vh]"
          role="dialog"
          aria-modal="true"
          aria-label="一键部署"
        >
          {/* ── Header ─────────────────────────────────────────────── */}
          <div className="flex items-start justify-between px-6 pt-5 pb-4 border-b border-warm-100 shrink-0">
            <div className="flex items-start gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-500 shadow-sm shrink-0">
                <Rocket className="h-5 w-5 text-white" />
              </div>
              <div>
                <h2 className="text-lg font-bold text-warm-800">一键部署</h2>
                <p className="text-xs text-warm-400 mt-0.5 leading-relaxed">
                  快速将当前对话 / 智能体部署为在线服务、API 接口或独立应用
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="flex h-8 w-8 items-center justify-center rounded-full text-warm-400 hover:bg-warm-100 hover:text-warm-600 transition-colors shrink-0"
              aria-label="关闭"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* ── Body: Deploy options ────────────────────────────────── */}
          <div className="px-6 py-4 space-y-4 overflow-y-auto flex-1">
            {/* Session context */}
            <div className="rounded-lg bg-warm-50 border border-warm-100 px-3 py-2 flex items-center gap-2 text-xs text-warm-500">
              <FileText className="h-3.5 w-3.5 shrink-0 text-warm-400" />
              <span className="truncate">部署会话：{sessionName || sessionId}</span>
            </div>

            {/* ── Project / Branch / Domain configuration ──────────── */}
            <fieldset>
              <legend className="text-sm font-semibold text-warm-700 mb-2.5">
                部署配置
              </legend>
              <div className="space-y-3">
                {/* Project select */}
                <div>
                  <label htmlFor="ocd-project" className="block text-xs font-medium text-warm-500 mb-1">
                    项目 <span className="text-danger-500">*</span>
                  </label>
                  <select
                    id="ocd-project"
                    value={projectId}
                    onChange={(e) => setProjectId(e.target.value)}
                    className="w-full rounded-lg border border-warm-200 bg-white px-3 py-2.5 text-sm text-warm-800 focus:outline-none focus:ring-2 focus:ring-primary-400/60 focus:border-primary-400 transition-shadow appearance-none bg-[image:url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2216%22%20height%3D%2216%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%23999%22%20stroke-width%3D%222%22%3E%3Cpath%20d%3D%22m6%209%206%206%206-6%22%2F%3E%3C%2Fsvg%3E')] bg-[length:16px] bg-[right_8px_center] bg-no-repeat pr-8"
                    aria-label="选择部署项目"
                  >
                    <option value="" disabled>
                      请选择项目...
                    </option>
                    {MOCK_PROJECTS.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Branch + Domain (2-col) */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {/* Branch */}
                  <div>
                    <label htmlFor="ocd-branch" className="block text-xs font-medium text-warm-500 mb-1">
                      分支 <span className="text-danger-500">*</span>
                    </label>
                    <div className="relative">
                      <FolderGit2 className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-warm-350 pointer-events-none" />
                      <input
                        id="ocd-branch"
                        type="text"
                        value={branch}
                        onChange={(e) => {
                          setBranch(e.target.value);
                          setBranchTouched(true);
                        }}
                        onBlur={() => setBranchTouched(true)}
                        placeholder="main"
                        aria-label="Git 分支名称"
                        aria-invalid={branchError !== null}
                        className={`w-full rounded-lg border pl-8 pr-3 py-2.5 text-sm font-mono text-warm-800 placeholder:text-warm-350 focus:outline-none focus:ring-2 focus:ring-primary-400/60 transition-shadow ${
                          branchError !== null
                            ? 'border-danger-500 focus:border-danger-500 focus:ring-danger-500/40'
                            : 'border-warm-200 focus:border-primary-400'
                        }`}
                        autoComplete="off"
                        spellCheck={false}
                      />
                    </div>
                    {branchError !== null && (
                      <p className="mt-1 text-xs text-danger-500" role="alert">
                        {branchError}
                      </p>
                    )}
                  </div>

                  {/* Domain */}
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <label htmlFor="ocd-domain" className="text-xs font-medium text-warm-500">
                        域名 <span className="text-danger-500">*</span>
                      </label>
                      <a
                        href="https://www.volcengine.com/activity/domain-coze"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-[11px] text-primary-500 hover:text-primary-600 transition-colors"
                      >
                        <ExternalLink className="h-3 w-3" />
                        购买域名
                      </a>
                    </div>
                    <div className="relative">
                      <Globe className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-warm-350 pointer-events-none" />
                      <input
                        id="ocd-domain"
                        type="text"
                        value={domain}
                        onChange={(e) => {
                          setDomain(e.target.value.toLowerCase());
                          setDomainTouched(true);
                        }}
                        onBlur={() => setDomainTouched(true)}
                        placeholder="myapp"
                        aria-label="部署域名"
                        aria-invalid={domainError !== null}
                        className={`w-full rounded-lg border pl-8 pr-3 py-2.5 text-sm font-mono text-warm-800 placeholder:text-warm-350 focus:outline-none focus:ring-2 focus:ring-primary-400/60 transition-shadow ${
                          domainError !== null
                            ? 'border-danger-500 focus:border-danger-500 focus:ring-danger-500/40'
                            : 'border-warm-200 focus:border-primary-400'
                        }`}
                        autoComplete="off"
                        spellCheck={false}
                      />
                    </div>
                    {domainError !== null && (
                      <p className="mt-1 text-xs text-danger-500" role="alert">
                        {domainError}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            </fieldset>

            {/* ── Deploy target cards ──────────────────────────────── */}
            <fieldset>
              <legend className="text-sm font-semibold text-warm-700 mb-2.5">
                选择部署方式
              </legend>
              <div className="grid grid-cols-2 gap-2">
                {DEPLOY_OPTIONS.map((opt) => {
                  const OptIcon = opt.icon;
                  const isActive = selectedTarget === opt.id;
                  return (
                    <button
                      key={opt.id}
                      type="button"
                      onClick={() => handleSelectTarget(opt.id)}
                      disabled={generating}
                      className={`flex flex-col items-start gap-1.5 rounded-xl border-2 px-4 py-3.5 text-left transition-all disabled:opacity-60 ${
                        isActive
                          ? 'border-primary-400 bg-primary-50/60 shadow-sm'
                          : 'border-warm-150 bg-white hover:border-warm-250 hover:bg-warm-50'
                      }`}
                    >
                      <div className="flex items-center gap-2 w-full">
                        <div
                          className={`flex h-8 w-8 items-center justify-center rounded-lg shrink-0 transition-colors ${
                            isActive ? 'bg-primary-100 text-primary-600' : 'bg-warm-100 text-warm-500'
                          }`}
                        >
                          <OptIcon className="h-4 w-4" />
                        </div>
                        <span
                          className={`text-sm font-semibold ${
                            isActive ? 'text-primary-700' : 'text-warm-700'
                          }`}
                        >
                          {opt.label}
                        </span>
                        {isActive && (
                          <span className="ml-auto flex h-5 w-5 items-center justify-center rounded-full bg-primary-500 text-white">
                            <Check className="h-3 w-3" />
                          </span>
                        )}
                      </div>
                      <span className="text-[11px] text-warm-400 leading-tight">
                        {opt.desc}
                      </span>
                    </button>
                  );
                })}
              </div>
            </fieldset>

            {/* ── Configuration / Result panel ──────────────────────── */}
            <div className="rounded-xl border border-warm-150 bg-warm-50/50 overflow-hidden">
              {/* Panel header */}
              <div className="flex items-center gap-2 px-4 py-2.5 border-b border-warm-100 bg-white/60">
                <IconComponent className="h-4 w-4 text-primary-500" />
                <span className="text-sm font-semibold text-warm-700">
                  {currentOption.label} 配置
                </span>
                {generating && (
                  <span className="ml-auto inline-flex items-center gap-1 text-xs text-primary-500">
                    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
                    生成中...
                  </span>
                )}
                {generated && !generating && (
                  <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-success-600">
                    <Check className="h-3 w-3" />
                    已生成
                  </span>
                )}
              </div>

              {/* Panel content */}
              <div className="px-4 py-3 space-y-3">
                {!generated && !generating && (
                  <div className="text-xs text-warm-400 text-center py-4">
                    选择部署方式后点击下方按钮生成
                  </div>
                )}

                {generating && (
                  <div className="flex flex-col items-center gap-3 py-6">
                    <div className="relative">
                      <div className="h-12 w-12 animate-spin rounded-full border-3 border-primary-100 border-t-primary-500" />
                      <Rocket className="absolute inset-0 m-auto h-5 w-5 text-primary-500" />
                    </div>
                    <span className="text-sm text-warm-500 font-medium">
                      正在生成部署配置...
                    </span>
                    <span className="text-xs text-warm-400">
                      请稍候，预计需要几秒钟
                    </span>
                  </div>
                )}

                {/* ── WebApp result ──────────────────────────────── */}
                {generated && selectedTarget === 'webapp' && (
                  <>
                    <div>
                      <label className="block text-[11px] font-semibold text-warm-500 uppercase tracking-wide mb-1">
                        部署地址
                      </label>
                      <div className="flex items-center gap-2">
                        <div className="flex items-center gap-1.5 flex-1 rounded-lg border border-warm-200 bg-white px-3 py-2 min-w-0">
                          <Link className="h-3.5 w-3.5 text-warm-400 shrink-0" />
                          <code className="text-xs font-mono text-primary-600 truncate">
                            {generated.url}
                          </code>
                        </div>
                        <button
                          type="button"
                          onClick={() => copyToClipboard(generated.url, 'url')}
                          className={`flex h-8 w-8 items-center justify-center rounded-lg border transition-all shrink-0 ${
                            copiedField === 'url'
                              ? 'border-success-300 bg-success-50 text-success-600'
                              : 'border-warm-200 bg-white text-warm-500 hover:border-primary-300 hover:text-primary-600'
                          }`}
                          title="复制链接"
                        >
                          {copiedField === 'url' ? (
                            <Check className="h-3.5 w-3.5" />
                          ) : (
                            <Copy className="h-3.5 w-3.5" />
                          )}
                        </button>
                      </div>
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      {Object.entries(generated.config).map(([key, val]) => (
                        <div key={key} className="rounded-lg bg-white border border-warm-100 px-2.5 py-2">
                          <span className="block text-[10px] text-warm-400 uppercase">{key}</span>
                          <span className="text-xs font-medium text-warm-700">{val as string}</span>
                        </div>
                      ))}
                    </div>
                    <div className="flex items-center gap-2 text-xs text-warm-400">
                      <span className="inline-block h-1.5 w-1.5 rounded-full bg-success-400" />
                      运行中 · 有效期至 {new Date(generated.expiresAt).toLocaleDateString('zh-CN')}
                    </div>
                  </>
                )}

                {/* ── API result ──────────────────────────────────── */}
                {generated && selectedTarget === 'api' && (
                  <>
                    <div>
                      <label className="block text-[11px] font-semibold text-warm-500 uppercase tracking-wide mb-1">
                        API 端点
                      </label>
                      <div className="flex items-center gap-2">
                        <div className="flex items-center gap-1.5 flex-1 rounded-lg border border-warm-200 bg-white px-3 py-2">
                          <span className="inline-flex items-center rounded bg-success-100 px-1.5 py-0.5 text-[10px] font-bold text-success-700 font-mono">
                            {generated.method}
                          </span>
                          <code className="text-xs font-mono text-warm-700 truncate">
                            {generated.endpoint}
                          </code>
                        </div>
                        <button
                          type="button"
                          onClick={() => copyToClipboard(generated.endpoint, 'endpoint')}
                          className={`flex h-8 w-8 items-center justify-center rounded-lg border transition-all shrink-0 ${
                            copiedField === 'endpoint'
                              ? 'border-success-300 bg-success-50 text-success-600'
                              : 'border-warm-200 bg-white text-warm-500 hover:border-primary-300 hover:text-primary-600'
                          }`}
                          title="复制端点"
                        >
                          {copiedField === 'endpoint' ? (
                            <Check className="h-3.5 w-3.5" />
                          ) : (
                            <Copy className="h-3.5 w-3.5" />
                          )}
                        </button>
                      </div>
                    </div>
                    <div>
                      <label className="block text-[11px] font-semibold text-warm-500 uppercase tracking-wide mb-1">
                        API 密钥
                      </label>
                      <div className="flex items-center gap-2">
                        <div className="flex items-center gap-1.5 flex-1 rounded-lg border border-warm-200 bg-white px-3 py-2">
                          <Key className="h-3.5 w-3.5 text-warning-500 shrink-0" />
                          <code className="text-xs font-mono text-warm-700 truncate select-all">
                            {generated.apiKey}
                          </code>
                        </div>
                        <button
                          type="button"
                          onClick={() => copyToClipboard(generated.apiKey, 'apiKey')}
                          className={`flex h-8 w-8 items-center justify-center rounded-lg border transition-all shrink-0 ${
                            copiedField === 'apiKey'
                              ? 'border-success-300 bg-success-50 text-success-600'
                              : 'border-warm-200 bg-white text-warm-500 hover:border-primary-300 hover:text-primary-600'
                          }`}
                          title="复制密钥"
                        >
                          {copiedField === 'apiKey' ? (
                            <Check className="h-3.5 w-3.5" />
                          ) : (
                            <Copy className="h-3.5 w-3.5" />
                          )}
                        </button>
                      </div>
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      {Object.entries(generated.config).map(([key, val]) => (
                        <div key={key} className="rounded-lg bg-white border border-warm-100 px-2.5 py-2">
                          <span className="block text-[10px] text-warm-400 uppercase">{key}</span>
                          <span className="text-xs font-medium text-warm-700">{val as string}</span>
                        </div>
                      ))}
                    </div>
                    <a
                      href={generated.docsUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-xs text-primary-500 hover:text-primary-600 transition-colors"
                    >
                      <ExternalLink className="h-3 w-3" />
                      API 文档
                    </a>
                  </>
                )}

                {/* ── Embed result ────────────────────────────────── */}
                {generated && selectedTarget === 'embed' && (
                  <>
                    <div>
                      <label className="block text-[11px] font-semibold text-warm-500 uppercase tracking-wide mb-1">
                        iframe 嵌入代码
                      </label>
                      <div className="relative group">
                        <pre className="rounded-lg border border-warm-200 bg-warm-800 text-warm-100 px-3 py-2.5 overflow-x-auto text-[11px] font-mono leading-relaxed">
                          {generated.iframe}
                        </pre>
                        <button
                          type="button"
                          onClick={() => copyToClipboard(generated.iframe, 'iframe')}
                          className={`absolute top-2 right-2 flex h-7 w-7 items-center justify-center rounded-md border transition-all ${
                            copiedField === 'iframe'
                              ? 'border-success-400 bg-success-500/20 text-success-300'
                              : 'border-warm-600 bg-warm-700/60 text-warm-300 hover:bg-warm-600 hover:text-white'
                          }`}
                          title="复制代码"
                        >
                          {copiedField === 'iframe' ? (
                            <Check className="h-3 w-3" />
                          ) : (
                            <Copy className="h-3 w-3" />
                          )}
                        </button>
                      </div>
                    </div>
                    <div>
                      <label className="block text-[11px] font-semibold text-warm-500 uppercase tracking-wide mb-1">
                        JS SDK 引入
                      </label>
                      <div className="relative group">
                        <pre className="rounded-lg border border-warm-200 bg-warm-800 text-warm-100 px-3 py-2.5 overflow-x-auto text-[11px] font-mono leading-relaxed">
                          {generated.scriptTag}
                        </pre>
                        <button
                          type="button"
                          onClick={() => copyToClipboard(generated.scriptTag, 'script')}
                          className={`absolute top-2 right-2 flex h-7 w-7 items-center justify-center rounded-md border transition-all ${
                            copiedField === 'script'
                              ? 'border-success-400 bg-success-500/20 text-success-300'
                              : 'border-warm-600 bg-warm-700/60 text-warm-300 hover:bg-warm-600 hover:text-white'
                          }`}
                          title="复制代码"
                        >
                          {copiedField === 'script' ? (
                            <Check className="h-3 w-3" />
                          ) : (
                            <Copy className="h-3 w-3" />
                          )}
                        </button>
                      </div>
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      {Object.entries(generated.config).map(([key, val]) => (
                        <div key={key} className="rounded-lg bg-white border border-warm-100 px-2.5 py-2">
                          <span className="block text-[10px] text-warm-400 uppercase">{key}</span>
                          <span className="text-xs font-medium text-warm-700">{val as string}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}

                {/* ── Package result ──────────────────────────────────── */}
                {generated && selectedTarget === 'package' && (
                  <>
                    <div className="flex items-center gap-3 rounded-xl border border-warm-200 bg-white px-4 py-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-warning-100 shrink-0">
                        <Package className="h-5 w-5 text-warning-600" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <span className="block text-sm font-semibold text-warm-800 truncate">
                          {generated.packageName}
                        </span>
                        <span className="text-xs text-warm-400">
                          {generated.size} · Node.js {generated.nodeVersion}
                        </span>
                      </div>
                      <Download className="h-4 w-4 text-warm-400" />
                    </div>
                    <div>
                      <label className="block text-[11px] font-semibold text-warm-500 uppercase tracking-wide mb-1">
                        启动命令
                      </label>
                      <div className="flex items-center gap-2">
                        <div className="flex items-center gap-1.5 flex-1 rounded-lg border border-warm-200 bg-warm-800 px-3 py-2">
                          <Terminal className="h-3.5 w-3.5 text-warm-300 shrink-0" />
                          <code className="text-xs font-mono text-success-300 truncate">
                            {generated.startCommand}
                          </code>
                        </div>
                        <button
                          type="button"
                          onClick={() => copyToClipboard(generated.startCommand, 'command')}
                          className={`flex h-8 w-8 items-center justify-center rounded-lg border transition-all shrink-0 ${
                            copiedField === 'command'
                              ? 'border-success-300 bg-success-50 text-success-600'
                              : 'border-warm-200 bg-white text-warm-500 hover:border-primary-300 hover:text-primary-600'
                          }`}
                          title="复制命令"
                        >
                          {copiedField === 'command' ? (
                            <Check className="h-3.5 w-3.5" />
                          ) : (
                            <Copy className="h-3.5 w-3.5" />
                          )}
                        </button>
                      </div>
                    </div>
                    <div>
                      <label className="block text-[11px] font-semibold text-warm-500 uppercase tracking-wide mb-1">
                        环境变量
                      </label>
                      <div className="flex flex-wrap gap-1.5">
                        {generated.envVars.map((env: string) => (
                          <code
                            key={env}
                            className="rounded-md bg-white border border-warm-150 px-2 py-1 text-[10px] font-mono text-warm-600"
                          >
                            {env}
                          </code>
                        ))}
                      </div>
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* ── Footer ──────────────────────────────────────────────────── */}
          <div className="flex items-center justify-between px-6 py-4 border-t border-warm-100 bg-warm-50/50 shrink-0">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleReset}
                disabled={generating}
                className="inline-flex items-center gap-1.5 rounded-lg border border-warm-200 bg-white px-3 py-2 text-xs font-medium text-warm-600 hover:bg-warm-100 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                重置配置
              </button>
              <button
                type="button"
                className="inline-flex items-center gap-1.5 rounded-lg border border-warm-200 bg-white px-3 py-2 text-xs font-medium text-warm-500 hover:bg-warm-100 transition-all"
              >
                <FileText className="h-3.5 w-3.5" />
                查看部署文档
              </button>
            </div>
            <button
              type="button"
              onClick={handleMainAction}
              disabled={generating || !configValid}
              title={!configValid ? '请先完成部署配置' : undefined}
              className="inline-flex items-center gap-2 rounded-lg bg-primary-500 px-5 py-2.5 text-sm font-semibold text-white hover:bg-primary-600 disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.97] transition-all shadow-sm"
            >
              {generating ? (
                <>
                  <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                  生成中...
                </>
              ) : (
                <>
                  <Rocket className="h-4 w-4" />
                  {generated ? currentOption.actionLabel : currentOption.actionLabel}
                </>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* ── Toast notification ──────────────────────────────────────── */}
      {toastMessage && (
        <div className="fixed top-6 left-1/2 -translate-x-1/2 z-[120] pointer-events-none animate-in slide-in-from-top-2 fade-in duration-200">
          <div className="inline-flex items-center gap-2 rounded-xl bg-warm-800 px-4 py-2.5 text-sm text-white shadow-lg">
            <Check className="h-4 w-4 text-success-400" />
            {toastMessage}
          </div>
        </div>
      )}
    </>
  );
});

export default OneClickDeployModal;
