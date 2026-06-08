'use client';

import { memo, useState, type JSX, useEffect, useRef } from 'react';
import { X, Rocket, Monitor, Server, Globe, Info } from 'lucide-react';

interface DeployModalProps {
  open: boolean;
  version: string;
  description: string;
  onConfirm: (details: { environment: string; notes: string; targets: string[] }) => void;
  onCancel: () => void;
}

const ENVIRONMENTS = [
  { id: 'dev', label: '开发环境', icon: Monitor, desc: '本地开发服务器' },
  { id: 'staging', label: '预发布', icon: Server, desc: '测试/预发布环境' },
  { id: 'production', label: '生产环境', icon: Globe, desc: '线上生产环境（需谨慎）' },
] as const;

const DEPLOY_TARGETS = [
  { id: 'frontend', label: '前端', desc: 'Next.js / React 应用', checked: true },
  { id: 'backend', label: '后端', desc: 'FastAPI / Python 服务', checked: false },
  { id: 'static', label: '静态资源', desc: 'CDN / 静态文件', checked: false },
] as const;

const DeployModal = memo(function DeployModal({
  open,
  version,
  description,
  onConfirm,
  onCancel,
}: DeployModalProps): JSX.Element | null {
  const [environment, setEnvironment] = useState('dev');
  const [notes, setNotes] = useState('');
  const [targets, setTargets] = useState<string[]>(['frontend']);
  const [submitting, setSubmitting] = useState(false);
  const overlayRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Focus the notes input when modal opens
  useEffect(() => {
    if (open) {
      // Reset form on each open
      setEnvironment('dev');
      setNotes('');
      setTargets(['frontend']);
      setSubmitting(false);
      // Small delay for transition to finish
      const timer = setTimeout(() => inputRef.current?.focus(), 150);
      return () => clearTimeout(timer);
    }
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onCancel();
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onCancel]);

  function handleToggleTarget(targetId: string) {
    setTargets((prev) =>
      prev.includes(targetId)
        ? prev.filter((t) => t !== targetId)
        : [...prev, targetId]
    );
  }

  function handleSubmit() {
    setSubmitting(true);
    onConfirm({ environment, notes: notes.trim(), targets });
  }

  function handleOverlayClick(e: React.MouseEvent) {
    if (e.target === overlayRef.current) onCancel();
  }

  if (!open) return null;

  return (
    <div
      ref={overlayRef}
      onClick={handleOverlayClick}
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-sm p-4 animate-in fade-in duration-150"
    >
      <div
        className="w-full max-w-lg bg-white rounded-2xl shadow-2xl border border-warm-200 overflow-hidden animate-in zoom-in-95 duration-150"
        role="dialog"
        aria-modal="true"
        aria-label="部署配置"
      >
        {/* ── Header ──────────────────────────────────────────── */}
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
            className="flex h-8 w-8 items-center justify-center rounded-lg text-warm-400 hover:bg-warm-100 hover:text-warm-600 transition-colors"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* ── Body ────────────────────────────────────────────── */}
        <div className="px-6 py-4 space-y-5 max-h-[60vh] overflow-y-auto">
          {/* Description summary */}
          <div className="rounded-lg bg-warm-50 border border-warm-100 px-3 py-2.5">
            <p className="text-xs text-warm-500 line-clamp-2">{description}</p>
          </div>

          {/* ── Environment selector ──────────────────────────── */}
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
                        : 'border-warm-150 bg-white hover:border-warm-250 hover:bg-warm-50'
                    }`}
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

          {/* ── Deploy targets ─────────────────────────────────── */}
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
                        : 'border-warm-150 bg-white hover:border-warm-250'
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

          {/* ── Notes ──────────────────────────────────────────── */}
          <div>
            <label className="block text-sm font-semibold text-warm-700 mb-1.5">
              部署备注
            </label>
            <input
              ref={inputRef}
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) handleSubmit();
              }}
              placeholder="如：修复了登录页样式问题..."
              className="w-full rounded-lg border border-warm-200 px-3 py-2 text-sm text-warm-800 placeholder:text-warm-350 focus:outline-none focus:ring-2 focus:ring-indigo-400/60 focus:border-indigo-400 transition-shadow"
              maxLength={200}
            />
          </div>

          {/* ── Info banner ────────────────────────────────────── */}
          <div className="flex items-start gap-2 rounded-lg bg-amber-50 border border-amber-150 px-3 py-2.5">
            <Info className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
            <p className="text-xs text-amber-700">
              当前仅支持前端部署预览，后端和静态资源部署功能将在后续版本中提供。
            </p>
          </div>
        </div>

        {/* ── Footer ──────────────────────────────────────────── */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-warm-100 bg-warm-50/50">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="rounded-lg border border-warm-200 bg-white px-4 py-2 text-sm font-medium text-warm-600 hover:bg-warm-100 disabled:opacity-50 transition-all"
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting || targets.length === 0}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-5 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50 active:scale-[0.97] transition-all shadow-sm"
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
