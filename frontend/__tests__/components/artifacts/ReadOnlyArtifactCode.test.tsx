import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ReadOnlyArtifactCode } from '../../../components/artifacts/ReadOnlyArtifactCode';

afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('ReadOnlyArtifactCode', () => {
  it('uses a local lazy frame, sends CJK text as data, and rejects unrelated messages', () => {
    const close = vi.fn();
    render(<ReadOnlyArtifactCode text={'中文 <script>bad()</script>'} mediaType="application/json" artifactId="registered-1" onClose={close} />);
    const frame = screen.getByTitle('产物 registered-1 的只读代码') as HTMLIFrameElement;
    expect(frame.getAttribute('src')).toBe('/artifact-code-frame.html');
    expect(document.querySelector('script')).toBeNull();
    const post = vi.spyOn(frame.contentWindow!, 'postMessage').mockImplementation(() => {});
    fireEvent.load(frame);
    const payload = post.mock.calls[0][0];
    expect(payload).toMatchObject({ type: 'artifact-code:set', text: '中文 <script>bad()</script>', language: 'json' });
    expect(post.mock.calls[0][1]).toBe(window.location.origin);
    fireEvent(window, new MessageEvent('message', { source: frame.contentWindow, origin: 'https://foreign.invalid', data: { ...payload, type: 'artifact-code:escape' } }));
    fireEvent(window, new MessageEvent('message', { source: window, origin: window.location.origin, data: { ...payload, type: 'artifact-code:escape' } }));
    fireEvent(window, new MessageEvent('message', { source: frame.contentWindow, origin: window.location.origin, data: { type: 'artifact-code:escape', channel: 'wrong' } }));
    expect(close).not.toHaveBeenCalled();
    fireEvent(window, new MessageEvent('message', { source: frame.contentWindow, origin: window.location.origin, data: { ...payload, type: 'artifact-code:rendered' } }));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    fireEvent(window, new MessageEvent('message', { source: frame.contentWindow, origin: window.location.origin, data: { ...payload, type: 'artifact-code:escape' } }));
    expect(close).toHaveBeenCalledOnce();
  });

  it('disposes the owned frame channel on unmount', () => {
    const { unmount } = render(<ReadOnlyArtifactCode text="" mediaType="text/plain" artifactId="empty" />);
    const frame = screen.getByTitle('产物 empty 的只读代码') as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow!, 'postMessage').mockImplementation(() => {});
    fireEvent.load(frame);
    const channel = post.mock.calls[0][0].channel;
    expect(screen.getByText('产物内容为空（0 字节）。')).toBeVisible();
    unmount();
    expect(post).toHaveBeenLastCalledWith({ type: 'artifact-code:dispose', channel }, window.location.origin);
    expect(document.querySelector('iframe')).toBeNull();
  });

  it.each(['\0', '界'.repeat(87382)])('rejects unsafe or oversized content before creating a frame', (text) => {
    render(<ReadOnlyArtifactCode text={text} mediaType="text/plain" artifactId="bounded" />);
    expect(screen.getByRole('alert')).toHaveTextContent('256 KiB');
    expect(document.querySelector('iframe')).toBeNull();
  });

  it('shows an honest resource error after its bounded load deadline', () => {
    vi.useFakeTimers();
    render(<ReadOnlyArtifactCode text="code" mediaType="text/plain" artifactId="slow" />);
    fireEvent(window, new Event('unrelated'));
    act(() => vi.advanceTimersByTime(15000));
    expect(screen.getByRole('alert')).toHaveTextContent('本地编辑器加载失败');
    expect(document.querySelector('iframe')).toBeNull();
  });

  it('updates text and callback without disposing the current runtime', () => {
    const previousClose = vi.fn();
    const close = vi.fn();
    const { rerender } = render(<ReadOnlyArtifactCode text="old" mediaType="text/plain" artifactId="registered" onClose={previousClose} />);
    const frame = screen.getByTitle('产物 registered 的只读代码') as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow!, 'postMessage').mockImplementation(() => {});
    fireEvent.load(frame);
    const channel = post.mock.calls[0][0].channel;
    rerender(<ReadOnlyArtifactCode text="更新文本" mediaType="application/json" artifactId="registered" onClose={close} />);
    expect(screen.getByTitle('产物 registered 的只读代码')).toBe(frame);
    expect(post.mock.calls.some(([data]) => data.type === 'artifact-code:dispose')).toBe(false);
    expect(post).toHaveBeenLastCalledWith({ type: 'artifact-code:set', channel, text: '更新文本', language: 'json' }, window.location.origin);
    fireEvent(window, new MessageEvent('message', { source: frame.contentWindow, origin: window.location.origin, data: { type: 'artifact-code:escape', channel } }));
    expect(previousClose).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledOnce();
  });
});
