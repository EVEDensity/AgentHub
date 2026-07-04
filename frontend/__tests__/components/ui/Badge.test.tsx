import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Badge } from '../../../components/ui/Badge';

describe('Badge', () => {
  it('renders children text', () => {
    render(<Badge>Active</Badge>);
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it.each(['default', 'success', 'warning', 'danger', 'info', 'primary', 'outline'] as const)(
    'renders variant %s without crash', (variant) => {
      render(<Badge variant={variant}>{variant}</Badge>);
      expect(screen.getByText(variant)).toBeInTheDocument();
    }
  );

  it.each(['xs', 'sm', 'md'] as const)(
    'renders size %s without crash', (size) => {
      render(<Badge size={size}>{size}</Badge>);
      expect(screen.getByText(size)).toBeInTheDocument();
    }
  );

  it('shows a dot when dot=true', () => {
    const { container } = render(<Badge dot>Online</Badge>);
    const dot = container.querySelector('.w-1\\.5.h-1\\.5.rounded-full');
    expect(dot).toBeInTheDocument();
  });

  it('shows a remove button when removable=true', () => {
    render(<Badge removable onRemove={vi.fn()}>Tag</Badge>);
    const removeBtn = screen.getByRole('button');
    expect(removeBtn).toBeInTheDocument();
  });

  it('calls onRemove when remove button is clicked', async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    render(<Badge removable onRemove={onRemove}>Tag</Badge>);
    await user.click(screen.getByRole('button'));
    expect(onRemove).toHaveBeenCalledTimes(1);
  });

  it('passes extra className', () => {
    const { container } = render(<Badge className="extra-class">Tag</Badge>);
    expect(container.firstChild).toHaveClass('extra-class');
  });
});
