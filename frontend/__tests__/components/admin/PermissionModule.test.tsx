import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PermissionModule from '../../../components/admin/PermissionModule';

describe('PermissionModule', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('loads permission rules from the admin API instead of showing the placeholder', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ([
        {
          id: 1,
          agentId: '*',
          toolPattern: 'file_*',
          pathPattern: '/workspace/**',
          behavior: 'ask',
          source: 'user',
          priority: 10,
          enabled: true,
          createdAt: '2026-07-19T10:00:00',
        },
        {
          id: 2,
          agentId: 'operator',
          toolPattern: 'shell',
          pathPattern: '*',
          behavior: 'deny',
          source: 'system',
          priority: 20,
          enabled: false,
          createdAt: '2026-07-19T10:05:00',
        },
      ]),
    }));
    vi.stubGlobal('fetch', fetchMock);

    render(
      <PermissionModule
        authHeaders={() => ({ Authorization: 'Bearer test' })}
        setNotice={vi.fn()}
      />
    );

    expect(await screen.findByText('权限规则中心')).toBeInTheDocument();
    expect(await screen.findByText('file_*')).toBeInTheDocument();
    expect(screen.getByText('shell')).toBeInTheDocument();
    expect(screen.queryByText('该模块已独立，等待配置项接入。')).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/admin/permissions/rules', expect.any(Object));
  });

  it('validates required tool pattern and priority range before creating', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => [] }));
    vi.stubGlobal('fetch', fetchMock);

    render(<PermissionModule authHeaders={() => ({})} setNotice={vi.fn()} />);
    await screen.findByText('\u6682\u65e0\u6743\u9650\u89c4\u5219\u3002\u53ef\u4ee5\u5148\u6dfb\u52a0\u4e00\u6761 allow / ask / deny \u89c4\u5219\u3002');

    const toolInput = screen.getByPlaceholderText('file_*');
    await user.clear(toolInput);
    await user.click(screen.getByRole('button', { name: '\u521b\u5efa' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('\u5de5\u5177\u6a21\u5f0f\u4e0d\u80fd\u4e3a\u7a7a');
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await user.type(toolInput, 'shell_*');
    const priorityInput = screen.getByRole('spinbutton');
    await user.clear(priorityInput);
    await user.type(priorityInput, '10001');
    await user.click(screen.getByRole('button', { name: '\u521b\u5efa' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('-10000 \u5230 10000');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('edits, toggles and deletes an existing rule through the API', async () => {
    const user = userEvent.setup();
    const rule = {
      id: 7,
      agentId: '*',
      toolPattern: 'file_*',
      pathPattern: '/workspace/**',
      behavior: 'ask',
      source: 'user',
      priority: 10,
      enabled: true,
      createdAt: '2026-07-19T10:00:00',
    };
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => ({
      ok: true,
      status: 200,
      json: async () => init?.method ? { status: 'ok' } : [rule],
    }));
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(<PermissionModule authHeaders={() => ({ Authorization: 'Bearer test' })} setNotice={vi.fn()} />);
    await screen.findByText('file_*');

    await user.click(screen.getByRole('button', { name: '\u7f16\u8f91\u6743\u9650\u89c4\u5219 file_*' }));
    const editTool = screen.getByRole('textbox', { name: '\u7f16\u8f91\u5de5\u5177\u6a21\u5f0f' });
    await user.clear(editTool);
    await user.type(editTool, 'shell_*');
    await user.selectOptions(screen.getByRole('combobox', { name: '\u7f16\u8f91\u6743\u9650\u52a8\u4f5c' }), 'deny');
    await user.click(screen.getByRole('button', { name: '\u4fdd\u5b58\u6743\u9650\u89c4\u5219' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/permissions/rules/7',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ tool_pattern: 'shell_*', path_pattern: '/workspace/**', behavior: 'deny', priority: 10 }),
      }),
    ));

    await user.click(screen.getByRole('button', { name: '\u542f\u7528' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/permissions/rules/7',
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ enabled: false }) }),
    ));

    await user.click(screen.getByRole('button', { name: '\u5220\u9664\u6743\u9650\u89c4\u5219 file_*' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/permissions/rules/7',
      expect.objectContaining({ method: 'DELETE' }),
    ));
  });
});
