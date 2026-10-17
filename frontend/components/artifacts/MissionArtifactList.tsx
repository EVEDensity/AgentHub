'use client';

import { useEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { ArtifactDialog } from '../ui/ArtifactDialog';
import {
  readArtifactPreview, registeredArtifact, previewUnavailable,
  type ArtifactPreview, type RegisteredArtifact,
} from '../../lib/artifacts/missionArtifactPreview';

const ReadOnlyArtifactCode = dynamic(() => import('./ReadOnlyArtifactCode'), {
  ssr: false, loading: () => <p role="status">正在加载本地代码视图</p>,
});
const ReadOnlyArtifactLog = dynamic(() => import('./ReadOnlyArtifactLog'), {
  ssr: false, loading: () => <p role="status">正在加载本地日志视图</p>,
});

type PreviewState = { key: string; status: 'loading' | 'error'; error?: string }
  | { key: string; status: 'ready'; preview: ArtifactPreview };

interface Props {
  missionId: string;
  items: Record<string, unknown>[];
  authHeaders: () => Record<string, string>;
}

/** Parent remounts this projection when workspace, identity, Mission or detail revision changes. */
export function MissionArtifactList({ missionId, items, authHeaders }: Props) {
  const [selected, setSelected] = useState<RegisteredArtifact | null>(null);
  const [state, setState] = useState<PreviewState | null>(null);
  const [revision, setRevision] = useState(0);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const sequence = useRef(0);
  const key = selected ? JSON.stringify([missionId, selected.id, selected.workUnitId, selected.attempt, selected.digest, revision]) : '';

  useEffect(() => {
    const current = ++sequence.current;
    if (!selected) { setState(null); return; }
    const controller = new AbortController();
    let expired = false;
    const timer = setTimeout(() => {
      expired = true;
      if (sequence.current === current) setState({ key, status: 'error', error: '产物读取超时，请重试' });
      controller.abort();
    }, 15_000);
    setState({ key, status: 'loading' });
    void readArtifactPreview(selected, authHeaders(), controller.signal).then(preview => {
      if (!controller.signal.aborted && sequence.current === current) setState({ key, status: 'ready', preview });
    }).catch(error => {
      if (sequence.current === current && (!controller.signal.aborted || expired)) {
        setState({ key, status: 'error', error: expired ? '产物读取超时，请重试' : error instanceof Error ? error.message : '产物读取失败' });
      }
    }).finally(() => clearTimeout(timer));
    return () => { ++sequence.current; clearTimeout(timer); controller.abort(); };
  }, [authHeaders, key, selected]);

  const close = () => { setSelected(null); setState(null); };
  const visible = state?.key === key ? state : null;

  return (
    <section aria-label="Mission Artifact">
      <h4 className="text-sm font-medium text-warm-800">Artifact</h4>
      {items.length ? <ul className="mt-2 divide-y divide-warm-100 rounded border border-warm-200">
        {items.map((item, index) => {
          const artifact = registeredArtifact(item, missionId);
          const id = typeof item.id === 'string' ? item.id : `未命名 Artifact ${index + 1}`;
          const unavailable = artifact ? previewUnavailable(artifact) : '元数据不完整，无法预览';
          return <li key={`${id}:${index}`} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs text-warm-700">
            <div className="min-w-0"><span className="break-all font-mono">{id}</span>
              {artifact && <span className="ml-2">attempt {artifact.attempt} · {artifact.sizeBytes} 字节</span>}
              {unavailable && <p className="mt-1 text-warm-500">{unavailable}</p>}
            </div>
            <button type="button" disabled={!!unavailable} aria-label={`预览 ${id}`}
              className="rounded border border-warm-200 px-3 py-1 text-primary-700 hover:bg-warm-50 disabled:opacity-50"
              onClick={event => { triggerRef.current = event.currentTarget; setState(null); setSelected(artifact); }}>只读预览</button>
          </li>;
        })}
      </ul> : <p className="mt-2 text-xs text-warm-500">暂无 Artifact</p>}
      <ArtifactDialog open={selected !== null} onOpenChange={open => { if (!open) close(); }}
        title="Artifact 只读预览" description="校验登记字节后显示；内容不会执行，也不代表任务验收通过。" returnFocusRef={triggerRef}>
        {selected && <div className="min-w-0 space-y-3">
          <dl className="grid gap-1 break-all text-xs text-warm-600">
            <div><dt className="inline font-medium">Artifact：</dt><dd className="inline font-mono">{selected.id}</dd></div>
            <div><dt className="inline font-medium">WorkUnit：</dt><dd className="inline font-mono">{selected.workUnitId} · attempt {selected.attempt}</dd></div>
            <div><dt className="inline font-medium">摘要：</dt><dd className="inline font-mono">{selected.digest}</dd></div>
          </dl>
          {(!visible || visible.status === 'loading') && <p role="status" className="text-sm text-warm-600">正在读取并校验产物</p>}
          {visible?.status === 'error' && <div role="alert" className="space-y-2 text-sm text-danger-700">
            <p>{visible.error}</p><button type="button" className="rounded border border-warm-200 px-3 py-1" onClick={() => setRevision(value => value + 1)}>重试读取产物</button>
          </div>}
          {visible?.status === 'ready' && <>
            <p className="text-xs text-warm-600">字节校验通过 · {visible.preview.artifact.sizeBytes} 字节；Mission 状态仍以服务端验收为准。</p>
            {visible.preview.text === '' ? <p className="text-sm text-warm-600">空产物（0 字节）</p>
              : selected.kind === 'log' ? <ReadOnlyArtifactLog text={visible.preview.text} artifactId={selected.id} onClose={close} />
              : <ReadOnlyArtifactCode text={visible.preview.text} artifactId={selected.id} mediaType={selected.mediaType} onClose={close} />}
          </>}
        </div>}
      </ArtifactDialog>
    </section>
  );
}
