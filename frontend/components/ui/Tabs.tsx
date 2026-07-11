'use client';

import { type JSX, type ReactNode } from 'react';

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

const VARIANT_STYLES = {
  underline: {
    container: 'gap-1 border-b border-warm-200',
    tab: (active: boolean) =>
      active
        ? 'text-primary-500 font-medium border-b-2 border-primary-500 -mb-[1px] bg-transparent'
        : 'text-warm-400 hover:text-warm-500 border-b-2 border-transparent',
  },
  pills: {
    container: 'gap-0.5',
    tab: (active: boolean) =>
      active
        ? 'bg-primary-500 text-warm-50'
        : 'text-warm-500 hover:bg-warm-100',
  },
  buttons: {
    container: 'gap-0 border border-warm-200 overflow-hidden',
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
            className={`inline-flex items-center gap-1.5 transition-colors outline-none focus-visible:ring-2 focus-visible:ring-primary-400
              ${styles.tab(isActive)}
              ${sizeStyle.tab}
              ${widthClass}
              ${tab.disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}`}
            onClick={() => !tab.disabled && onChange(tab.key)}
            disabled={tab.disabled}
          >
            {tab.icon && (
              <span className={`shrink-0 ${sizeStyle.icon}`}>{tab.icon}</span>
            )}
            <span>{tab.label}</span>
            {tab.badge !== undefined && (
              <span className={`text-[10px] px-1.5 py-0.5 shrink-0 ${
                isActive && variant === 'pills'
                  ? 'bg-white/20 text-white'
                  : 'bg-warm-200 text-warm-500'
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
