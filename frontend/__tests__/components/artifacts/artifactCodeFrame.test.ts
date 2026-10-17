import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { describe, expect, it, vi } from 'vitest';

function frameRuntime() {
  const handlers = new Map<string, (event: Record<string, unknown>) => void>();
  const source = readFileSync(resolve(process.cwd(), 'public/artifact-code-frame.js'), 'utf8');
  const parent = { postMessage: vi.fn() };
  const nativeTerminate = vi.fn();
  const revoke = vi.fn();
  const editorDispose = vi.fn();
  const modelDispose = vi.fn();
  const createModel = vi.fn(() => ({ dispose: modelDispose }));
  const createEditor = vi.fn(() => ({ dispose: editorDispose }));
  const require = Object.assign((_deps: string[], loaded: () => void) => {
    const defaults = { setDiagnosticsOptions: vi.fn() };
    window.monaco = { editor: { createModel, create: createEditor }, languages: { json: { jsonDefaults: defaults }, typescript: { typescriptDefaults: defaults, javascriptDefaults: defaults } } };
    loaded();
  }, { config: vi.fn() });
  const window = {
    parent, location: { origin: 'https://agenthub.test' },
    Worker: class { terminate = nativeTerminate; },
    addEventListener: (name: string, listener: (event: Record<string, unknown>) => void) => handlers.set(name, listener),
    removeEventListener: (name: string) => handlers.delete(name),
    require, monaco: undefined as unknown,
  };
  const script = { src: '', onload: () => {}, onerror: () => {} };
  const error = { hidden: true };
  const document = { getElementById: (id: string) => id === 'error' ? error : {}, createElement: () => script, head: { appendChild: vi.fn() } };
  const url = { createObjectURL: () => 'blob:owned', revokeObjectURL: revoke };
  runInNewContext(source, { window, document, URL: url, TextEncoder });
  const receive = (data: Record<string, unknown>, origin = window.location.origin, eventSource: unknown = parent) => handlers.get('message')?.({ data, origin, source: eventSource });
  return { window, script, url, handlers, parent, receive, createModel, createEditor, editorDispose, modelDispose, nativeTerminate, revoke };
}

describe('owned Monaco frame runtime', () => {
  it('loads only the local runtime and keeps CJK and HTML source as read-only model data', () => {
    const frame = frameRuntime();
    expect(frame.script.src).toBe('/monaco/0.55.1/vs/loader.js');
    frame.receive({ type: 'artifact-code:set', channel: 'owned', text: '中文<script>bad()</script>', language: 'html' });
    expect(frame.createModel).not.toHaveBeenCalled();
    frame.script.onload();
    expect(frame.createModel).toHaveBeenCalledWith('中文<script>bad()</script>', 'html');
    expect(frame.createEditor.mock.calls[0][1]).toMatchObject({ readOnly: true, domReadOnly: true, tabFocusMode: true, links: false, contextmenu: false });
  });

  it('rejects wrong origin, source, channel, binary and oversized text', () => {
    const frame = frameRuntime();
    frame.script.onload();
    const data = { type: 'artifact-code:set', channel: 'owned', text: 'safe', language: 'plaintext' };
    frame.receive(data, 'https://foreign.invalid');
    frame.receive(data, undefined, {});
    frame.receive({ ...data, text: '界'.repeat(87382) });
    frame.receive({ ...data, text: '\0' });
    expect(frame.createModel).not.toHaveBeenCalled();
    frame.receive(data);
    frame.receive({ ...data, channel: 'wrong', text: 'replacement' });
    expect(frame.createModel).toHaveBeenCalledTimes(1);
  });

  it('explicitly disposes editor, model, workers, object URLs and subscriptions', () => {
    const frame = frameRuntime();
    frame.script.onload();
    frame.receive({ type: 'artifact-code:set', channel: 'owned', text: 'code', language: 'plaintext' });
    new frame.window.Worker();
    frame.url.createObjectURL();
    frame.receive({ type: 'artifact-code:dispose', channel: 'foreign' });
    expect(frame.editorDispose).not.toHaveBeenCalled();
    frame.receive({ type: 'artifact-code:dispose', channel: 'owned' });
    expect(frame.editorDispose).toHaveBeenCalledOnce();
    expect(frame.modelDispose).toHaveBeenCalledOnce();
    expect(frame.nativeTerminate).toHaveBeenCalledOnce();
    expect(frame.revoke).toHaveBeenCalledWith('blob:owned');
    expect(frame.handlers.has('message')).toBe(false);
    expect(frame.handlers.has('keydown')).toBe(false);
    expect(frame.handlers.has('pagehide')).toBe(false);
  });

  it('still releases workers and URLs if an editor disposal hook fails', () => {
    const frame = frameRuntime();
    frame.script.onload();
    frame.receive({ type: 'artifact-code:set', channel: 'owned', text: 'code', language: 'plaintext' });
    new frame.window.Worker();
    frame.url.createObjectURL();
    frame.editorDispose.mockImplementation(() => { throw new Error('dispose failed'); });
    frame.receive({ type: 'artifact-code:dispose', channel: 'owned' });
    expect(frame.modelDispose).toHaveBeenCalledOnce();
    expect(frame.nativeTerminate).toHaveBeenCalledOnce();
    expect(frame.revoke).toHaveBeenCalledWith('blob:owned');
  });
});
