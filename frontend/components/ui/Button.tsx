'use client';

import { type JSX, type ButtonHTMLAttributes, type ReactNode } from 'react';

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

const VARIANT_STYLES: Record<ButtonVariant, string> = {
  primary: 'bg-primary-500 text-warm-50 hover:bg-primary-600 active:opacity-90',
  secondary: 'bg-transparent text-warm-600 border border-warm-300 hover:bg-warm-100 hover:border-warm-400',
  ghost: 'text-warm-600 hover:bg-warm-100',
  danger: 'bg-danger-500 text-white hover:bg-danger-600',
  outline: 'bg-transparent text-primary-500 border border-primary-400 hover:bg-primary-50',
  link: 'text-primary-500 hover:text-primary-600 bg-transparent p-0',
};

const SIZE_STYLES: Record<ButtonSize, string> = {
  xs: 'text-[10px] px-2 py-1 gap-1',
  sm: 'text-xs px-3 py-1.5 gap-1.5',
  md: 'text-sm px-4 py-2 gap-2',
  lg: 'text-base px-6 py-3 gap-2.5',
};

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
  const base = 'inline-flex items-center justify-center font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 focus-visible:ring-offset-2';
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
          className="shrink-0 inline-block border-2 border-current/30 border-t-current rounded-full animate-spin"
          style={{ width: '1em', height: '1em' }}
        />
      )}
      {!loading && icon && iconPosition === 'left' && (
        <span className="shrink-0 text-[1.1em]">{icon}</span>
      )}
      {children && <span>{children}</span>}
      {!loading && icon && iconPosition === 'right' && (
        <span className="shrink-0 text-[1.1em]">{icon}</span>
      )}
    </button>
  );
}

export default Button;
