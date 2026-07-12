import { memo, useEffect, useRef, useState } from 'react';
import { Shield, Zap, Compass, Check, ChevronDown } from 'lucide-react';

// ── Exec Permission Mode ─────────────────────────────────────────────
// 1 = 询问权限 (Ask)   — every file/shell op requires user confirmation
// 2 = 跳过权限 (Skip)   — auto-execute all ops without confirmation (dev mode)
// 3 = 计划模式 (Plan)   — read-only; deny all write/delete/shell operations

export type ExecPermission = 1 | 2 | 3;

interface PermissionModePopoverProps {
  value: ExecPermission;
  onChange: (mode: ExecPermission) => void;
}

type ModeMeta = {
  value: ExecPermission;
  label: string;
  shortLabel: string;
  description: string;
  Icon: typeof Shield;
  iconWrap: string;
  iconFg: string;
  bar: string;
  bg: string;
  hover: string;
};

const MODES: ModeMeta[] = [
  {
    value: 1, label: '询问权限', shortLabel: '询问',
    description: 'CLI 请求时确认文件编辑和高风险命令',
    Icon: Shield,
    iconWrap: 'bg-primary-50',
    iconFg:   'text-primary-500',
    bar:      'bg-primary-500',
    bg:       'bg-primary-50',
    hover:    'hover:bg-warm-100',
  },
  {
    value: 2, label: '跳过权限', shortLabel: '跳过',
    description: '对 Shell 和文件系统的完整工具访问',
    Icon: Zap,
    iconWrap: 'bg-warning-50',
    iconFg:   'text-warning-600',
    bar:      'bg-warning-500',
    bg:       'bg-warning-50',
    hover:    'hover:bg-warm-100',
  },
  {
    value: 3, label: '计划模式', shortLabel: '计划',
    description: '仅架构和推理，不操作文件',
    Icon: Compass,
    iconWrap: 'bg-warm-150',
    iconFg:   'text-warm-500',
    bar:      'bg-warm-500',
    bg:       'bg-warm-100',
    hover:    'hover:bg-warm-100',
  },
];

const PermissionModePopover = memo(function PermissionModePopover({
  value,
  onChange,
}: PermissionModePopoverProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  const current = MODES.find((m) => m.value === value) ?? MODES[0];
  const CurrentIcon = current.Icon;

  // outside click + Esc
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node | null;
      if (!t) return;
      if (panelRef.current?.contains(t)) return;
      if (triggerRef.current?.contains(t)) return;
      setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onEsc);
    };
  }, [open]);

  function pick(v: ExecPermission) {
    onChange(v);
    setOpen(false);
  }

  return (
    <div className="inline-flex">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={`当前权限：${current.label} — 点击切换`}
        className={`
          group inline-flex h-8 items-center gap-1.5 rounded-lg
          border bg-warm-100 pl-1.5 pr-2 text-warm-600
          transition-all duration-150 ease-out
          hover:border-warm-300 hover:bg-warm-50
          focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-300
          ${open ? 'border-warm-300 bg-warm-50' : 'border-warm-200'}
        `}
      >
        <span
          className={`
            inline-flex h-5 w-5 items-center justify-center rounded-md
            transition-colors ${current.iconWrap}
          `}
        >
          <CurrentIcon className={`h-3 w-3 ${current.iconFg}`} strokeWidth={2.4} />
        </span>
        <span className="text-xs font-medium tracking-tight">{current.shortLabel}</span>
        <ChevronDown
          className={`h-3.5 w-3.5 text-warm-400 transition-transform duration-200 ${
            open ? 'rotate-180 text-warm-600' : ''
          }`}
          strokeWidth={2.4}
        />
      </button>

      {/* 弹层：absolute + bottom-100% 让 panel 浮在 trigger 上方
          上层祖先 grid 已改为 overflow-visible → 不会被裁 */}
      {open && (
        <div
          ref={panelRef}
          role="dialog"
          aria-label="选择执行权限"
          className="
            absolute bottom-[calc(100%+6px)] left-0 z-50
            w-[320px] origin-bottom-left
            rounded-2xl border border-warm-200 bg-warm-100 p-1.5
            shadow-[0_18px_40px_-12px_rgba(28,25,23,0.18),0_2px_6px_-2px_rgba(28,25,23,0.08)]
            animate-[perm-popover-in_140ms_cubic-bezier(0.2,0.9,0.3,1.05)]
          "
          style={{ animationFillMode: 'forwards' }}
        >
          <div className="px-3 pt-2 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-warm-400">
            执行权限
          </div>

          <ul className="flex flex-col gap-0.5" role="radiogroup">
            {MODES.map((m) => {
              const isActive = value === m.value;
              const Icon = m.Icon;
              return (
                <li key={m.value} className="relative">
                  <span
                    aria-hidden
                    className={`
                      pointer-events-none absolute inset-y-1 left-0 w-[3px] rounded-r-full
                      transition-opacity duration-200
                      ${m.bar} ${isActive ? 'opacity-100' : 'opacity-0'}
                    `}
                  />
                  <button
                    type="button"
                    role="radio"
                    aria-checked={isActive}
                    onClick={() => pick(m.value)}
                    className={`
                      flex w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left
                      transition-colors duration-150
                      ${isActive ? m.bg : m.hover}
                      focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-300
                    `}
                  >
                    <span
                      className={`
                        flex h-9 w-9 shrink-0 items-center justify-center rounded-lg
                        ${m.iconWrap} ${isActive ? 'shadow-sm' : ''}
                      `}
                    >
                      <Icon className={`h-[18px] w-[18px] ${m.iconFg}`} strokeWidth={2.2} />
                    </span>

                    <span className="min-w-0 flex-1 pt-0.5">
                      <span className="flex items-center gap-2">
                        <span className="text-[13px] font-semibold leading-tight text-warm-800">
                          {m.label}
                        </span>
                        {isActive && (
                          <span
                            className={`
                              inline-flex h-4 w-4 items-center justify-center rounded-full
                              ${m.bar} text-white
                              animate-[perm-check-pop_180ms_cubic-bezier(0.2,0.9,0.3,1.4)]
                            `}
                          >
                            <Check className="h-2.5 w-2.5" strokeWidth={3.4} />
                          </span>
                        )}
                      </span>
                      <span className="mt-0.5 block text-[11px] leading-snug text-warm-500">
                        {m.description}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="mx-3 mt-1 border-t border-warm-100 pt-2 pb-1.5">
            <p className="text-[10.5px] leading-snug text-warm-400">
              不同模式会影响 Agent 对工具调用的拦截策略，切换后立即生效。
            </p>
          </div>
        </div>
      )}

      {/* keyframes: 通过 <style> 标签全局注入（避免 jsx 作用域影响 keyframe 名） */}
      <style>{`
        @keyframes perm-popover-in {
          from { opacity: 0; transform: translateY(4px) scale(0.96); }
          to   { opacity: 1; transform: translateY(0)   scale(1);    }
        }
        @keyframes perm-check-pop {
          from { opacity: 0; transform: scale(0.4); }
          to   { opacity: 1; transform: scale(1);   }
        }
      `}</style>
    </div>
  );
});

export default PermissionModePopover;
