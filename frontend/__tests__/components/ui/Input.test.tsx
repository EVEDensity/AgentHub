import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Input } from '../../../components/ui/Input';

describe('Input', () => {
  it('renders an input element', () => {
    render(<Input />);
    const input = screen.getByRole('textbox');
    expect(input).toBeInTheDocument();
    expect(input.tagName).toBe('INPUT');
  });

  it('renders a label when provided', () => {
    render(<Input label="Email" />);
    expect(screen.getByText('Email')).toBeInTheDocument();
  });

  it('associates label with input via htmlFor', () => {
    render(<Input label="Email" />);
    const label = screen.getByText('Email');
    const input = screen.getByRole('textbox');
    expect(label).toHaveAttribute('for', input.id);
  });

  it('shows hint text when no error', () => {
    render(<Input hint="Enter your email address" />);
    expect(screen.getByText('Enter your email address')).toBeInTheDocument();
  });

  it('shows error text (and hides hint)', () => {
    render(<Input hint="Hint" error="Required field" />);
    expect(screen.getByText('Required field')).toBeInTheDocument();
    expect(screen.queryByText('Hint')).toBeNull();
  });

  it('renders an icon on the left', () => {
    render(<Input icon="mail" />);
    // Icon renders as a plain span (no material-symbols-outlined since Lucide migration)
    const icon = screen.getByText('mail', { selector: 'span' });
    expect(icon).toBeInTheDocument();
  });

  it('calls onChange when typing', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Input onChange={onChange} />);
    await user.type(screen.getByRole('textbox'), 'hello');
    expect(onChange).toHaveBeenCalledTimes(5);
  });

  it('shows clear button when clearable and has value', () => {
    render(<Input clearable value="text" onChange={vi.fn()} />);
    // The clear button has a close icon
    const clearBtn = document.querySelector('button[type="button"]');
    expect(clearBtn).toBeInTheDocument();
  });

  it('calls onClear when clear button is clicked', async () => {
    const user = userEvent.setup();
    const onClear = vi.fn();
    render(<Input clearable value="text" onChange={vi.fn()} onClear={onClear} />);
    const clearBtn = document.querySelector('button[type="button"]');
    await user.click(clearBtn!);
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it('is disabled when disabled prop is true', () => {
    render(<Input disabled />);
    expect(screen.getByRole('textbox')).toBeDisabled();
  });

  it('sets placeholder text', () => {
    render(<Input placeholder="Search..." />);
    expect(screen.getByPlaceholderText('Search...')).toBeInTheDocument();
  });

  it('renders with size variants without crash', () => {
    const { rerender } = render(<Input size="sm" />);
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    rerender(<Input size="lg" />);
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });
});
