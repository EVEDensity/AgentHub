'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import dynamic from 'next/dynamic';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { useAuthStore } from '../../stores/authStore';
import { useWorkspaceStore } from '../../stores/workspaceStore';
import { useAdminStore } from '../../stores/adminStore';
import { useMissionChat } from '../../hooks/useMissionChat';
import { useMissionExecutionStatus } from '../../hooks/useMissionExecutionStatus';
import { appendMissionDialogRows, projectMissionDialogEvents, type MissionDialogProjection } from '../../lib/missionDialogProjection';
import MissionAdmissionControls from './MissionAdmissionControls';
import MissionExecutionBanner from './MissionExecutionBanner';

const MissionObservationThread = dynamic(() => import('./MissionObservationThread').then(module => module.MissionObservationThread), {
  ssr: false, loading: () => <p role="status" className="text-sm text-warm-600">正在加载本地对话视图</p>,
});

interface AIChatDialogProps {
  open: boolean;
  onClose: () => void;
  addConsoleLog?: (tag: string, msg: string, tagClass?: string) => void;
}

function DialogFrame({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) {
  const previousFocus = useRef<HTMLElement | null>(null);
  return <DialogPrimitive.Root open={open} onOpenChange={value => { if (!value) onClose(); }}>
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-[80] bg-black/60" />
      <DialogPrimitive.Content className="fixed left-1/2 top-1/2 z-[81] flex max-h-[94vh] w-[min(94vw,760px)] -translate-x-1/2 -translate-y-1/2 flex-col gap-3 overflow-y-auto rounded border border-warm-200 bg-warm-100 p-4 shadow-lg focus:outline-none"
        onOpenAutoFocus={() => { previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; }}
        onCloseAutoFocus={event => { event.preventDefault(); if (previousFocus.current?.isConnected) previousFocus.current.focus(); }}>
        <DialogPrimitive.Title className="pr-8 text-lg font-semibold text-warm-900">Mission 助手</DialogPrimitive.Title>
        <DialogPrimitive.Description className="pr-8 text-sm text-warm-600">提交会创建 Mission。关闭仅暂停观察，任务继续；最终结果以服务端独立验收为准。</DialogPrimitive.Description>
        <DialogPrimitive.Close aria-label="关闭 Mission 助手" className="absolute right-3 top-3 rounded p-2 text-warm-600 hover:bg-warm-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600"><X className="h-4 w-4" aria-hidden="true" /></DialogPrimitive.Close>
        {children}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  </DialogPrimitive.Root>;
}

function MissionChatScope({ open, onClose, token, workspaceId }: AIChatDialogProps & { token: string; workspaceId: string }) {
  const authHeaders = useAuthStore(state => state.authHeaders);
  const setActiveMenu = useAdminStore(state => state.setActiveMenu);
  const [draft, setDraft] = useState('');
  const [projection, setProjection] = useState<MissionDialogProjection>({ rows: [], omitted: false });
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const sendingRef = useRef(false);
  const chat = useMissionChat({ token, workspaceId, sessionId: 'admin-assistant', authHeaders, enabled: open, eventLimit: 200, cacheSessionLink: false });
  const status = useMissionExecutionStatus(chat.missionId, token, authHeaders, { workspaceId, enabled: open });
  const terminal = status.data && ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(status.data.missionStatus);
  const active = !!chat.missionId && !terminal;
  const disabled = chat.sending || !!chat.pending || active;

  useEffect(() => {
    setProjection(previous => appendMissionDialogRows(previous, projectMissionDialogEvents(chat.events, chat.missionId)));
  }, [chat.events, chat.missionId]);

  const submit = async () => {
    const submitted = draftRef.current;
    const text = submitted.trim();
    if (!open || disabled || sendingRef.current || !text || text.length > 8000) return;
    sendingRef.current = true;
    try {
      const result = await chat.sendMission(text);
      if (!result) return;
      setProjection(previous => appendMissionDialogRows(previous, [{
        id: `request:${crypto.randomUUID()}`, role: 'user', text,
        label: '已登记请求 · 后续状态见上方',
        ...(result.missionId ? { missionId: result.missionId } : {}),
      }]));
      setDraft(current => current === submitted ? '' : current);
    } finally { sendingRef.current = false; }
  };

  return <DialogFrame open={open} onClose={onClose}>
    <p className="break-all text-xs text-warm-600">工作空间：{workspaceId}{chat.missionId && <> · 当前 Mission：<span className="font-mono">{chat.missionId}</span></>}</p>
    <MissionAdmissionControls pending={chat.pending} error={chat.error} sending={chat.sending} cancelling={chat.cancelling} missionId={chat.missionId} onConfirm={chat.confirmPending} onCancel={chat.cancel} onReconnect={chat.reconnect} />
    <MissionExecutionBanner missionId={chat.missionId} token={token} authHeaders={authHeaders} onCancel={chat.cancel} status={status} />
    {status.data?.missionStatus === 'WAITING_DECISION' && <button type="button" onClick={() => { setActiveMenu('决策收件箱'); onClose(); }} className="self-start rounded border border-warm-200 px-3 py-1 text-sm text-primary-700">打开决策收件箱</button>}
    {open && <MissionObservationThread rows={projection.rows} />}
    {(projection.omitted || chat.omittedEvents > 0) && <p role="status" className="text-xs text-warm-600">仅保留最近 200 条消息；较早观察已省略，此视图不是完整历史。</p>}
    <form onSubmit={event => { event.preventDefault(); void submit(); }} className="space-y-2">
      <label className="block text-sm text-warm-800">任务要求<textarea aria-label="任务要求" value={draft} onChange={event => setDraft(event.target.value)} rows={3} maxLength={8000}
        onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void submit(); } }}
        placeholder="输入纯文本任务；Enter 提交，Shift+Enter 换行" className="mt-1 w-full resize-y rounded border border-warm-200 bg-warm-50 px-3 py-2 text-warm-900 focus:outline focus:outline-2 focus:outline-primary-600" /></label>
      <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs text-warm-600">仅支持纯文本；模型与权限由服务端合同控制。</span><button type="submit" disabled={disabled || !draft.trim()} className="rounded bg-primary-600 px-4 py-2 text-sm text-warm-50 disabled:opacity-50">{chat.sending ? '正在提交请求…' : '提交 Mission'}</button></div>
      {active && <p className="text-xs text-warm-600">当前 Mission 尚未结束，可继续编辑下一条草稿。</p>}
    </form>
  </DialogFrame>;
}

/** Scope changes synchronously replace the local projection and every observer. */
export default function AIChatDialog(props: AIChatDialogProps) {
  const token = useAuthStore(state => state.token);
  const user = useAuthStore(state => state.user);
  const workspaceId = useWorkspaceStore(state => state.currentWorkspaceId);
  const parts = [workspaceId, token, user?.id ?? '', user?.role ?? ''];
  const scope = useRef({ parts, revision: 0 });
  if (parts.some((part, index) => scope.current.parts[index] !== part)) scope.current = { parts, revision: scope.current.revision + 1 };
  if (!token || !workspaceId || !user?.id) return <DialogFrame open={props.open} onClose={props.onClose}><p role="alert" className="text-sm text-warm-600">请先登录并选择工作空间，才能提交或观察 Mission。</p></DialogFrame>;
  return <MissionChatScope key={scope.current.revision} {...props} token={token} workspaceId={workspaceId} />;
}
