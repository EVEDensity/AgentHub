import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import MissionAdmissionControls from '../../../components/chat/MissionAdmissionControls';

it('blocks confirmation and duplicate cancellation while the pending cancellation awaits acknowledgement', () => {
  const confirm = vi.fn(); const cancel = vi.fn();
  render(<MissionAdmissionControls pending={{ id: 'pending-1', description: '需要人工确认' }} error={null} sending={false} cancelling missionId={null}
    onConfirm={confirm} onCancel={cancel} onReconnect={vi.fn()} />);
  const approve = screen.getByRole('button', { name: '确认执行' });
  const stop = screen.getByRole('button', { name: '正在取消请求…' });
  expect(approve).toBeDisabled(); expect(stop).toBeDisabled();
  fireEvent.click(approve); fireEvent.click(stop);
  expect(confirm).not.toHaveBeenCalled(); expect(cancel).not.toHaveBeenCalled();
});
