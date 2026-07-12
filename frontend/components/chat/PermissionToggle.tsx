import { memo } from 'react';

// ── Exec Permission Mode ─────────────────────────────────────────────
// 1 = 询问权限 (Ask)   — every file/shell op requires user confirmation
// 2 = 跳过权限 (Skip)   — auto-execute all ops without confirmation (dev mode)
// 3 = 计划模式 (Plan)   — read-only; deny all write/delete/shell operations

export type ExecPermission = 1 | 2 | 3;

interface PermissionToggleProps {
  value: ExecPermission;
  onChange: (mode: ExecPermission) => void;
}

const MODES: Array<{
  value: ExecPermission;
  label: string;
  shortLabel: string;
  icon: string;
  description: string;
}> = [
  {
    value: 1,
    label: '询问权限',
    shortLabel: '询问',
    icon: '[lock]',
    description: '文件/Shell操作需用户确认',
  },
  {
    value: 2,
    label: '跳过权限',
    shortLabel: '跳过',
    icon: '[auto]',
    description: '自动执行所有操作（开发模式）',
  },
  {
    value: 3,
    label: '计划模式',
    shortLabel: '计划',
    icon: '[plan]',
    description: '只读规划，禁止写/删/执行',
  },
];

const PermissionToggle = memo(function PermissionToggle({
  value,
  onChange,
}: PermissionToggleProps) {
  return (
    <div
      className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-warm-50 border border-warm-150 select-none"
      role="radiogroup"
      aria-label="执行权限模式"
      title="选择AI Agent的执行权限级别"
    >
      <span className="text-[10px] text-warm-400 mr-1 shrink-0 font-medium tracking-wide uppercase">
        Mode
      </span>
      {MODES.map((mode, idx) => {
        const isActive = value === mode.value;
        const isFirst = idx === 0;
        const isLast = idx === MODES.length - 1;

        // Active color per mode
        const activeColors: Record<number, string> = {
          1: 'bg-blue-500 text-white border-blue-500 shadow-sm',      // Ask → blue
          2: 'bg-amber-500 text-white border-amber-500 shadow-sm',    // Skip → amber
          3: 'bg-purple-500 text-white border-purple-500 shadow-sm',  // Plan → purple
        };

        // Inactive hover color per mode
        const hoverColors: Record<number, string> = {
          1: 'hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700',
          2: 'hover:border-amber-300 hover:bg-amber-50 hover:text-amber-700',
          3: 'hover:border-purple-300 hover:bg-purple-50 hover:text-purple-700',
        };

        // Active indicator dot color per mode
        const dotColors: Record<number, string> = {
          1: 'bg-blue-400',
          2: 'bg-amber-400',
          3: 'bg-purple-400',
        };

        return (
          <button
            key={mode.value}
            type="button"
            role="radio"
            aria-checked={isActive}
            tabIndex={isActive ? 0 : -1}
            onClick={() => onChange(mode.value)}
            className={`
              relative flex items-center gap-1 px-2.5 py-1 text-xs font-medium
              rounded-full border transition-all duration-150 ease-out
              ${isActive
                ? activeColors[mode.value]
                : `border-transparent text-warm-500 ${hoverColors[mode.value]}`
              }
              ${!isFirst && !isActive ? '-ml-0.5' : ''}
              ${!isLast && !isActive ? '-mr-0.5' : ''}
              focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-300 focus-visible:ring-offset-1
            `}
            title={`${mode.label}：${mode.description}`}
          >
            <span className="text-sm leading-none">{mode.icon}</span>
            <span>{mode.shortLabel}</span>
            {isActive && (
              <span
                className={`absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full ${dotColors[mode.value]} ring-1 ring-white`}
              />
            )}
          </button>
        );
      })}
    </div>
  );
});

export default PermissionToggle;
