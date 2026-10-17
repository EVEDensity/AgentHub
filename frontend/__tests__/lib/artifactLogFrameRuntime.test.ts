import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { describe, expect, it, vi } from 'vitest';

function runtime() {
  const windowListeners = new Map<string, (...args: any[]) => void>();
  const documentListeners = new Map<string, (...args: any[]) => void>();
  const parent = { postMessage: vi.fn() };
  const host = { replaceChildren: vi.fn() };
  const error = { hidden: true };
  const instances: Array<{ options: Record<string, any>; dispose: ReturnType<typeof vi.fn>; focus: ReturnType<typeof vi.fn>; write: ReturnType<typeof vi.fn>; open: ReturnType<typeof vi.fn>; scrollToTop: ReturnType<typeof vi.fn>; textarea: { readOnly: boolean; setAttribute: ReturnType<typeof vi.fn> }; keyHandler?: () => boolean; written?: () => void }> = [];
  class Terminal {
    options: Record<string, any>;
    dispose = vi.fn(); focus = vi.fn(); open = vi.fn(); scrollToTop = vi.fn();
    textarea = { readOnly: false, setAttribute: vi.fn() };
    keyHandler?: () => boolean;
    written?: () => void;
    write = vi.fn((_text: string, complete: () => void) => { this.written = complete; });
    attachCustomKeyEventHandler = (handler: () => boolean) => { this.keyHandler = handler; };
    constructor(options: Record<string, any>) { this.options = options; instances.push(this); }
  }
  const window = { location: { origin: 'https://agenthub.test' }, parent, Terminal,
    addEventListener: (type: string, listener: (...args: any[]) => void) => windowListeners.set(type, listener),
    removeEventListener: (type: string) => windowListeners.delete(type) };
  const document = { getElementById: (id: string) => id === 'terminal-host' ? host : error,
    addEventListener: (type: string, listener: (...args: any[]) => void) => documentListeners.set(type, listener),
    removeEventListener: (type: string) => documentListeners.delete(type) };
  runInNewContext(readFileSync(resolve('public/artifact-log-frame.js'), 'utf8'), { window, document });
  const receive = windowListeners.get('message')!;
  const message = (data: unknown, source: unknown = parent, origin = window.location.origin) => receive({ data, source, origin });
  const set = (text: string, channel = 'channel-1') => message({ type: 'artifact-log:set', channel, text });
  return { windowListeners, documentListeners, parent, host, error, instances, message, set, window };
}

describe('private Artifact log frame runtime', () => {
  it('creates a disabled local renderer from printable data and preserves line starts without raw source controls', () => {
    const frame = runtime(); frame.set('真实 中文\n[U+001B]]52;c;value[U+0007]');
    expect(frame.instances).toHaveLength(1);
    const terminal = frame.instances[0];
    expect(terminal.options).toMatchObject({ cols: 80, rows: 24, scrollback: 6000, disableStdin: true, screenReaderMode: true, convertEol: false, windowOptions: {}, logLevel: 'off' });
    expect(terminal.textarea.readOnly).toBe(true);
    expect(terminal.keyHandler?.()).toBe(false);
    expect(terminal.write).toHaveBeenCalledWith('真实 中文\r\n[U+001B]]52;c;value[U+0007]', expect.any(Function));
    expect(frame.parent.postMessage).not.toHaveBeenCalledWith(expect.objectContaining({ type: 'artifact-log:rendered' }), expect.anything());
    terminal.written?.();
    expect(terminal.scrollToTop).toHaveBeenCalledOnce();
    expect(frame.parent.postMessage).toHaveBeenLastCalledWith({ type: 'artifact-log:rendered', channel: 'channel-1' }, 'https://agenthub.test');
  });

  it('rejects other sources, origins and channels and never calls the renderer for controls or oversized messages', () => {
    const frame = runtime();
    const valid = { type: 'artifact-log:set', channel: 'channel-1', text: 'actual output' };
    frame.message(valid, frame.window);
    frame.message(valid, undefined, 'https://foreign.invalid');
    for (const text of ['\x1b[31mred', '\rprogress', '\t', '\0', '\x7f', '\x85', '\u00ad', '\u061c', '\u200b', '\u202e', '\u2066', '\ufeff', '\ud800', 'x'.repeat(100001), 'x'.repeat(2001), '\n'.repeat(2000)]) frame.set(text);
    expect(frame.instances).toHaveLength(0);
    frame.set('actual output');
    frame.set('wrong channel replacement', 'other');
    expect(frame.instances).toHaveLength(1);
    expect(frame.instances[0].write).toHaveBeenCalledTimes(1);
    frame.message({ type: 'artifact-log:dispose', channel: 'other' });
    expect(frame.instances[0].dispose).not.toHaveBeenCalled();
    const focus = { type: 'artifact-log:focus', channel: 'channel-1' };
    frame.message(focus, frame.window);
    frame.message(focus, undefined, 'https://foreign.invalid');
    frame.message({ ...focus, channel: 'other' });
    expect(frame.instances[0].focus).not.toHaveBeenCalled();
    frame.message(focus);
    expect(frame.instances[0].focus).toHaveBeenCalledOnce();
  });

  it('does not restart for duplicate payloads and ignores old write completion after replacement', () => {
    const frame = runtime(); frame.set('first'); frame.set('first');
    expect(frame.instances).toHaveLength(1);
    frame.set('second');
    expect(frame.instances).toHaveLength(2);
    expect(frame.instances[0].dispose).toHaveBeenCalledOnce();
    frame.instances[0].written?.();
    expect(frame.instances[0].scrollToTop).not.toHaveBeenCalled();
    frame.instances[1].written?.();
    expect(frame.instances[1].scrollToTop).toHaveBeenCalledOnce();
  });

  it('disposes the Terminal and every owned listener and ignores late messages/writes', () => {
    const frame = runtime(); frame.set('actual output');
    const terminal = frame.instances[0];
    frame.message({ type: 'artifact-log:dispose', channel: 'channel-1' });
    expect(terminal.dispose).toHaveBeenCalledOnce();
    expect(frame.windowListeners.size).toBe(0);
    expect(frame.documentListeners.size).toBe(0);
    terminal.written?.(); frame.set('late replacement');
    expect(frame.instances).toHaveLength(1);
    expect(terminal.scrollToTop).not.toHaveBeenCalled();
  });

  it('releases resources on pagehide and does not interpret paste/drop as input', () => {
    const frame = runtime(); frame.set('actual output');
    const preventDefault = vi.fn(); const stopImmediatePropagation = vi.fn();
    frame.documentListeners.get('paste')?.({ preventDefault, stopImmediatePropagation });
    frame.documentListeners.get('drop')?.({ preventDefault, stopImmediatePropagation });
    expect(preventDefault).toHaveBeenCalledTimes(2); expect(stopImmediatePropagation).toHaveBeenCalledTimes(2);
    const escape = { key: 'Escape', preventDefault: vi.fn() };
    frame.windowListeners.get('keydown')?.(escape);
    expect(frame.parent.postMessage).toHaveBeenLastCalledWith({ type: 'artifact-log:escape', channel: 'channel-1' }, 'https://agenthub.test');
    frame.windowListeners.get('pagehide')?.();
    expect(frame.instances[0].dispose).toHaveBeenCalledOnce();
  });
});
