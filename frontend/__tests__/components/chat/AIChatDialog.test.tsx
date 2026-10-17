import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import AIChatDialog from '../../../components/chat/AIChatDialog';
import { useAuthStore } from '../../../stores/authStore';
import { useWorkspaceStore } from '../../../stores/workspaceStore';
import { useAdminStore } from '../../../stores/adminStore';

const fixture = vi.hoisted(() => ({ chat: {} as Record<string, any>, status: {} as Record<string, any>, options: vi.fn() }));
vi.mock('../../../hooks/useMissionChat', () => ({ useMissionChat: (options: unknown) => { fixture.options(options); return fixture.chat; } }));
vi.mock('../../../hooks/useMissionExecutionStatus', () => ({ useMissionExecutionStatus: () => fixture.status }));
vi.mock('../../../components/chat/MissionExecutionBanner', () => ({ default: () => <div>服务端状态由共享 Banner 呈现</div> }));
vi.mock('next/dynamic', async () => {
  const { MissionObservationThread } = await import('../../../components/chat/MissionObservationThread');
  return { default: () => MissionObservationThread };
});

function Example() {
  const [open, setOpen] = useState(false);
  return <><button onClick={() => setOpen(true)}>打开助手</button><AIChatDialog open={open} onClose={() => setOpen(false)} /></>;
}

beforeEach(() => {
  fixture.options.mockClear();
  fixture.chat = {
    sendMission: vi.fn().mockResolvedValue({ missionId: 'mission-1' }), cancel: vi.fn().mockResolvedValue(true), confirmPending: vi.fn(), reconnect: vi.fn(),
    sending: false, pending: null, error: null, missionId: null, events: [], omittedEvents: 0,
  };
  fixture.status = { data: null, error: null, retry: vi.fn() };
  useAuthStore.setState({ token: 'token-human', user: { id: 'human', name: 'Ada', role: 'developer' } });
  useWorkspaceStore.setState({ currentWorkspaceId: 'workspace-1' });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('Mission admin assistant', () => {
  it('retains a failed draft and never generates a local assistant answer or success', async () => {
    fixture.chat.sendMission.mockResolvedValue(null);
    render(<AIChatDialog open onClose={vi.fn()} />);
    const input = screen.getByRole('textbox', { name: '任务要求' });
    fireEvent.change(input, { target: { value: '请分析 中文' } });
    fireEvent.click(screen.getByRole('button', { name: '提交 Mission' }));
    await waitFor(() => expect(fixture.chat.sendMission).toHaveBeenCalledWith('请分析 中文'));
    expect(input).toHaveValue('请分析 中文');
    expect(within(screen.getByLabelText('Mission 对话观察')).getByText(/尚无提交或观察/)).toBeVisible();
    expect(screen.queryByText(/Claude|GPT-4o|请求已提交|验收通过/)).toBeNull();
  });

  it('adds a user row only after acknowledgement and preserves edits made during admission', async () => {
    let acknowledge!: (value: { missionId: string }) => void;
    fixture.chat.sendMission.mockImplementation(() => new Promise(resolve => { acknowledge = resolve; }));
    render(<AIChatDialog open onClose={vi.fn()} />);
    const input = screen.getByRole('textbox', { name: '任务要求' });
    fireEvent.change(input, { target: { value: 'first request' } });
    fireEvent.click(screen.getByRole('button', { name: '提交 Mission' }));
    expect(within(screen.getByLabelText('Mission 对话观察')).queryByText('first request')).toBeNull();
    fireEvent.change(input, { target: { value: '下一条草稿' } });
    fireEvent.click(screen.getByRole('button', { name: '提交 Mission' }));
    expect(fixture.chat.sendMission).toHaveBeenCalledTimes(1);
    await act(async () => acknowledge({ missionId: 'mission-1' }));
    expect(within(screen.getByLabelText('Mission 对话观察')).getByText('first request')).toBeVisible();
    expect(input).toHaveValue('下一条草稿');
  });

  it('renders actual observation IDs once as escaped text and never as executable tools or HTML', async () => {
    fixture.chat.missionId = 'mission-1';
    fixture.chat.events = [{ eventId: 'ledger-1', eventType: 'harness.tool.completed', missionId: 'mission-1', type: 'tool_result', timestamp: '', content: '<img src=x onerror=bad()> PASS' }];
    const { rerender } = render(<AIChatDialog open onClose={vi.fn()} />);
    await expect(screen.findByText('<img src=x onerror=bad()> PASS')).resolves.toBeVisible();
    expect(screen.getByText('观察 · harness.tool.completed')).toBeVisible();
    expect(document.querySelector('img')).toBeNull();
    expect(screen.queryByRole('button', { name: /执行|批准工具|重试工具/ })).toBeNull();
    fixture.chat.events = [...fixture.chat.events];
    rerender(<AIChatDialog open onClose={vi.fn()} />);
    expect(screen.getAllByText('<img src=x onerror=bad()> PASS')).toHaveLength(1);
  });

  it('pauses on close without cancellation, unmounts the runtime and restores its trigger', async () => {
    const user = userEvent.setup();
    render(<Example />);
    const trigger = screen.getByRole('button', { name: '打开助手' });
    await user.click(trigger);
    const input = screen.getByRole('textbox', { name: '任务要求' });
    fireEvent.change(input, { target: { value: '保留草稿' } });
    await user.click(screen.getByRole('button', { name: '关闭 Mission 助手' }));
    expect(screen.queryByLabelText('Mission 对话观察')).toBeNull();
    expect(fixture.options).toHaveBeenLastCalledWith(expect.objectContaining({ enabled: false, eventLimit: 200 }));
    expect(fixture.chat.cancel).not.toHaveBeenCalled();
    expect(trigger).toHaveFocus();
    await user.click(trigger);
    expect(screen.getByRole('textbox', { name: '任务要求' })).toHaveValue('保留草稿');
  });

  it.each(['workspace', 'token', 'user', 'role'])('clears old bytes synchronously when %s changes', async change => {
    fixture.chat.events = [];
    render(<AIChatDialog open onClose={vi.fn()} />);
    fireEvent.change(screen.getByRole('textbox', { name: '任务要求' }), { target: { value: '旧范围私密草稿' } });
    act(() => {
      if (change === 'workspace') useWorkspaceStore.setState({ currentWorkspaceId: 'workspace-2' });
      if (change === 'token') useAuthStore.setState({ token: 'new-token' });
      if (change === 'user') useAuthStore.setState({ user: { id: 'other', name: 'Other', role: 'developer' } });
      if (change === 'role') useAuthStore.setState({ user: { id: 'human', name: 'Ada', role: 'viewer' } });
    });
    expect(screen.getByRole('textbox', { name: '任务要求' })).toHaveValue('');
    expect(document.body).not.toHaveTextContent('旧范围私密草稿');
  });

  it.each(['RUNNING', 'VERIFYING', 'WAITING_DECISION', 'UNKNOWN'])('blocks another submit while status is %s without blocking draft edits', status => {
    fixture.chat.missionId = 'mission-1';
    fixture.status.data = status === 'UNKNOWN' ? null : { missionStatus: status };
    render(<AIChatDialog open onClose={vi.fn()} />);
    fireEvent.change(screen.getByRole('textbox', { name: '任务要求' }), { target: { value: '下一任务' } });
    expect(screen.getByRole('button', { name: '提交 Mission' })).toBeDisabled();
    expect(screen.getByRole('textbox', { name: '任务要求' })).toHaveValue('下一任务');
  });

  it('opens the existing Decision inbox without creating a resolution command', async () => {
    fixture.chat.missionId = 'mission-1';
    fixture.status.data = { missionStatus: 'WAITING_DECISION' };
    const close = vi.fn();
    render(<AIChatDialog open onClose={close} />);
    fireEvent.click(screen.getByRole('button', { name: '打开决策收件箱' }));
    expect(useAdminStore.getState().activeMenu).toBe('决策收件箱');
    expect(close).toHaveBeenCalledOnce();
    expect(fixture.chat.cancel).not.toHaveBeenCalled();
  });

  it('does not submit IME Enter or Shift+Enter', () => {
    render(<AIChatDialog open onClose={vi.fn()} />);
    const input = screen.getByRole('textbox', { name: '任务要求' });
    fireEvent.change(input, { target: { value: '中文组合输入' } });
    fireEvent.keyDown(input, { key: 'Enter', keyCode: 229, isComposing: true });
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    expect(fixture.chat.sendMission).not.toHaveBeenCalled();
  });

  it('does not mount transport or runtime without authentication', () => {
    useAuthStore.setState({ token: '' });
    render(<AIChatDialog open onClose={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent('请先登录并选择工作空间');
    expect(fixture.options).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('Mission 对话观察')).toBeNull();
  });
});
