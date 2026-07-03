'use client';

import { type JSX, type ButtonHTMLAttributes, type ReactNode } from 'react';

// ── Types ─────────────────────────────────────────────────────────────

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline' | 'link';
export type ButtonSize = 'xs' | 'sm' | 'md' | 'lg';

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: string;
  iconPosition?: 'left' | 'right';
  fullWidth?: boolean;
  children?: ReactNode;
}

// ── Style maps ────────────────────────────────────────────────────────

const VARIANT_STYLES: Record<ButtonVariant, string> = {
  primary: 'bg-primary-500 text-white hover:bg-primary-600 active:bg-primary-700 shadow-sm',
  secondary: 'bg-white text-warm-700 border border-warm-200 hover:bg-warm-50 active:bg-warm-100 shadow-sm',
  ghost: 'text-warm-600 hover:bg-warm-100 active:bg-warm-200',
  danger: 'bg-danger-500 text-white hover:bg-danger-600 active:bg-danger-700 shadow-sm',
  outline: 'bg-transparent text-primary-600 border border-primary-300 hover:bg-primary-50 active:bg-primary-100',
  link: 'text-primary-500 hover:text-primary-700 hover:underline bg-transparent p-0',
};

const SIZE_STYLES: Record<ButtonSize, string> = {
  xs: 'text-[10px] px-2 py-1 rounded-md gap-1',
  sm: 'text-xs px-3 py-1.5 rounded-lg gap-1.5',
  md: 'text-sm px-4 py-2 rounded-lg gap-2',
  lg: 'text-base px-6 py-3 rounded-xl gap-2.5',
};

const ICON_SIZE: Record<ButtonSize, string> = {
  xs: 'text-[12px]',
  sm: 'text-[14px]',
  md: 'text-[16px]',
  lg: 'text-[18px]',
};

// ── Component ─────────────────────────────────────────────────────────

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  icon,
  iconPosition = 'left',
  fullWidth = false,
  children,
  disabled,
  className = '',
  ...rest
}: ButtonProps): JSX.Element {
  const base = 'inline-flex items-center justify-center font-medium transition-all duration-200 ease-out active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed disabled:active:scale-100 select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 focus-visible:ring-offset-2';
  const widthClass = fullWidth ? 'w-full' : '';

  const isDisabled = disabled || loading;

  return (
    <button
      className={`${base} ${VARIANT_STYLES[variant]} ${SIZE_STYLES[size]} ${widthClass} ${className}`}
      disabled={isDisabled}
      {...rest}
    >
      {loading && (
        <span
          className={`shrink-0 inline-block border-2 border-current/30 border-t-current rounded-full animate-spin ${ICON_SIZE[size]}`}
          style={{ width: '1em', height: '1em' }}
        />
      )}
      {!loading && icon && iconPosition === 'left' && (
        <span className={`material-symbols-outlined shrink-0 ${ICON_SIZE[size]}`}>{icon}</span>
      )}
      {children && <span>{children}</span>}
      {!loading && icon && iconPosition === 'right' && (
        <span className={`material-symbols-outlined shrink-0 ${ICON_SIZE[size]}`}>{icon}</span>
      )}
    </button>
  );
}

export default Button;
