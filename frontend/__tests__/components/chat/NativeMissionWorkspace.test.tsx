import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import NativeMissionWorkspace from '../../../components/chat/NativeMissionWorkspace';

const fixture = vi.hoisted(() => ({ chat: {} as Record<string, any>, status: {} as Record<string, any>, sessions: {} as Record<string, any>, missions: {} as Record<string, any>, options: vi.fn() }));
vi.mock('../../../hooks/useMissionChat', () => ({ useMissionChat: (options: any) => {
  fixture.options(options);
  return { ...fixture.chat, sessionId: options.nativeSessionId ?? fixture.chat.sessionId, missionId: options.resumeMissionId ?? fixture.chat.missionId };
} }));
vi.mock('../../../hooks/useMissionExecutionStatus', () => ({ useMissionExecutionStatus: () => fixture.status }));
vi.mock('../../../hooks/useMissionSessions', () => ({ useMissionSessions: () => fixture.sessions, useSessionMissions: () => fixture.missions }));
vi.mock('next/dynamic', async () => {
  const { MissionObservationThread } = await import('../../../components/chat/MissionObservationThread');
  return { default: () => MissionObservationThread };
});
const identity = { token: 'token-human', user: { id: 'alice', name: 'Alice', role: 'developer' }, onLogout: vi.fn() };
const page = () => render(<NativeMissionWorkspace {...identity} />);
async function selectWorkspace(id = 'workspace-1') {
  const selector = await screen.findByRole('combobox', { name: '工作空间' });
  await waitFor(() => expect(selector).toBeEnabled());
  await userEvent.selectOptions(selector, id);
}
const form = () => screen.getByRole('textbox', { name: '任务要求' });
const submit = () => screen.getByRole('button', { name: '提交 Mission' });
const observations = () => screen.getByRole('region', { name: 'Mission 对话观察' });

beforeEach(() => {
  fixture.options.mockClear();
  fixture.chat = { sendMission: vi.fn().mockResolvedValue({ missionId: 'mission-new' }), sessionId: undefined, missionId: null, pending: null,
    events: [], omittedEvents: 0, sending: false, cancelling: false, error: null, cancel: vi.fn().mockResolvedValue(true), confirmPending: vi.fn(), reconnect: vi.fn() };
  fixture.status = { data: null, error: null, retry: vi.fn() };
  fixture.sessions = { items: [{ id: 'session-old', workspaceId: 'workspace-1', title: 'Earlier session', status: 'active' }], loading: false, error: null, hasMore: false, limited: false, loadMore: vi.fn(), reload: vi.fn() };
  fixture.missions = { items: [{ id: 'mission-old', workspaceId: 'workspace-1', sessionId: 'session-old', title: 'Earlier mission', objectivePreview: '<img src=x onerror=bad()> old target', objectiveTruncated: true, status: 'SUCCEEDED' }], loading: false, error: null, hasMore: false, limited: false, loadMore: vi.fn(), reload: vi.fn() };
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ userId: 'alice', workspaceId: 'workspace-1', name: 'Alice', role: 'developer', isAdmin: false, canVerify: false })))));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('Native Mission workspace', () => {
  it('requires an explicitly selected real workspace and does not infer user scope', async () => {
    page();
    await screen.findByRole('option', { name: '个人工作空间 (workspace-1)' });
    expect(fixture.options).not.toHaveBeenCalled();
    expect(screen.queryByRole('textbox', { name: '任务要求' })).toBeNull();
    await selectWorkspace();
    expect(fixture.options).toHaveBeenLastCalledWith(expect.objectContaining({ workspaceId: 'workspace-1', eventLimit: 200, cacheSessionLink: false }));
    expect(screen.getByText(/旧聊天会话及没有原生工作空间归属的记录未迁移/)).toBeVisible();
  });

  it('shows catalog errors and retries without inventing a Default workspace', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response('{}', { status: 503 }));
    page();
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 503');
    expect(fixture.options).not.toHaveBeenCalled();
    expect(screen.queryByRole('option', { name: 'Default' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '刷新工作空间' }));
    await screen.findByRole('option', { name: '个人工作空间 (workspace-1)' });
    expect(fixture.options).not.toHaveBeenCalled();
  });

  it('keeps rejected drafts and adds no optimistic request or assistant row', async () => {
    fixture.chat.sendMission.mockResolvedValue(null);
    page(); await selectWorkspace();
    fireEvent.change(form(), { target: { value: '完整 中文草稿' } });
    fireEvent.click(submit());
    await waitFor(() => expect(fixture.chat.sendMission).toHaveBeenCalledWith('完整 中文草稿'));
    expect(form()).toHaveValue('完整 中文草稿');
    expect(observations()).toHaveTextContent('尚无提交或观察');
    expect(within(observations()).queryByText('完整 中文草稿')).toBeNull();
  });

  it('waits for one acknowledgement and preserves text and attachments edited during it', async () => {
    let acknowledge!: (result: { missionId: string }) => void;
    fixture.chat.sendMission.mockImplementation(() => new Promise(resolve => { acknowledge = resolve; }));
    page(); await selectWorkspace();
    fireEvent.change(form(), { target: { value: 'first request' } });
    fireEvent.click(submit());
    expect(within(observations()).queryByText('first request')).toBeNull();
    fireEvent.change(form(), { target: { value: 'next draft' } });
    fireEvent.change(screen.getByLabelText('选择附件'), { target: { files: [new File(['next'], 'next.txt')] } });
    fireEvent.click(submit());
    expect(fixture.chat.sendMission).toHaveBeenCalledTimes(1);
    await act(async () => acknowledge({ missionId: 'mission-new' }));
    expect(form()).toHaveValue('next draft');
    expect(screen.getByRole('button', { name: '移除 next.txt' })).toBeVisible();
    expect(within(observations()).getByText('first request')).toBeVisible();
    expect(fixture.options).toHaveBeenLastCalledWith(expect.objectContaining({ nativeSessionId: undefined, resumeMissionId: undefined }));
    expect(fixture.sessions.reload).toHaveBeenCalledTimes(1);
  });

  it('rejects every selected attachment before admission without losing text or bytes', async () => {
    page(); await selectWorkspace();
    fireEvent.change(form(), { target: { value: 'request with required context' } });
    fireEvent.change(screen.getByLabelText('选择附件'), { target: { files: [new File(['private'], 'context.txt')] } });
    fireEvent.click(submit());
    expect(await screen.findByRole('alert')).toHaveTextContent('当前 Mission 聊天暂不支持附件');
    expect(fixture.chat.sendMission).not.toHaveBeenCalled();
    expect(form()).toHaveValue('request with required context');
    fireEvent.click(screen.getByRole('button', { name: '移除 context.txt' }));
    fireEvent.click(submit());
    await waitFor(() => expect(fixture.chat.sendMission).toHaveBeenCalledTimes(1));
  });

  it.each([null, 'RUNNING', 'VERIFYING', 'WAITING_DECISION'])('blocks another request while current Mission is active or unknown: %s', async status => {
    fixture.chat.missionId = 'mission-new';
    fixture.status.data = status ? { missionStatus: status, workUnits: [] } : null;
    page(); await selectWorkspace();
    fireEvent.change(form(), { target: { value: 'editable next request' } });
    expect(form()).toHaveValue('editable next request');
    expect(submit()).toBeDisabled();
    fireEvent.keyDown(form(), { key: 'Enter' });
    expect(fixture.chat.sendMission).not.toHaveBeenCalled();
  });

  it('observes a selected exact native Mission with no submission and keeps history read-only', async () => {
    fixture.status.data = { missionStatus: 'SUCCEEDED', workUnits: [] };
    page(); await selectWorkspace();
    fireEvent.change(form(), { target: { value: 'old unsent draft' } });
    fireEvent.click(screen.getByRole('button', { name: /打开会话 Earlier session/ }));
    expect(form()).toHaveValue('');
    fireEvent.click(screen.getByRole('button', { name: /观察 Mission Earlier mission/ }));
    expect(fixture.options).toHaveBeenLastCalledWith(expect.objectContaining({ nativeSessionId: 'session-old', resumeMissionId: 'mission-old' }));
    expect(screen.getByText('<img src=x onerror=bad()> old target')).toBeVisible();
    expect(document.querySelector('img')).toBeNull();
    expect(screen.getByText('任务目标预览已截断。')).toBeVisible();
    fireEvent.change(form(), { target: { value: 'cannot continue from incomplete history' } });
    expect(submit()).toBeDisabled();
    expect(fixture.chat.sendMission).not.toHaveBeenCalled();
    expect(fixture.chat.cancel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '新会话' }));
    expect(form()).toHaveValue('');
    expect(screen.queryByText('<img src=x onerror=bad()> old target')).toBeNull();
    expect(fixture.chat.cancel).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('renders explicit directory errors and pagination limits instead of empty success', async () => {
    fixture.sessions.error = '会话目录 HTTP 403';
    fixture.sessions.hasMore = true;
    fixture.missions.limited = true;
    page(); await selectWorkspace();
    expect(screen.getByRole('alert')).toHaveTextContent('HTTP 403');
    fireEvent.click(screen.getByRole('button', { name: '重试读取会话' }));
    expect(fixture.sessions.reload).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '加载更多会话' }));
    expect(fixture.sessions.loadMore).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: /打开会话 Earlier session/ }));
    expect(screen.getByText(/仅显示最近读取的 200 个 Mission/)).toBeVisible();
  });

  it('clears previous scope bytes and drafts synchronously while rechecking workspace authority', async () => {
    page(); await selectWorkspace();
    fireEvent.change(form(), { target: { value: 'workspace one secret' } });
    fireEvent.click(submit());
    await screen.findByText('已登记请求 · 后续状态见上方');
    fireEvent.change(form(), { target: { value: 'workspace one private draft' } });
    fireEvent.click(screen.getByRole('button', { name: '刷新工作空间' }));
    expect(screen.queryByRole('textbox', { name: '任务要求' })).toBeNull();
    expect(screen.queryByText('workspace one secret')).toBeNull();
    expect(document.body).not.toHaveTextContent('workspace one private draft');
    expect(fixture.chat.cancel).not.toHaveBeenCalled();
    await selectWorkspace();
    expect(form()).toHaveValue('');
  });

  it('rejects another authenticated principal returned by whoami before any Mission read', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ userId: 'other-user', workspaceId: 'workspace-1' })));
    page();
    expect(await screen.findByRole('alert')).toHaveTextContent('服务端身份与当前登录用户不一致');
    expect(fixture.options).not.toHaveBeenCalled();
    expect(screen.queryByRole('option', { name: /个人工作空间/ })).toBeNull();
  });

  it('latches an uncertain admission and cannot resend its preserved draft', async () => {
    fixture.chat.admissionUncertain = true;
    page(); await selectWorkspace();
    fireEvent.change(form(), { target: { value: 'possibly committed request' } });
    expect(submit()).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('提交结果未确认，请刷新会话目录核对');
    fireEvent.keyDown(form(), { key: 'Enter' });
    expect(fixture.chat.sendMission).not.toHaveBeenCalled();
    expect(form()).toHaveValue('possibly committed request');
  });

  it('does not submit an IME Enter or Shift+Enter', async () => {
    page(); await selectWorkspace();
    fireEvent.change(form(), { target: { value: '输入 中文' } });
    fireEvent.keyDown(form(), { key: 'Enter', keyCode: 229, isComposing: true });
    fireEvent.keyDown(form(), { key: 'Enter', shiftKey: true });
    expect(fixture.chat.sendMission).not.toHaveBeenCalled();
    fireEvent.keyDown(form(), { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(fixture.chat.sendMission).toHaveBeenCalledTimes(1));
  });
});
