import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import OneClickDeployModal from '../../../components/chat/OneClickDeployModal';
import WorkflowModule from '../../../components/admin/WorkflowModule';
import { useWorkflowStore } from '../../../stores/workflowStore';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
afterEach(() => vi.unstubAllGlobals());

describe('unsupported product actions', () => {
  it('explains deployment is unavailable and exposes no generated endpoint or credential', () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    render(<OneClickDeployModal open sessionId="session" sessionName="Project" onClose={() => {}} />);
    expect(screen.getByRole('dialog')).toHaveTextContent('当前版本尚未接入部署服务');
    expect(screen.queryByText('生成链接')).not.toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('keeps workflow editing while disabling unsupported execution and metrics', async () => {
    useWorkflowStore.setState({ workflows: [{ id: 1, name: 'Real definition', description: '', active: true,
      isDefault: false, triggerKeywords: [], nodes: [], edges: [], version: 1, schemaVersion: 1,
      createdAt: '', updatedAt: '' }], loadWorkflows: vi.fn().mockResolvedValue(undefined) });
    const notice = vi.fn();
    const user = userEvent.setup();
    render(<WorkflowModule authHeaders={() => ({})} setNotice={notice} />);
    expect(screen.getByText('Real definition')).toBeVisible();
    expect(screen.getByRole('button', { name: '测试暂不可用' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: /执行历史/ }));
    expect(screen.getByRole('status')).toHaveTextContent('尚未接入真实任务记录');
    await user.click(screen.getByRole('button', { name: /分析概览/ }));
    expect(screen.getByRole('status')).toHaveTextContent('执行统计尚未接入');
    expect(notice).not.toHaveBeenCalled();
  });
});
