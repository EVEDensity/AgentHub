'use client';

import { memo, useState, type JSX, useEffect, useRef, useMemo } from 'react';
import {
  X, Rocket, Monitor, Server, Globe, Info,
  Eye, Shield, Settings, ChevronDown, ChevronRight,
  FolderGit2, MapPin, Key, Wrench, Activity, Heart,
} from 'lucide-react';

// ── Types ────────────────────────────────────────────────────────────────

interface DeployModalProps {
  open: boolean;
  version: string;
  description: string;
  /** 可选项目列表 */
  projects: { id: string; name: string; defaultBranch: string; defaultDomain: string }[];
  /** 默认项目 ID */
  defaultProjectId: string;
  /** 默认分支（当 defaultProjectId 对应的项目未匹配时使用） */
  defaultBranch: string;
  /** 默认域名（当 defaultProjectId 对应的项目未匹配时使用） */
  defaultDomain: string;
  onConfirm: (details: {
    projectId: string;
    branch: string;
    domain: string;
    deployType: 'preview' | 'production' | 'custom';
    environment: string;
    notes: string;
    targets: string[];
  }) => void;
  onCancel: () => void;
}

// ── Constants ─────────────────────────────────────────────────────────────

const ENVIRONMENTS = [
  { id: 'dev', label: '开发环境', icon: Monitor, desc: '本地开发服务器' },
  { id: 'staging', label: '预发布', icon: Server, desc: '测试/预发布环境' },
  { id: 'production', label: '生产环境', icon: Globe, desc: '线上生产环境（需谨慎）' },
] as const;

const DEPLOY_TYPES = [
  { id: 'preview' as const, label: '预览部署', icon: Eye, desc: '生成预览链接，快速验证' },
  { id: 'production' as const, label: '生产部署', icon: Shield, desc: '正式上线至生产环境' },
  { id: 'custom' as const, label: '自定义', icon: Settings, desc: '按需自定义部署参数' },
];

const DEPLOY_TARGETS = [
  { id: 'frontend', label: '前端', desc: 'Next.js / React 应用', checked: true },
  { id: 'backend', label: '后端', desc: 'FastAPI / Python 服务', checked: false },
  { id: 'static', label: '静态资源', desc: 'CDN / 静态文件', checked: false },
] as const;

const REGIONS = [
  { id: 'us-east-1', label: '美国东部 (us-east-1)' },
  { id: 'ap-southeast-1', label: '亚太东南 (ap-southeast-1)' },
  { id: 'eu-west-1', label: '欧洲西部 (eu-west-1)' },
] as const;

// ── Validation ────────────────────────────────────────────────────────────

const BRANCH_REGEX = /^[a-zA-Z0-9._/-]+$/;
const DOMAIN_REGEX = /^[a-z0-9-]+$/;

function validateBranch(v: string): string | null {
  if (!v.trim()) return '请输入分支名称';
  if (v.startsWith('/')) return '分支不能以 / 开头';
  if (/\s/.test(v)) return '分支不能包含空格';
  if (!BRANCH_REGEX.test(v)) return '分支包含非法字符，仅允许字母、数字、.、_、/、-';
  return null;
}

function validateDomain(v: string): string | null {
  if (!v.trim()) return '请输入域名';
  if (!DOMAIN_REGEX.test(v)) return '域名只能包含小写字母、数字和连字符';
  return null;
}

// ── Component ─────────────────────────────────────────────────────────────

const DeployModal = memo(function DeployModal({
  open,
  version,
  description,
  projects,
  defaultProjectId,
  defaultBranch,
  defaultDomain,
  onConfirm,
  onCancel,
}: DeployModalProps): JSX.Element | null {
  // ── State ──────────────────────────────────────────────────────────
  const [projectId, setProjectId] = useState(defaultProjectId);
  const [branch, setBranch] = useState(defaultBranch);
  const [domain, setDomain] = useState(defaultDomain);
  const [deployType, setDeployType] = useState<'preview' | 'production' | 'custom'>('preview');
  const [environment, setEnvironment] = useState('dev');
  const [notes, setNotes] = useState('');
  const [targets, setTargets] = useState<string[]>(['frontend']);
  const [submitting, setSubmitting] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [branchTouched, setBranchTouched] = useState(false);
  const [domainTouched, setDomainTouched] = useState(false);
  const overlayRef = useRef<HTMLDivElement>(null);
  const projectSelectRef = useRef<HTMLSelectElement>(null);

  // ── Derived validation ──────────────────────────────────────────────
  const branchError = useMemo(() => {
    if (!branchTouched) return null;
    return validateBranch(branch);
  }, [branch, branchTouched]);

  const domainError = useMemo(() => {
    if (!domainTouched) return null;
    return validateDomain(domain);
  }, [domain, domainTouched]);

  const isSubmitDisabled =
    submitting ||
    !projectId ||
    !branch.trim() ||
    branchError !== null ||
    !domain.trim() ||
    domainError !== null ||
    targets.length === 0;

  // ── Effects ─────────────────────────────────────────────────────────

  // Sync branch & domain when project changes
  useEffect(() => {
    const project = projects.find((p) => p.id === projectId);
    if (project) {
      setBranch(project.defaultBranch);
      setDomain(project.defaultDomain);
      setBranchTouched(false);
      setDomainTouched(false);
    }
  }, [projectId, projects]);

  // Reset form on each open
  useEffect(() => {
    if (open) {
      setProjectId(defaultProjectId);
      setBranch(defaultBranch);
      setDomain(defaultDomain);
      setDeployType('preview');
      setEnvironment('dev');
      setNotes('');
      setTargets(['frontend']);
      setSubmitting(false);
      setAdvancedOpen(false);
      setBranchTouched(false);
      setDomainTouched(false);
      // Small delay for transition to finish, then focus project select
      const timer = setTimeout(() => projectSelectRef.current?.focus(), 150);
      return () => clearTimeout(timer);
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onCancel();
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onCancel]);

  // ── Handlers ────────────────────────────────────────────────────────

  function handleToggleTarget(targetId: string) {
    setTargets((prev) =>
      prev.includes(targetId)
        ? prev.filter((t) => t !== targetId)
        : [...prev, targetId]
    );
  }

  function handleSubmit() {
    setSubmitting(true);
    onConfirm({
      projectId,
      branch,
      domain,
      deployType,
      environment,
      notes: notes.trim(),
      targets,
    });
  }

  function handleOverlayClick(e: React.MouseEvent) {
    if (e.target === overlayRef.current) onCancel();
  }

  if (!open) return null;

  return (
    <div
      ref={overlayRef}
      onClick={handleOverlayClick}
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4"
    >
      <div
        className="w-full max-w-2xl bg-warm-100 rounded-2xl shadow-modal border border-warm-200 overflow-hidden animate-in zoom-in-95 duration-150"
        role="dialog"
        aria-modal="true"
        aria-label="部署配置"
      >
        {/* ── Header ──────────────────────────────────────────────── */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-warm-100">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-indigo-100">
              <Rocket className="h-5 w-5 text-indigo-600" />
            </div>
            <div>
              <h2 className="text-base font-bold text-warm-800">部署配置</h2>
              <p className="text-xs text-warm-400">
                版本 <code className="font-mono text-indigo-600">{version}</code>
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-warm-400 hover:bg-warm-100 hover:text-warm-600 transition-colors focus:ring-2 focus:ring-indigo-400/60"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* ── Body ────────────────────────────────────────────────── */}
        <div className="px-6 py-4 space-y-5 max-h-[60vh] overflow-y-auto">

          {/* Description summary */}
          <div className="rounded-lg bg-warm-50 border border-warm-100 px-3 py-2.5">
            <p className="text-xs text-warm-500 line-clamp-2">{description}</p>
          </div>

          {/* ════════════════════════════════════════════════════════
              P0 ① — 项目选择
              ════════════════════════════════════════════════════════ */}
          <fieldset>
            <legend className="text-sm font-semibold text-warm-700 mb-2.5">
              项目 <span className="text-danger-500">*</span>
            </legend>
            <select
              ref={projectSelectRef}
              value={projectId}
              onChange={(e) => {
                setProjectId(e.target.value);
              }}
              className="w-full rounded-lg border border-warm-200 bg-warm-100 px-3 py-2.5 text-sm text-warm-800 focus:outline-none focus:ring-2 focus:ring-indigo-400/60 focus:border-indigo-400 transition-shadow appearance-none bg-[image:url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2216%22%20height%3D%2216%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%23999%22%20stroke-width%3D%222%22%3E%3Cpath%20d%3D%22m6%209%206%206%206-6%22%2F%3E%3C%2Fsvg%3E')] bg-[length:16px] bg-[right_8px_center] bg-no-repeat pr-8"
              aria-label="选择部署项目"
            >
              <option value="" disabled>
                请选择项目...
              </option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </fieldset>

          {/* ════════════════════════════════════════════════════════
              P0 ② — 分支 + 域名（2 列网格）
              ════════════════════════════════════════════════════════ */}
          <fieldset>
            <legend className="text-sm font-semibold text-warm-700 mb-2.5">
              Git 分支 & 部署域名
            </legend>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {/* Branch */}
              <div>
                <label
                  htmlFor="deploy-branch"
                  className="block text-xs font-medium text-warm-500 mb-1"
                >
                  分支 <span className="text-danger-500">*</span>
                </label>
                <div className="relative">
                  <FolderGit2 className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-warm-350 pointer-events-none" />
                  <input
                    id="deploy-branch"
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
                    className={`w-full rounded-lg border pl-8 pr-3 py-2.5 text-sm font-mono text-warm-800 placeholder:text-warm-350 focus:outline-none focus:ring-2 focus:ring-indigo-400/60 transition-shadow ${
                      branchError !== null
                        ? 'border-danger-500 focus:border-danger-500 focus:ring-danger-500/40'
                        : 'border-warm-200 focus:border-indigo-400'
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
                <label
                  htmlFor="deploy-domain"
                  className="block text-xs font-medium text-warm-500 mb-1"
                >
                  域名 <span className="text-danger-500">*</span>
                </label>
                <div className="relative">
                  <Globe className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-warm-350 pointer-events-none" />
                  <input
                    id="deploy-domain"
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
                    className={`w-full rounded-lg border pl-8 pr-3 py-2.5 text-sm font-mono text-warm-800 placeholder:text-warm-350 focus:outline-none focus:ring-2 focus:ring-indigo-400/60 transition-shadow ${
                      domainError !== null
                        ? 'border-danger-500 focus:border-danger-500 focus:ring-danger-500/40'
                        : 'border-warm-200 focus:border-indigo-400'
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
          </fieldset>

          {/* ════════════════════════════════════════════════════════
              P0 ③ — 部署类型
              ════════════════════════════════════════════════════════ */}
          <fieldset>
            <legend className="text-sm font-semibold text-warm-700 mb-2.5">
              部署类型 <span className="text-danger-500">*</span>
            </legend>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              {DEPLOY_TYPES.map((dt) => {
                const Icon = dt.icon;
                const isActive = deployType === dt.id;
                return (
                  <button
                    key={dt.id}
                    type="button"
                    onClick={() => setDeployType(dt.id)}
                    className={`flex flex-col items-center gap-1 rounded-xl border-2 px-3 py-3 text-center transition-all ${
                      isActive
                        ? 'border-indigo-400 bg-indigo-50 shadow-sm'
                        : 'border-warm-150 bg-warm-100 hover:border-warm-250 hover:bg-warm-50'
                    }`}
                    aria-pressed={isActive}
                    aria-label={dt.label}
                  >
                    <Icon
                      className={`h-5 w-5 ${isActive ? 'text-indigo-600' : 'text-warm-400'}`}
                    />
                    <span
                      className={`text-xs font-medium ${isActive ? 'text-indigo-700' : 'text-warm-600'}`}
                    >
                      {dt.label}
                    </span>
                    <span className="text-[10px] text-warm-400 leading-tight">
                      {dt.desc}
                    </span>
                  </button>
                );
              })}
            </div>
          </fieldset>

          {/* ════════════════════════════════════════════════════════
              P0 ④ — 部署环境（原有，保留）
              ════════════════════════════════════════════════════════ */}
          <fieldset>
            <legend className="text-sm font-semibold text-warm-700 mb-2.5">
              部署环境
            </legend>
            <div className="grid grid-cols-3 gap-2">
              {ENVIRONMENTS.map((env) => {
                const Icon = env.icon;
                const isActive = environment === env.id;
                return (
                  <button
                    key={env.id}
                    type="button"
                    onClick={() => setEnvironment(env.id)}
                    className={`flex flex-col items-center gap-1 rounded-xl border-2 px-3 py-3 text-center transition-all ${
                      isActive
                        ? 'border-indigo-400 bg-indigo-50 shadow-sm'
                        : 'border-warm-150 bg-warm-100 hover:border-warm-250 hover:bg-warm-50'
                    }`}
                    aria-pressed={isActive}
                  >
                    <Icon
                      className={`h-5 w-5 ${isActive ? 'text-indigo-600' : 'text-warm-400'}`}
                    />
                    <span
                      className={`text-xs font-medium ${isActive ? 'text-indigo-700' : 'text-warm-600'}`}
                    >
                      {env.label}
                    </span>
                    <span className="text-[10px] text-warm-400 leading-tight">
                      {env.desc}
                    </span>
                  </button>
                );
              })}
            </div>
          </fieldset>

          {/* ════════════════════════════════════════════════════════
              部署目标（原有，保留）
              ════════════════════════════════════════════════════════ */}
          <fieldset>
            <legend className="text-sm font-semibold text-warm-700 mb-2.5">
              部署目标
            </legend>
            <div className="space-y-2">
              {DEPLOY_TARGETS.map((t) => {
                const checked = targets.includes(t.id);
                return (
                  <label
                    key={t.id}
                    className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 cursor-pointer transition-all ${
                      checked
                        ? 'border-indigo-300 bg-indigo-50/50'
                        : 'border-warm-150 bg-warm-100 hover:border-warm-250'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => handleToggleTarget(t.id)}
                      className="h-4 w-4 rounded border-warm-300 text-indigo-600 focus:ring-indigo-500"
                    />
                    <div className="flex-1 min-w-0">
                      <span className="text-sm font-medium text-warm-700">{t.label}</span>
                      <span className="ml-2 text-xs text-warm-400">{t.desc}</span>
                    </div>
                  </label>
                );
              })}
            </div>
          </fieldset>

          {/* ── Notes ──────────────────────────────────────────────── */}
          <div>
            <label htmlFor="deploy-notes" className="block text-sm font-semibold text-warm-700 mb-1.5">
              部署备注
            </label>
            <input
              id="deploy-notes"
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !isSubmitDisabled) handleSubmit();
              }}
              placeholder="如：修复了登录页样式问题..."
              className="w-full rounded-lg border border-warm-200 px-3 py-2 text-sm text-warm-800 placeholder:text-warm-350 focus:outline-none focus:ring-2 focus:ring-indigo-400/60 focus:border-indigo-400 transition-shadow"
              maxLength={200}
            />
          </div>

          {/* ════════════════════════════════════════════════════════
              P1/P2 — 高级配置（折叠面板，全部 disabled）
              ════════════════════════════════════════════════════════ */}
          <div className="rounded-xl border border-warm-150 overflow-hidden">
            <button
              type="button"
              onClick={() => setAdvancedOpen((v) => !v)}
              className="flex items-center justify-between w-full px-4 py-3 text-left hover:bg-warm-50 transition-colors focus:ring-2 focus:ring-indigo-400/60 focus:ring-inset"
              aria-expanded={advancedOpen}
              aria-controls="advanced-panel"
            >
              <div className="flex items-center gap-2">
                <Settings className="h-4 w-4 text-warm-400" />
                <span className="text-sm font-semibold text-warm-700">高级配置</span>
                <span className="rounded bg-warm-100 px-1.5 py-0.5 text-[10px] font-medium text-warm-400">
                  即将推出
                </span>
              </div>
              {advancedOpen ? (
                <ChevronDown className="h-4 w-4 text-warm-400" />
              ) : (
                <ChevronRight className="h-4 w-4 text-warm-400" />
              )}
            </button>

            {advancedOpen && (
              <div
                id="advanced-panel"
                className="border-t border-warm-100 px-4 py-4 space-y-4 bg-warm-50/30"
              >
                {/* ⑤ 区域 (region) — select, disabled */}
                <div>
                  <label className="block text-xs font-medium text-warm-400 mb-1">
                    <MapPin className="inline h-3.5 w-3.5 mr-1" />
                    部署区域
                  </label>
                  <select
                    disabled
                    defaultValue="us-east-1"
                    className="w-full rounded-lg border border-warm-150 bg-warm-50 px-3 py-2.5 text-sm text-warm-400 opacity-50 cursor-not-allowed appearance-none bg-[image:url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2216%22%20height%3D%2216%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%23999%22%20stroke-width%3D%222%22%3E%3Cpath%20d%3D%22m6%209%206%206%206-6%22%2F%3E%3C%2Fsvg%3E')] bg-[length:16px] bg-[right_8px_center] bg-no-repeat pr-8"
                    aria-disabled="true"
                    aria-label="部署区域（即将推出）"
                  >
                    {REGIONS.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                </div>

                {/* ⑥ 环境变量 (envVars) — key-value 编辑器, disabled */}
                <fieldset>
                  <legend className="text-xs font-medium text-warm-400 mb-2">
                    <Key className="inline h-3.5 w-3.5 mr-1" />
                    环境变量
                  </legend>
                  <div className="space-y-2">
                    {['NEXT_PUBLIC_API_URL', 'DATABASE_URL', 'REDIS_URL'].map((key, i) => (
                      <div key={key} className="grid grid-cols-2 gap-2">
                        <input
                          type="text"
                          value={key}
                          disabled
                          readOnly
                          className="rounded-lg border border-warm-150 bg-warm-50 px-3 py-2 text-xs font-mono text-warm-400 opacity-50 cursor-not-allowed"
                          aria-disabled="true"
                        />
                        <input
                          type="text"
                          value={['https://api.example.com', 'postgres://...', 'redis://...'][i]}
                          disabled
                          readOnly
                          className="rounded-lg border border-warm-150 bg-warm-50 px-3 py-2 text-xs font-mono text-warm-400 opacity-50 cursor-not-allowed"
                          aria-disabled="true"
                        />
                      </div>
                    ))}
                  </div>
                </fieldset>

                {/* ⑦ 构建配置 (buildSettings) — 3 readonly inputs */}
                <fieldset>
                  <legend className="text-xs font-medium text-warm-400 mb-2">
                    <Wrench className="inline h-3.5 w-3.5 mr-1" />
                    构建配置
                  </legend>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    {[
                      { label: 'Node 版本', value: '20.x' },
                      { label: '包管理器', value: 'pnpm' },
                      { label: '输出目录', value: '.next' },
                    ].map((cfg) => (
                      <div key={cfg.label}>
                        <span className="block text-[10px] text-warm-400 mb-0.5">{cfg.label}</span>
                        <input
                          type="text"
                          value={cfg.value}
                          disabled
                          readOnly
                          className="w-full rounded-lg border border-warm-150 bg-warm-50 px-3 py-2 text-xs font-mono text-warm-400 opacity-50 cursor-not-allowed"
                          aria-disabled="true"
                        />
                      </div>
                    ))}
                  </div>
                </fieldset>

                {/* ⑧ 流量切分 (trafficSplit) — slider, disabled */}
                <div>
                  <label className="block text-xs font-medium text-warm-400 mb-1">
                    <Activity className="inline h-3.5 w-3.5 mr-1" />
                    流量切分
                  </label>
                  <div className="flex items-center gap-3">
                    <input
                      type="range"
                      min={0}
                      max={100}
                      value={100}
                      disabled
                      className="flex-1 h-2 rounded-full appearance-none bg-warm-150 opacity-50 cursor-not-allowed [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-warm-300"
                      aria-disabled="true"
                      aria-label="流量切分比例（即将推出）"
                    />
                    <span className="w-10 text-right text-xs font-mono text-warm-400 opacity-50">
                      100%
                    </span>
                  </div>
                </div>

                {/* ⑨ 健康检查 (healthCheck) — URL + timeout */}
                <fieldset>
                  <legend className="text-xs font-medium text-warm-400 mb-2">
                    <Heart className="inline h-3.5 w-3.5 mr-1" />
                    健康检查
                  </legend>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <span className="block text-[10px] text-warm-400 mb-0.5">检查路径</span>
                      <input
                        type="text"
                        value="/api/health"
                        disabled
                        readOnly
                        className="w-full rounded-lg border border-warm-150 bg-warm-50 px-3 py-2 text-xs font-mono text-warm-400 opacity-50 cursor-not-allowed"
                        aria-disabled="true"
                      />
                    </div>
                    <div>
                      <span className="block text-[10px] text-warm-400 mb-0.5">超时 (秒)</span>
                      <input
                        type="text"
                        value="30"
                        disabled
                        readOnly
                        className="w-full rounded-lg border border-warm-150 bg-warm-50 px-3 py-2 text-xs font-mono text-warm-400 opacity-50 cursor-not-allowed"
                        aria-disabled="true"
                      />
                    </div>
                  </div>
                </fieldset>
              </div>
            )}
          </div>

          {/* ── Info banner ────────────────────────────────────────── */}
          <div className="flex items-start gap-2 rounded-lg bg-warning-50 border border-warning-100 px-3 py-2.5">
            <Info className="h-4 w-4 text-warning-500 shrink-0 mt-0.5" />
            <p className="text-xs text-warning-700">
              当前仅支持前端部署预览，后端和静态资源部署功能将在后续版本中提供。
            </p>
          </div>
        </div>

        {/* ── Footer ──────────────────────────────────────────────── */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-warm-100 bg-warm-50/50">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="rounded-lg border border-warm-200 bg-warm-100 px-4 py-2 text-sm font-medium text-warm-600 hover:bg-warm-100 disabled:opacity-50 transition-all focus:ring-2 focus:ring-indigo-400/60"
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={isSubmitDisabled}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-5 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50 active:scale-[0.97] transition-all shadow-sm focus:ring-2 focus:ring-indigo-400/60"
          >
            <Rocket className="h-4 w-4" />
            {submitting ? '部署中...' : '确认部署'}
          </button>
        </div>
      </div>
    </div>
  );
});

export default DeployModal;
