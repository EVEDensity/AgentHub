'use client';

import { useState, type JSX, type InputHTMLAttributes } from 'react';

// ── Types ─────────────────────────────────────────────────────────────

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  label?: string;
  hint?: string;
  error?: string;
  icon?: string;
  iconPosition?: 'left' | 'right';
  clearable?: boolean;
  onClear?: () => void;
  containerClass?: string;
  size?: 'sm' | 'md' | 'lg';
}

// ── Size styles ───────────────────────────────────────────────────────

const SIZE_STYLES = {
  sm: 'text-xs py-1.5',
  md: 'text-sm py-2',
  lg: 'text-base py-2.5',
};

const ICON_SIZE = { sm: 'text-[12px]', md: 'text-[14px]', lg: 'text-[16px]' };

// ── Component ─────────────────────────────────────────────────────────

export function Input({
  label,
  hint,
  error,
  icon,
  iconPosition = 'left',
  clearable = false,
  onClear,
  containerClass = '',
  size = 'md',
  className = '',
  disabled,
  value,
  onChange,
  id,
  ...rest
}: InputProps): JSX.Element {
  const [focused, setFocused] = useState(false);
  const inputId = id || (label ? label.replace(/\s+/g, '-').toLowerCase() : undefined);

  const hasLeftIcon = icon && iconPosition === 'left';
  const hasRightIcon = (icon && iconPosition === 'right') || (clearable && value);

  const borderClass = error
    ? 'border-danger-300 focus:border-danger-400 focus:ring-danger-200'
    : 'border-warm-200 focus:border-primary-400 focus:ring-primary-200';

  const isFocused = focused ? 'ring-2 ring-offset-0' : '';

  return (
    <div className={`flex flex-col gap-1 ${containerClass}`}>
      {label && (
        <label htmlFor={inputId} className="text-xs font-medium text-warm-600">
          {label}
        </label>
      )}
      <div className="relative">
        {hasLeftIcon && (
          <span className={`material-symbols-outlined absolute left-2.5 top-1/2 -translate-y-1/2 text-warm-400 pointer-events-none ${ICON_SIZE[size]}`}>
            {icon}
          </span>
        )}
        <input
          id={inputId}
          className={`w-full rounded-lg bg-white transition-all duration-200 placeholder:text-warm-300 disabled:opacity-50 disabled:cursor-not-allowed disabled:bg-warm-50
            ${SIZE_STYLES[size]}
            ${hasLeftIcon ? 'pl-9' : 'pl-3'}
            ${hasRightIcon ? 'pr-9' : 'pr-3'}
            border ${borderClass} ${isFocused}
            ${className}`}
          disabled={disabled}
          value={value}
          onChange={onChange}
          onFocus={(e) => { setFocused(true); rest.onFocus?.(e); }}
          onBlur={(e) => { setFocused(false); rest.onBlur?.(e); }}
          {...rest}
        />
        {clearable && value && (
          <button
            type="button"
            className={`absolute right-2.5 top-1/2 -translate-y-1/2 text-warm-300 hover:text-warm-500 ${ICON_SIZE[size]}`}
            onClick={() => onClear?.()}
          >
            <span className="material-symbols-outlined">close</span>
          </button>
        )}
        {!clearable && icon && iconPosition === 'right' && (
          <span className={`material-symbols-outlined absolute right-2.5 top-1/2 -translate-y-1/2 text-warm-400 pointer-events-none ${ICON_SIZE[size]}`}>
            {icon}
          </span>
        )}
      </div>
      {error && <p className="text-[11px] text-danger-500">{error}</p>}
      {!error && hint && <p className="text-[11px] text-warm-400">{hint}</p>}
    </div>
  );
}

export default Input;
