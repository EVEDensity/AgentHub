'use client';

import { type JSX, type ReactNode } from 'react';

// ── Types ─────────────────────────────────────────────────────────────

export interface TabItem<T extends string = string> {
  key: T;
  label: string;
  icon?: string;
  badge?: string | number;
  disabled?: boolean;
}

export interface TabsProps<T extends string = string> {
  tabs: readonly TabItem<T>[];
  active: T;
  onChange: (key: T) => void;
  variant?: 'underline' | 'pills' | 'buttons';
  size?: 'sm' | 'md';
  fullWidth?: boolean;
  className?: string;
}

// ── Style maps ────────────────────────────────────────────────────────

const VARIANT_STYLES = {
  underline: {
    container: 'gap-1 border-b border-warm-100',
    tab: (active: boolean) =>
      active
        ? 'text-primary-600 font-medium border-b-2 border-primary-500 -mb-[1px] bg-transparent'
        : 'text-warm-400 hover:text-warm-600 border-b-2 border-transparent',
  },
  pills: {
    container: 'gap-0.5',
    tab: (active: boolean) =>
      active
        ? 'bg-primary-500 text-white shadow-sm'
        : 'text-warm-500 hover:bg-warm-100',
  },
  buttons: {
    container: 'gap-0 border border-warm-200 rounded-lg overflow-hidden',
    tab: (active: boolean) =>
      active
        ? 'bg-warm-100 text-warm-700 font-medium'
        : 'text-warm-500 hover:bg-warm-50',
  },
};

const SIZE_STYLES = {
  sm: { tab: 'text-[10px] px-2.5 py-1.5', icon: 'text-[12px]' },
  md: { tab: 'text-xs px-3 py-2', icon: 'text-[14px]' },
};

// ── Component ─────────────────────────────────────────────────────────

export function Tabs<T extends string = string>({
  tabs,
  active,
  onChange,
  variant = 'underline',
  size = 'md',
  fullWidth = false,
  className = '',
}: TabsProps<T>): JSX.Element {
  const styles = VARIANT_STYLES[variant];
  const sizeStyle = SIZE_STYLES[size];
  const widthClass = fullWidth ? 'flex-1 justify-center' : '';

  return (
    <div className={`flex items-center ${styles.container} ${className}`}>
      {tabs.map((tab) => {
        const isActive = active === tab.key;
        return (
          <button
            key={tab.key}
            className={`inline-flex items-center gap-1.5 transition-all duration-200 outline-none focus-visible:ring-2 focus-visible:ring-primary-400 rounded
              ${styles.tab(isActive)}
              ${sizeStyle.tab}
              ${variant === 'pills' ? 'rounded-full first:rounded-l-full last:rounded-r-full' :
                variant === 'buttons' ? 'border-r border-warm-200 last:border-r-0 first:rounded-l-lg last:rounded-r-lg' :
                'rounded-t'}
              ${widthClass}
              ${tab.disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}`}
            onClick={() => !tab.disabled && onChange(tab.key)}
            disabled={tab.disabled}
          >
            {tab.icon && (
              <span className={`material-symbols-outlined shrink-0 ${sizeStyle.icon}`}>{tab.icon}</span>
            )}
            <span>{tab.label}</span>
            {tab.badge !== undefined && (
              <span className={`text-[10px] px-1.5 py-0.5 rounded-full shrink-0 ${
                isActive && variant === 'pills'
                  ? 'bg-white/20 text-white'
                  : 'bg-warm-200 text-warm-600'
              }`}>
                {tab.badge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export default Tabs;
