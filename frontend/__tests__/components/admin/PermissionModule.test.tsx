import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
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
});
