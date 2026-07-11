'use client';

import { useState, type JSX, type InputHTMLAttributes } from 'react';

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

const SIZE_STYLES = {
  sm: 'text-xs py-1.5',
  md: 'text-sm py-2',
  lg: 'text-base py-2.5',
};

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
    : 'border-warm-200 focus:border-primary-500 focus:ring-primary-500';

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
          <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-warm-400 pointer-events-none text-sm">
            {icon}
          </span>
        )}
        <input
          id={inputId}
          className={`w-full bg-warm-100 transition-colors placeholder:text-warm-400 disabled:opacity-50 disabled:cursor-not-allowed disabled:bg-warm-50
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
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-warm-400 hover:text-warm-500 text-sm"
            onClick={() => onClear?.()}
          >
            ✕
          </button>
        )}
        {!clearable && icon && iconPosition === 'right' && (
          <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-warm-400 pointer-events-none text-sm">
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
