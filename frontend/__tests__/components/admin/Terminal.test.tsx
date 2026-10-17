import { StrictMode } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Terminal from '../../../components/admin/Terminal';

afterEach(() => vi.unstubAllGlobals());

describe('admin terminal availability', () => {
  it('advertises the missing execution service without a command surface or fabricated data', () => {
    render(<Terminal />);

    expect(screen.getByRole('heading', { name: '终端暂不可用' })).toBeVisible();
    expect(screen.getByRole('status', { name: '终端不可用' })).toHaveTextContent(
      '当前版本尚未接入真实终端服务，无法执行命令或读取系统、进程、文件及环境信息。',
    );
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.queryByText(/admin@agenthub|DATABASE_URL|200 OK|healthy|uptime/i)).not.toBeInTheDocument();
  });

  it('does not execute typed commands or open a transport during its lifecycle', async () => {
    const fetch = vi.fn();
    const webSocket = vi.fn();
    const eventSource = vi.fn();
    const invoke = vi.fn();
    vi.stubGlobal('fetch', fetch);
    vi.stubGlobal('WebSocket', webSocket);
    vi.stubGlobal('EventSource', eventSource);
    vi.stubGlobal('__TAURI_INTERNALS__', { invoke });
    const user = userEvent.setup();
    const view = render(<StrictMode><Terminal /></StrictMode>);
    const initialNotice = screen.getByRole('status').textContent;

    await user.keyboard('status{Enter}agents{Enter}ls{Enter}cat .env{Enter}ps{Enter}');
    await user.keyboard('curl https://example.com{Enter}git status{Enter}npm install{Enter}ping localhost{Enter}');
    view.rerender(<StrictMode><Terminal /></StrictMode>);

    expect(screen.getByRole('status')).toHaveTextContent(initialNotice ?? '');
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    view.unmount();
    expect(fetch).not.toHaveBeenCalled();
    expect(webSocket).not.toHaveBeenCalled();
    expect(eventSource).not.toHaveBeenCalled();
    expect(invoke).not.toHaveBeenCalled();
  });
});
