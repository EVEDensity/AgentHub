import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import AdminActivityPanel from '../../../components/admin/AdminActivityPanel';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('admin local activity', () => {
  it('starts empty and explains the browser-only provenance without a command input', () => {
    render(<AdminActivityPanel activities={[]} onClear={vi.fn()} onCollapse={vi.fn()} />);
    expect(screen.getByRole('heading', { name: '前端活动' })).toBeVisible();
    expect(screen.getByRole('log', { name: '本地导航活动' })).toHaveTextContent('暂无前端活动');
    expect(screen.getByText(/不是执行日志、工具调用或服务端状态/)).toBeVisible();
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.queryByText(/SYS|v5\.1\.0|控制台已就绪/)).toBeNull();
  });

  it('renders provided navigation literally and only clears or collapses its view', async () => {
    const transport = vi.fn();
    vi.stubGlobal('fetch', transport); vi.stubGlobal('WebSocket', transport); vi.stubGlobal('EventSource', transport);
    vi.stubGlobal('__TAURI_INTERNALS__', { invoke: transport });
    const clear = vi.fn(); const collapse = vi.fn();
    render(<AdminActivityPanel activities={[{ id: 1, time: '09:05:00', message: '导航至: <script>bad()</script>' }]} onClear={clear} onCollapse={collapse} />);
    expect(screen.getByRole('log')).toHaveTextContent('09:05:00');
    expect(screen.getByRole('log')).toHaveTextContent('导航至: <script>bad()</script>');
    expect(document.querySelector('script')).toBeNull();
    await userEvent.keyboard('status{Enter}version{Enter}ls{Enter}');
    await userEvent.click(screen.getByTitle('清空前端活动'));
    await userEvent.click(screen.getByTitle('折叠面板'));
    expect(clear).toHaveBeenCalledOnce(); expect(collapse).toHaveBeenCalledOnce();
    expect(transport).not.toHaveBeenCalled();
  });
});
