import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useRef, useState } from 'react';
import { ArtifactDialog } from '../../../components/ui/ArtifactDialog';

afterEach(cleanup);

function Example() {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  return <><button ref={trigger} onClick={() => setOpen(true)}>查看产物</button><ArtifactDialog open={open} onOpenChange={setOpen} title="已登记产物" description="只读字节，完整性不代表验收成功。" returnFocusRef={trigger}><button>内容操作</button></ArtifactDialog></>;
}

describe('ArtifactDialog', () => {
  it('announces its name and description, closes by Escape and restores its real trigger', async () => {
    const user = userEvent.setup();
    render(<Example />);
    const trigger = screen.getByRole('button', { name: '查看产物' });
    await user.click(trigger);
    const dialog = screen.getByRole('dialog', { name: '已登记产物' });
    expect(dialog).toHaveAccessibleDescription('只读字节，完整性不代表验收成功。');
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('renders no content or editor frame when closed', () => {
    render(<ArtifactDialog open={false} onOpenChange={vi.fn()} title="产物" description="只读"><iframe title="hidden" /></ArtifactDialog>);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.querySelector('iframe')).toBeNull();
  });
});
