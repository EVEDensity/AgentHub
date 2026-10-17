import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AdminLayout from '../../../app/admin/layout';
import { useAdminStore, type MenuItem } from '../../../stores/adminStore';
import { useAgentStore } from '../../../stores/agentStore';
import { useAuthStore } from '../../../stores/authStore';

const navigation = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => navigation }));
vi.mock('../../../components/admin/WorkspaceSelector', () => ({ WorkspaceSelector: () => <button aria-label="选择工作空间">Selected workspace</button> }));
vi.mock('../../../components/admin/AdminSidebar', () => ({ default: ({ onMenuClick }: { onMenuClick: (value: MenuItem) => void }) => <nav aria-label="测试导航">
  <button onClick={() => onMenuClick('Mission Control')}>Mission Control</button>
  <button onClick={() => onMenuClick('决策收件箱')}>决策收件箱</button>
</nav> }));
vi.mock('../../../components/admin/CommandPalette', () => ({ default: () => null }));
vi.mock('../../../components/chat/AIChatDialog', () => ({ default: ({ open, onClose }: { open: boolean; onClose: () => void }) => open
  ? <section role="dialog" aria-label="测试聊天"><button onClick={onClose}>关闭测试聊天</button></section> : null }));
vi.mock('../../../components/common/ResizableDivider', () => ({ default: () => null }));

const originalAuth = useAuthStore.getState();
const originalAdmin = useAdminStore.getState();
const originalAgents = useAgentStore.getState();

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal('innerWidth', 1800);
  useAuthStore.setState({ user: { id: 'header-human', name: 'Header human', role: 'developer' } });
  useAdminStore.setState({ activeMenu: '服务商' });
  useAgentStore.setState({ defaultChatAgent: 'Orchestrator' });
});
afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); navigation.push.mockClear();
  useAuthStore.setState(originalAuth, true);
  useAdminStore.setState(originalAdmin, true);
  useAgentStore.setState(originalAgents, true);
  localStorage.clear();
});

function expectUnconnectedMetrics() {
  const header = screen.getByRole('banner', { name: '管理页头部' });
  expect(screen.getByRole('region', { name: '执行指标状态' })).toHaveTextContent('运行指标尚未接入：实际执行模型、Token 用量和上下文占用');
  expect(within(header).queryByTitle('当前模型')).toBeNull();
  expect(within(header).queryByTitle('Token 用量 (今日)')).toBeNull();
  expect(within(header).queryByTitle('上下文窗口填充率')).toBeNull();
  expect(within(header).queryByText(/Claude Opus 4\.8|Orchestrator|12\.4K|200K|18%/)).toBeNull();
  expect(within(header).queryByRole('progressbar')).toBeNull();
  expect(header.querySelector('[style*="18%"]')).toBeNull();
  return header;
}

describe('admin header execution-metric boundaries', () => {
  it('has no execution metrics without a task and retains workspace, user and chat navigation', () => {
    const transport = vi.fn(); vi.stubGlobal('fetch', transport);
    render(<AdminLayout><p>尚未选择任务</p></AdminLayout>);
    const header = expectUnconnectedMetrics();
    expect(within(header).getByRole('button', { name: '选择工作空间' })).toBeVisible();
    expect(within(header).getByText('Header human')).toBeVisible();
    expect(within(header).getByText('服务商', { exact: true })).toBeVisible();
    expect(within(header).getByRole('link', { name: '返回' })).toHaveAttribute('href', '/');
    fireEvent.click(screen.getByRole('button', { name: '关闭测试聊天' }));
    expect(screen.queryByRole('dialog', { name: '测试聊天' })).toBeNull();
    fireEvent.click(screen.getByTitle('AI 对话 (Ctrl+J)'));
    expect(screen.getByRole('dialog', { name: '测试聊天' })).toBeVisible();
    expect(transport).not.toHaveBeenCalled();
  });

  it('never converts changing or empty default Agent configuration into an execution model', () => {
    const view = render(<AdminLayout><p>任务观察页面 A</p></AdminLayout>);
    for (const agent of ['configured-agent-a', 'Claude Opus 4.8', '']) {
      act(() => useAgentStore.setState({ defaultChatAgent: agent }));
      view.rerender(<AdminLayout><p>任务观察页面 B</p></AdminLayout>);
      const header = expectUnconnectedMetrics();
      if (agent) expect(within(header).queryByText(agent, { exact: true })).toBeNull();
      expect(screen.getByText('任务观察页面 B')).toBeVisible();
    }
  });

  it.each(['http', 'network'] as const)('keeps metrics unconnected during a %s configuration failure and preserves menu changes', async failure => {
    const transport = failure === 'http'
      ? vi.fn().mockResolvedValue({ ok: false, status: 503 })
      : vi.fn().mockRejectedValue(new Error('Configuration unavailable'));
    vi.stubGlobal('fetch', transport);
    useAgentStore.setState({ defaultChatAgent: 'last-known-configured-agent' });
    const view = render(<AdminLayout><p role="alert">配置读取失败</p></AdminLayout>);
    expect(transport).not.toHaveBeenCalled();
    if (failure === 'http') await useAgentStore.getState().refresh();
    else await expect(useAgentStore.getState().refresh()).rejects.toThrow('Configuration unavailable');
    expect(transport).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole('button', { name: 'Mission Control', exact: true }));
    expect(navigation.push).toHaveBeenLastCalledWith('/admin?menu=Mission%20Control');
    expect(within(expectUnconnectedMetrics()).getByText('Mission Control', { exact: true })).toBeVisible();
    view.rerender(<AdminLayout><p>已选择另一个任务观察页面</p></AdminLayout>);
    fireEvent.click(screen.getByRole('button', { name: '决策收件箱', exact: true }));
    expect(navigation.push).toHaveBeenLastCalledWith('/admin?menu=%E5%86%B3%E7%AD%96%E6%94%B6%E4%BB%B6%E7%AE%B1');
    expect(within(expectUnconnectedMetrics()).getByText('决策收件箱', { exact: true })).toBeVisible();
    expect(screen.getByText('已选择另一个任务观察页面')).toBeVisible();
    expect(within(screen.getByRole('banner', { name: '管理页头部' })).queryByText('last-known-configured-agent')).toBeNull();
  });
});
