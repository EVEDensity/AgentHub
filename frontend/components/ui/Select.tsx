'use client';

import { type JSX, type SelectHTMLAttributes, type ReactNode } from 'react';

export interface SelectOption<T = string> {
  value: T;
  label: string;
  disabled?: boolean;
  description?: string;
}

export interface SelectProps<T = string> extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size' | 'children'> {
  label?: string;
  hint?: string;
  error?: string;
  options: readonly SelectOption<T>[];
  placeholder?: string;
  size?: 'sm' | 'md' | 'lg';
  containerClass?: string;
}

const SIZE_STYLES = {
  sm: 'text-xs py-1.5',
  md: 'text-sm py-2',
  lg: 'text-base py-2.5',
};

export function Select<T extends string = string>({
  label,
  hint,
  error,
  options,
  placeholder,
  size = 'md',
  containerClass = '',
  className = '',
  disabled,
  value,
  id,
  ...rest
}: SelectProps<T>): JSX.Element {
  const selectId = id || (label ? label.replace(/\s+/g, '-').toLowerCase() : undefined);

  const borderClass = error
    ? 'border-danger-300 focus:border-danger-400 focus:ring-danger-200'
    : 'border-warm-200 focus:border-primary-500 focus:ring-primary-500';

  return (
    <div className={`flex flex-col gap-1 ${containerClass}`}>
      {label && (
        <label htmlFor={selectId} className="text-xs font-medium text-warm-600">
          {label}
        </label>
      )}
      <select
        id={selectId}
        className={`w-full bg-warm-100 transition-colors focus:ring-2 focus:ring-offset-0 focus:outline-none text-warm-800
          disabled:opacity-50 disabled:cursor-not-allowed disabled:bg-warm-50
          px-3 border ${borderClass}
          ${SIZE_STYLES[size]}
          ${className}`}
        disabled={disabled}
        value={value}
        {...rest}
      >
        {placeholder && (
          <option value="" disabled>
            {placeholder}
          </option>
        )}
        {options.map((opt) => (
          <option key={String(opt.value)} value={String(opt.value)} disabled={opt.disabled}>
            {opt.label}
          </option>
        ))}
      </select>
      {error && <p className="text-[11px] text-danger-500">{error}</p>}
      {!error && hint && <p className="text-[11px] text-warm-400">{hint}</p>}
    </div>
  );
}

export default Select;
