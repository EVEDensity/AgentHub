'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { useMissionChat } from '../../hooks/useMissionChat';
import { useMissionExecutionStatus } from '../../hooks/useMissionExecutionStatus';
import { useMissionSessions, useSessionMissions } from '../../hooks/useMissionSessions';
import { appendMissionDialogRows, projectMissionDialogEvents, type MissionDialogProjection } from '../../lib/missionDialogProjection';
import { missionIdentifier, readMissionJson } from '../../lib/missionStream';
import { unsupportedMissionInput } from '../../lib/missionChatInput';
import type { User } from '../../types';
import MissionAdmissionControls from './MissionAdmissionControls';
import MissionExecutionBanner from './MissionExecutionBanner';

const MissionObservationThread = dynamic(() => import('./MissionObservationThread').then(module => module.MissionObservationThread), {
  ssr: false, loading: () => <p role="status">正在加载本地对话视图…</p>,
});
const buttonStyle = 'rounded border border-warm-200 px-3 py-2 text-sm hover:bg-warm-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-600 disabled:opacity-50';
interface Workspace { id: string; name: string; }
interface View { revision: number; sessionId?: string; missionId?: string; }

/** Personal scope comes from Mission Control, not a local user-to-workspace guess. */
export default function NativeMissionWorkspace({ token, user, onLogout }: { token: string; user: User & { id: string }; onLogout: () => void }) {
  const [catalog, setCatalog] = useState<Workspace[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [workspaceId, setWorkspaceId] = useState('');
  const scope = useRef({ workspaceId, revision: 0 });
  if (scope.current.workspaceId !== workspaceId) scope.current = { workspaceId, revision: scope.current.revision + 1 };
  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    const deadline = setTimeout(() => { if (current) setError('工作空间读取超时，请重试'); controller.abort(); }, 20_000);
    setCatalog(null);
    setError(null);
    void (async () => {
      try {
        const response = await fetch('/api/v1/access/whoami', { headers: { Authorization: `Bearer ${token}` }, signal: controller.signal, cache: 'no-store', redirect: 'error' });
        if (!response.ok) throw new Error(`工作空间读取失败（HTTP ${response.status}）`);
        const body = await readMissionJson(response, controller.signal);
        if (body.userId !== user.id) throw new Error('服务端身份与当前登录用户不一致，请退出后重新登录。');
        if (!missionIdentifier(body.workspaceId)) throw new Error('工作空间响应无效');
        if (!controller.signal.aborted) setCatalog([{ id: body.workspaceId, name: `个人工作空间 (${body.workspaceId})` }]);
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : '工作空间读取失败');
      } finally { clearTimeout(deadline); }
    })();
    return () => { current = false; clearTimeout(deadline); controller.abort(); };
  }, [token, user.id, reload]);
  const selected = catalog?.find(item => item.id === workspaceId);
  return <div className="min-h-screen bg-warm-50 text-warm-900">
    <header className="flex flex-wrap items-center gap-3 border-b border-warm-200 bg-warm-100 p-4">
      <h1 className="text-xl font-semibold">AgentHub · Mission 会话</h1>
      <label className="text-sm">工作空间<select aria-label="工作空间" value={selected ? workspaceId : ''} onChange={event => setWorkspaceId(event.target.value)} disabled={!catalog} className="ml-2 max-w-64 rounded border border-warm-200 bg-warm-50 p-2">
        <option value="">请选择工作空间</option>{catalog?.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
      <button type="button" className={buttonStyle} onClick={() => { setWorkspaceId(''); setReload(value => value + 1); }}>刷新工作空间</button>
      <span className="text-sm text-warm-600">{user.name}</span>
      <a href="/admin" className={buttonStyle}>管理控制台</a>
      <button type="button" className={buttonStyle} onClick={onLogout}>退出登录</button>
    </header>
    {error && <p role="alert" className="p-4 text-danger-600">{error}</p>}
    {!catalog && !error && <p role="status" className="p-4">正在读取工作空间…</p>}
    {catalog?.length === 0 && <p className="p-4">暂无可访问的工作空间。</p>}
    {catalog && !selected && <p className="p-4">请选择 Mission Control 返回的个人工作空间。当前视图仅支持个人范围，跨工作空间目录尚未接入。</p>}
    {selected && <NativeSessionScope key={scope.current.revision} token={token} workspaceId={selected.id} />}
  </div>;
}

function NativeSessionScope({ token, workspaceId }: { token: string; workspaceId: string }) {
  const sessions = useMissionSessions({ token, workspaceId });
  const [view, setView] = useState<View>({ revision: 0 });
  const [observed, setObserved] = useState<{ sessionId?: string; missionId?: string }>({});
  const liveSessionId = observed.sessionId ?? view.sessionId;
  const missions = useSessionMissions({ token, workspaceId, sessionId: liveSessionId });
  const select = (sessionId?: string, missionId?: string) => {
    setObserved({});
    setView(previous => ({ revision: previous.revision + 1, sessionId, missionId }));
  };
  const reportObserved = useCallback((sessionId?: string, missionId?: string) => setObserved(previous => previous.sessionId === sessionId && previous.missionId === missionId ? previous : { sessionId, missionId }), []);
  const reloadSessions = sessions.reload;
  const reloadMissions = missions.reload;
  const admitted = useCallback(() => { reloadSessions(); reloadMissions(); }, [reloadSessions, reloadMissions]);
  const historyMission = missions.items.find(item => item.id === view.missionId);
  return <div className="grid min-h-[calc(100vh-80px)] grid-cols-1 lg:grid-cols-[280px_minmax(0,1fr)]">
    <aside aria-label="原生会话目录" className="space-y-3 border-b border-warm-200 bg-warm-100 p-4 lg:border-b-0 lg:border-r">
      <h2 className="font-semibold">原生 Mission 会话</h2>
      <p className="break-all text-xs text-warm-600">工作空间：{workspaceId}</p>
      <div className="flex flex-wrap gap-2"><button type="button" className={buttonStyle} onClick={() => select()}>新会话</button><button type="button" className={buttonStyle} onClick={sessions.reload}>刷新会话</button></div>
      {sessions.loading && <p role="status">正在读取会话…</p>}
      {sessions.error && <p role="alert" className="text-sm text-danger-600">{sessions.error}<button type="button" className="ml-2 underline" onClick={sessions.reload}>重试读取会话</button></p>}
      {!sessions.loading && !sessions.error && sessions.items.length === 0 && <p className="text-sm text-warm-600">暂无原生会话。提交任务后由服务端建立会话。</p>}
      <ul className="max-h-[40vh] space-y-2 overflow-y-auto">{sessions.items.map(item => <li key={item.id}><button type="button" className={`${buttonStyle} w-full break-words text-left`} aria-label={`打开会话 ${item.title} (${item.id})`} aria-current={liveSessionId === item.id ? 'true' : undefined} onClick={() => select(item.id)}><span className="block">{item.title || '未命名会话'}</span><span className="block break-all text-xs text-warm-600">{item.id} · {item.status}</span></button></li>)}</ul>
      {sessions.hasMore && <button type="button" className={buttonStyle} disabled={sessions.loading} onClick={sessions.loadMore}>加载更多会话</button>}
      {sessions.limited && <p className="text-xs text-warm-600">目录仅显示最近读取的 500 个会话；不是完整历史。</p>}
      <p className="text-xs text-warm-600">旧聊天会话及没有原生工作空间归属的记录未迁移。这里展示任务目标与有界账本观察，不能恢复完整原始对话。</p>
    </aside>
    <main className="min-w-0 space-y-4 p-4 md:p-6">
      {liveSessionId && <section aria-label="会话 Mission 目录" className="space-y-2 rounded border border-warm-200 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="break-all font-semibold">会话：{liveSessionId}</h2><button type="button" className={buttonStyle} onClick={missions.reload}>刷新 Mission</button></div>
        {missions.loading && <p role="status">正在读取 Mission…</p>}
        {missions.error && <p role="alert" className="text-sm text-danger-600">{missions.error}<button type="button" onClick={missions.reload} className="ml-2 underline">重试读取 Mission</button></p>}
        {!missions.loading && !missions.error && missions.items.length === 0 && <p className="text-sm text-warm-600">此会话尚无可读取的 Mission。</p>}
        <ul className="max-h-48 space-y-2 overflow-y-auto">{missions.items.map(item => <li key={item.id}><button type="button" className={`${buttonStyle} w-full text-left`} aria-label={`观察 Mission ${item.title} (${item.id})`} aria-current={(observed.missionId ?? view.missionId) === item.id ? 'true' : undefined} onClick={() => select(liveSessionId, item.id)}><span className="block break-words">{item.title || '任务'} · {item.status}</span><span className="block break-all text-xs text-warm-600">{item.id}</span></button></li>)}</ul>
        {missions.hasMore && <button type="button" className={buttonStyle} disabled={missions.loading} onClick={missions.loadMore}>加载更多 Mission</button>}
        {missions.limited && <p className="text-xs text-warm-600">仅显示最近读取的 200 个 Mission；不是完整历史。</p>}
      </section>}
      <NativeMissionConversation key={view.revision} token={token} workspaceId={workspaceId} nativeSessionId={view.sessionId} resumeMissionId={view.missionId}
        objective={historyMission?.objectivePreview} objectiveTruncated={historyMission?.objectiveTruncated} onObserved={reportObserved} onAdmission={admitted} />
    </main>
  </div>;
}

function NativeMissionConversation({ token, workspaceId, nativeSessionId, resumeMissionId, objective, objectiveTruncated, onObserved, onAdmission }: {
  token: string; workspaceId: string; nativeSessionId?: string; resumeMissionId?: string;
  objective?: string; objectiveTruncated?: boolean; onObserved: (sessionId?: string, missionId?: string) => void; onAdmission: () => void;
}) {
  const authHeaders = useCallback(() => ({ Authorization: `Bearer ${token}` }), [token]);
  const chat = useMissionChat({ token, workspaceId, sessionId: nativeSessionId ?? 'new-main-view', nativeSessionId, resumeMissionId, authHeaders, eventLimit: 200, cacheSessionLink: false, blockUncertainAdmission: true });
  const status = useMissionExecutionStatus(chat.missionId, token, authHeaders, { workspaceId });
  const [draft, setDraft] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [inputError, setInputError] = useState<string | null>(null);
  const [projection, setProjection] = useState<MissionDialogProjection>({ rows: [], omitted: false });
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const filesRef = useRef(files);
  filesRef.current = files;
  const busy = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const terminal = !!status.data && ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(status.data.missionStatus);
  const active = !!chat.missionId && !terminal;
  const history = !!nativeSessionId;
  const disabled = history || chat.sending || !!chat.pending || active || chat.admissionUncertain;
  useEffect(() => { onObserved(chat.sessionId ?? nativeSessionId, chat.missionId ?? resumeMissionId); }, [chat.sessionId, chat.missionId, nativeSessionId, resumeMissionId, onObserved]);
  useEffect(() => { setProjection(previous => appendMissionDialogRows(previous, projectMissionDialogEvents(chat.events, chat.missionId))); }, [chat.events, chat.missionId]);
  const submit = async () => {
    const submitted = draftRef.current;
    const selectedFiles = filesRef.current;
    if (disabled || busy.current || !submitted.trim() && !selectedFiles.length) return;
    const unsupported = unsupportedMissionInput({ attachments: selectedFiles });
    if (unsupported) { setInputError(unsupported); return; }
    if (submitted.trim().length > 8000) { setInputError('任务要求不能超过 8000 个字符。'); return; }
    busy.current = true;
    setInputError(null);
    try {
      const result = await chat.sendMission(submitted.trim());
      if (!mounted.current || !result) return;
      setProjection(previous => appendMissionDialogRows(previous, [{ id: `request:${crypto.randomUUID()}`, role: 'user', text: submitted.trim(), label: '已登记请求 · 后续状态见上方', ...(result.missionId ? { missionId: result.missionId } : {}) }]));
      setDraft(current => current === submitted ? '' : current);
      setFiles(current => current === selectedFiles ? [] : current);
      onAdmission();
    } finally { busy.current = false; }
  };
  return <section aria-label="原生 Mission 对话" className="space-y-3">
    <h2 className="text-lg font-semibold">{history ? '历史任务观察' : '提交 Mission'}</h2>
    <p className="text-sm text-warm-600">切换会话仅停止本地观察，任务继续。模型文本与 WorkUnit 完成不代表独立验收通过。</p>
    {history && <p className="text-sm text-warm-600">历史会话只读。请选择 Mission 观察；继续提交请新建会话。原始完整消息未恢复。</p>}
    {objective !== undefined && <div className="rounded border border-warm-200 p-3"><h3 className="text-sm font-semibold">任务目标（服务端记录）</h3><p className="whitespace-pre-wrap break-words text-sm">{objective}</p>{objectiveTruncated && <p className="text-xs text-warm-600">任务目标预览已截断。</p>}</div>}
    <MissionAdmissionControls pending={chat.pending} error={chat.error} sending={chat.sending} cancelling={chat.cancelling} admissionBlocked={chat.admissionUncertain} missionId={chat.missionId} onConfirm={chat.confirmPending} onCancel={chat.cancel} onReconnect={chat.reconnect} />
    {chat.admissionUncertain && <p role="alert" className="text-sm text-danger-600">提交结果未确认，请刷新会话目录核对；当前草稿不会重发。如需另一任务请新建会话。</p>}
    <MissionExecutionBanner missionId={chat.missionId} token={token} authHeaders={authHeaders} status={status} onCancel={chat.cancel} />
    {status.data?.missionStatus === 'WAITING_DECISION' && <a href={`/admin?menu=${encodeURIComponent('决策收件箱')}`} className="inline-block underline">打开决策收件箱</a>}
    <MissionObservationThread rows={projection.rows} />
    {(projection.omitted || chat.omittedEvents > 0 || history) && <p className="text-xs text-warm-600">仅保留最近 200 条观察；服务端事件窗口也有界，此视图不是完整历史。</p>}
    <form onSubmit={event => { event.preventDefault(); void submit(); }} className="space-y-3">
      <label className="block text-sm">任务要求<textarea aria-label="任务要求" placeholder="输入纯文本任务；Enter 提交，Shift+Enter 换行" className="mt-1 block w-full rounded border border-warm-200 bg-warm-100 p-3 text-warm-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-600" rows={3} value={draft} maxLength={8000} onChange={event => setDraft(event.target.value)}
        onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void submit(); } }} /></label>
      <label className="inline-block text-sm">选择附件（当前暂不支持提交）<input type="file" multiple aria-label="选择附件" className="block text-xs" onChange={event => { const selected = Array.from(event.target.files ?? []); setFiles(previous => [...previous, ...selected]); event.target.value = ''; }} /></label>
      {files.map((file, index) => <div key={`${file.name}:${index}`} className="flex flex-wrap items-center gap-2 text-sm"><span>{file.name} · {file.size} 字节</span><button type="button" aria-label={`移除 ${file.name}`} className="underline" onClick={() => setFiles(previous => previous.filter((_, item) => item !== index))}>移除</button></div>)}
      {inputError && <p role="alert" className="text-sm text-danger-600">{inputError}</p>}
      <p className="text-xs text-warm-600">仅支持纯文本；权限、模型和执行由服务端合同控制。Enter 提交，Shift+Enter 换行。</p>
      <button type="submit" className={`${buttonStyle} bg-primary-600`} disabled={disabled || !draft.trim() && !files.length}>{chat.sending ? '正在提交请求…' : '提交 Mission'}</button>
      {active && <p className="text-sm text-warm-600">当前 Mission 尚未结束或状态未知，可继续编辑下一条草稿。</p>}
    </form>
  </section>;
}
