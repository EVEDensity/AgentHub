'use client';

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';

export interface ReadOnlyArtifactCodeProps {
  text: string;
  mediaType: string;
  artifactId: string;
  onClose?: () => void;
}

const MAX_BYTES = 262144;

function languageFor(mediaType: string): string {
  const type = mediaType.split(';')[0].trim().toLowerCase();
  if (type === 'application/json' || type.endsWith('+json')) return 'json';
  if (type === 'text/markdown') return 'markdown';
  if (type === 'text/x-python') return 'python';
  if (type === 'text/javascript' || type === 'application/javascript') return 'javascript';
  if (type === 'text/typescript') return 'typescript';
  if (type === 'text/css') return 'css';
  if (type === 'text/html') return 'html';
  return 'plaintext';
}

/** An ephemeral frame owns Monaco, its models and workers; no global loader. */
export function ReadOnlyArtifactCode({ text, mediaType, artifactId, onClose }: ReadOnlyArtifactCodeProps) {
  const frame = useRef<HTMLIFrameElement>(null);
  const channel = useId();
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const failed = state === 'error';
  const valid = !text.includes('\0') && new TextEncoder().encode(text).byteLength <= MAX_BYTES;
  const sendText = useCallback(() => {
    if (!valid) return;
    frame.current?.contentWindow?.postMessage({ type: 'artifact-code:set', channel, text, language: languageFor(mediaType) }, window.location.origin);
  }, [channel, mediaType, text, valid]);
  const currentSend = useRef(sendText);
  const currentClose = useRef(onClose);
  currentSend.current = sendText;
  currentClose.current = onClose;

  useLayoutEffect(() => {
    if (!valid || failed) return undefined;
    setState('loading');
    const currentFrame = frame.current;
    const listener = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.source !== currentFrame?.contentWindow || !event.data || typeof event.data !== 'object') return;
      if (event.data.type === 'artifact-code:ready') { currentSend.current(); return; }
      if (event.data.channel !== channel) return;
      if (event.data.type === 'artifact-code:rendered') setState('ready');
      if (event.data.type === 'artifact-code:error') setState('error');
      if (event.data.type === 'artifact-code:escape') currentClose.current?.();
    };
    window.addEventListener('message', listener);
    const timeout = window.setTimeout(() => setState((current) => current === 'loading' ? 'error' : current), 15000);
    return () => {
      clearTimeout(timeout);
      window.removeEventListener('message', listener);
      try { currentFrame?.contentWindow?.postMessage({ type: 'artifact-code:dispose', channel }, window.location.origin); } catch { /* A removed frame already owns no live browsing context. */ }
      // Frame removal also tears down this browsing context if a resource failed.
    };
  }, [channel, failed, valid]);
  useEffect(() => { if (valid && !failed) { setState('loading'); sendText(); } }, [failed, sendText, valid]);

  if (!valid) return <p role="alert" className="text-sm text-danger-700">产物不是可预览的有界 UTF-8 文本（上限 256 KiB）。</p>;
  return (
    <div className="space-y-2">
      {state === 'loading' && <p role="status" className="text-sm text-warm-600">正在加载本地只读编辑器</p>}
      {state === 'error' && <p role="alert" className="text-sm text-danger-700">本地编辑器加载失败。请关闭后重试。</p>}
      {!failed && <iframe ref={frame} src="/artifact-code-frame.html" title={`产物 ${artifactId} 的只读代码`} sandbox="allow-scripts allow-same-origin" referrerPolicy="no-referrer" className="h-[min(55vh,560px)] min-h-64 w-full rounded border border-warm-200" onLoad={sendText} />}
      {text === '' && <p className="text-sm text-warm-600">产物内容为空（0 字节）。</p>}
    </div>
  );
}

export default ReadOnlyArtifactCode;
