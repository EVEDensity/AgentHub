import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ReadOnlyArtifactLog from '../../../components/artifacts/ReadOnlyArtifactLog';

afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('read-only registered log view', () => {
  it('sends only the printable projection to a local private frame and validates its messages', () => {
    const close = vi.fn();
    render(<ReadOnlyArtifactLog text={'中文\n\x1b]52;c;secret\x07\r\u202e<script>bad()</script>'} artifactId="log-1" onClose={close} />);
    const frame = screen.getByTitle('产物 log-1 的只读日志') as HTMLIFrameElement;
    expect(frame.getAttribute('src')).toBe('/artifact-log-frame.html');
    const post = vi.spyOn(frame.contentWindow!, 'postMessage').mockImplementation(() => {});
    fireEvent.load(frame);
    const payload = post.mock.calls[0][0];
    expect(payload).toMatchObject({ type: 'artifact-log:set', text: '中文\n[U+001B]]52;c;secret[U+0007][U+000D][U+202E]<script>bad()</script>' });
    expect(post.mock.calls[0][1]).toBe(window.location.origin);
    expect(frame.tabIndex).toBe(0);
    fireEvent.focus(frame);
    expect(post).toHaveBeenLastCalledWith({ type: 'artifact-log:focus', channel: payload.channel }, window.location.origin);
    expect(screen.getByText(/显示投影不等同原始字节/)).toBeVisible();
    expect(document.querySelector('script')).toBeNull();
    for (const event of [
      { source: frame.contentWindow, origin: 'https://foreign.invalid', data: { ...payload, type: 'artifact-log:escape' } },
      { source: window, origin: window.location.origin, data: { ...payload, type: 'artifact-log:escape' } },
      { source: frame.contentWindow, origin: window.location.origin, data: { type: 'artifact-log:escape', channel: 'wrong' } },
    ]) fireEvent(window, new MessageEvent('message', event));
    expect(close).not.toHaveBeenCalled();
    fireEvent(window, new MessageEvent('message', { source: frame.contentWindow, origin: window.location.origin, data: { ...payload, type: 'artifact-log:rendered' } }));
    expect(screen.queryByRole('status')).toBeNull();
    fireEvent(window, new MessageEvent('message', { source: frame.contentWindow, origin: window.location.origin, data: { ...payload, type: 'artifact-log:escape' } }));
    expect(close).toHaveBeenCalledOnce();
  });

  it('disposes its exact channel and removes the owned frame on unmount', () => {
    const { unmount } = render(<ReadOnlyArtifactLog text="recorded output" artifactId="owned" />);
    const frame = screen.getByTitle('产物 owned 的只读日志') as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow!, 'postMessage').mockImplementation(() => {});
    fireEvent.load(frame);
    const channel = post.mock.calls[0][0].channel;
    unmount();
    expect(post).toHaveBeenLastCalledWith({ type: 'artifact-log:dispose', channel }, window.location.origin);
    expect(document.querySelector('iframe')).toBeNull();
  });

  it('updates bytes and callbacks without disposing the mounted channel', () => {
    const oldClose = vi.fn(); const close = vi.fn();
    const view = render(<ReadOnlyArtifactLog text="first" artifactId="owned" onClose={oldClose} />);
    const frame = screen.getByTitle('产物 owned 的只读日志') as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow!, 'postMessage').mockImplementation(() => {});
    fireEvent.load(frame);
    const channel = post.mock.calls[0][0].channel;
    view.rerender(<ReadOnlyArtifactLog text={'next\x1b'} artifactId="owned" onClose={close} />);
    expect(screen.getByTitle('产物 owned 的只读日志')).toBe(frame);
    expect(post.mock.calls.some(([data]) => data.type === 'artifact-log:dispose')).toBe(false);
    expect(post).toHaveBeenLastCalledWith({ type: 'artifact-log:set', channel, text: 'next[U+001B]' }, window.location.origin);
    fireEvent(window, new MessageEvent('message', { source: frame.contentWindow, origin: window.location.origin, data: { type: 'artifact-log:escape', channel } }));
    expect(oldClose).not.toHaveBeenCalled(); expect(close).toHaveBeenCalledOnce();
  });

  it('makes a load timeout terminal for this mount, even if text changes', () => {
    vi.useFakeTimers();
    const view = render(<ReadOnlyArtifactLog text="output" artifactId="slow" />);
    act(() => vi.advanceTimersByTime(15_000));
    expect(screen.getByRole('alert')).toHaveTextContent('本地日志视图加载失败');
    expect(document.querySelector('iframe')).toBeNull();
    view.rerender(<ReadOnlyArtifactLog text="late content" artifactId="slow" />);
    expect(screen.getByRole('alert')).toHaveTextContent('本地日志视图加载失败');
    expect(document.querySelector('iframe')).toBeNull();
  });

  it.each(['\ud800', '界'.repeat(87382)])('rejects malformed or excessive input before mounting a runtime', text => {
    render(<ReadOnlyArtifactLog text={text} artifactId="invalid" />);
    expect(screen.getByRole('alert')).toHaveTextContent('256 KiB');
    expect(document.querySelector('iframe')).toBeNull();
  });

  it('shows empty content and explicit projection omissions honestly', () => {
    const view = render(<ReadOnlyArtifactLog text="" artifactId="empty" />);
    expect(screen.getByText('空日志产物（0 字节）。')).toBeVisible();
    expect(document.querySelector('iframe')).toBeNull();
    view.rerender(<ReadOnlyArtifactLog text={Array.from({ length: 2002 }, (_, index) => `line ${index}`).join('\n')} artifactId="bounded" />);
    expect(screen.getByText(/源文本 2002 行 · 省略 2 行/)).toBeVisible();
  });
});
