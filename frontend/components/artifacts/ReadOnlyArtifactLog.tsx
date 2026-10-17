'use client';

import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { projectArtifactLog } from '../../lib/artifacts/artifactLogProjection';

/** Only the bounded printable projection enters the private terminal frame. */
export default function ReadOnlyArtifactLog({ text, artifactId, onClose }: { text: string; artifactId: string; onClose?: () => void }) {
  const projection = useMemo(() => {
    try { return projectArtifactLog(text); } catch { return null; }
  }, [text]);
  const frame = useRef<HTMLIFrameElement>(null);
  const channel = useId();
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const failed = state === 'error';
  const valid = projection !== null;
  const empty = text === '';
  const sendText = useCallback(() => {
    if (projection) frame.current?.contentWindow?.postMessage({ type: 'artifact-log:set', channel, text: projection.text }, window.location.origin);
  }, [channel, projection]);
  const currentSend = useRef(sendText);
  const currentClose = useRef(onClose);
  currentSend.current = sendText;
  currentClose.current = onClose;

  useLayoutEffect(() => {
    if (!valid || failed || empty) return;
    setState('loading');
    const ownedFrame = frame.current;
    const receive = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.source !== ownedFrame?.contentWindow || !event.data || typeof event.data !== 'object') return;
      if (event.data.type === 'artifact-log:ready') { currentSend.current(); return; }
      if (event.data.channel !== channel) return;
      if (event.data.type === 'artifact-log:rendered') setState('ready');
      if (event.data.type === 'artifact-log:error') setState('error');
      if (event.data.type === 'artifact-log:escape') currentClose.current?.();
    };
    window.addEventListener('message', receive);
    const timeout = window.setTimeout(() => setState(previous => previous === 'loading' ? 'error' : previous), 15_000);
    return () => {
      clearTimeout(timeout);
      window.removeEventListener('message', receive);
      try { ownedFrame?.contentWindow?.postMessage({ type: 'artifact-log:dispose', channel }, window.location.origin); } catch { /* Removed browsing contexts already own no runtime. */ }
    };
  }, [channel, empty, failed, valid]);
  useEffect(() => { if (valid && !failed && !empty) { setState('loading'); sendText(); } }, [empty, failed, sendText, valid]);

  if (!projection) return <p role="alert" className="text-sm text-danger-700">日志不是可显示的有界 UTF-8 文本（上限 256 KiB）。</p>;
  if (text === '') return <p className="text-sm text-warm-600">空日志产物（0 字节）。</p>;
  return <div className="space-y-2">
    <p className="text-xs text-warm-600">登记日志只读快照 · 有界纯文本视图（80 列 × 24 行）。控制符已转义，TAB 显示为空格；显示投影不等同原始字节。</p>
    <p className="text-xs text-warm-600">源文本 {projection.sourceLines} 行 · 省略 {projection.omittedLines} 行 · 截短 {projection.truncatedLines} 行 · 可见转义 {projection.escapedControls} 个控制符。仅显示有界观察，不代表任务验收通过。</p>
    {state === 'loading' && <p role="status" className="text-sm text-warm-600">正在加载本地只读日志视图</p>}
    {failed && <p role="alert" className="text-sm text-danger-700">本地日志视图加载失败。请关闭后重试。</p>}
    {!failed && <iframe ref={frame} src="/artifact-log-frame.html" title={`产物 ${artifactId} 的只读日志`} sandbox="allow-scripts allow-same-origin" referrerPolicy="no-referrer" tabIndex={0} className="h-[min(55vh,520px)] min-h-64 w-full rounded border border-warm-200" onLoad={sendText}
      onFocus={() => frame.current?.contentWindow?.postMessage({ type: 'artifact-log:focus', channel }, window.location.origin)} />}
  </div>;
}
