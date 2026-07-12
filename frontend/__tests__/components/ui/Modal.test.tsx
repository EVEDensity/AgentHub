import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Modal } from '../../../components/ui/Modal';

describe('Modal', () => {
  it('renders nothing when open=false', () => {
    const { container } = render(
      <Modal open={false} onClose={vi.fn()}>
        <p>Hidden content</p>
      </Modal>
    );
    expect(container.innerHTML).toBe('');
  });

  it('renders children when open=true', () => {
    render(
      <Modal open onClose={vi.fn()}>
        <p>Modal content</p>
      </Modal>
    );
    expect(screen.getByText('Modal content')).toBeInTheDocument();
  });

  it('renders title when provided', () => {
    render(
      <Modal open onClose={vi.fn()} title="Edit Profile">
        <p>Body</p>
      </Modal>
    );
    expect(screen.getByText('Edit Profile')).toBeInTheDocument();
  });

  it('renders footer when provided', () => {
    render(
      <Modal
        open
        onClose={vi.fn()}
        title="Confirm"
        footer={<button>Save</button>}
      >
        <p>Body</p>
      </Modal>
    );
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
  });

  it('calls onClose when backdrop is clicked (closeOnBackdrop=true by default)', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose}>
        <p>Body</p>
      </Modal>
    );
    // Click the backdrop (the outermost div)
    const backdrop = screen.getByText('Body').closest('.fixed.inset-0');
    await user.click(backdrop!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does NOT call onClose when backdrop is clicked and closeOnBackdrop=false', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} closeOnBackdrop={false}>
        <p>Body</p>
      </Modal>
    );
    const backdrop = screen.getByText('Body').closest('.fixed.inset-0');
    await user.click(backdrop!);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('calls onClose when close button is clicked', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Test">
        <p>Body</p>
      </Modal>
    );
    await user.click(screen.getByLabelText('Close modal'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose on Escape key when closeOnEsc=true', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose}>
        <p>Body</p>
      </Modal>
    );
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does NOT call onClose on Escape when closeOnEsc=false', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} closeOnEsc={false}>
        <p>Body</p>
      </Modal>
    );
    await user.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('stops propagation when modal content is clicked', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose}>
        <p>Click inside</p>
      </Modal>
    );
    await user.click(screen.getByText('Click inside'));
    expect(onClose).not.toHaveBeenCalled();
  });
});
