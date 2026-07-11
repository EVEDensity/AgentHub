import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Button } from '../../../components/ui/Button';

describe('Button', () => {
  // ── Render smoke tests ──────────────────────────────────────────

  it('renders children text', () => {
    render(<Button>Click me</Button>);
    expect(screen.getByRole('button', { name: /click me/i })).toBeInTheDocument();
  });

  it('renders with an icon (left by default)', () => {
    render(<Button icon="send">Send</Button>);
    // Icon renders as a plain span (no material-symbols-outlined since Lucide migration)
    const icon = screen.getByText('send', { selector: 'span' });
    expect(icon).toBeInTheDocument();
  });

  it('renders icon on the right when iconPosition="right"', () => {
    const { container } = render(<Button icon="arrow_forward" iconPosition="right">Next</Button>);
    const button = container.querySelector('button');
    const spans = button?.querySelectorAll('span');
    // First span should be children, last span should be right icon
    const lastSpan = spans?.[spans?.length - 1];
    expect(lastSpan?.textContent).toBe('arrow_forward');
  });

  // ── Variants ────────────────────────────────────────────────────

  it.each(['primary', 'secondary', 'ghost', 'danger', 'outline', 'link'] as const)(
    'renders variant %s without crash', (variant) => {
      render(<Button variant={variant}>{variant}</Button>);
      expect(screen.getByRole('button')).toBeInTheDocument();
    }
  );

  // ── Sizes ───────────────────────────────────────────────────────

  it.each(['xs', 'sm', 'md', 'lg'] as const)(
    'renders size %s without crash', (size) => {
      render(<Button size={size}>{size}</Button>);
      expect(screen.getByRole('button')).toBeInTheDocument();
    }
  );

  // ── Disabled/Loading state ──────────────────────────────────────

  it('sets disabled attribute when disabled', () => {
    render(<Button disabled>Disabled</Button>);
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('shows spinner and is disabled when loading', () => {
    render(<Button loading>Loading</Button>);
    const btn = screen.getByRole('button');
    expect(btn).toBeDisabled();
    const spinner = btn.querySelector('.animate-spin');
    expect(spinner).toBeInTheDocument();
  });

  it('hides icon when loading (spinner takes precedence)', () => {
    render(<Button icon="send" loading>Send</Button>);
    const btn = screen.getByRole('button');
    const spinner = btn.querySelector('.animate-spin');
    expect(spinner).toBeInTheDocument();
    // Icon should not be rendered (spinner takes precedence)
    expect(screen.queryByText('send')).toBeNull();
  });

  // ── Interaction ─────────────────────────────────────────────────

  it('calls onClick handler when clicked', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Click</Button>);
    await user.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('does NOT call onClick when disabled', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<Button disabled onClick={onClick}>Click</Button>);
    await user.click(screen.getByRole('button'));
    expect(onClick).not.toHaveBeenCalled();
  });

  it('does NOT call onClick when loading', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<Button loading onClick={onClick}>Click</Button>);
    await user.click(screen.getByRole('button'));
    expect(onClick).not.toHaveBeenCalled();
  });

  // ── fullWidth ───────────────────────────────────────────────────

  it('applies fullWidth class when fullWidth=true', () => {
    const { container } = render(<Button fullWidth>Full</Button>);
    expect(container.querySelector('button')?.className).toContain('w-full');
  });
});
