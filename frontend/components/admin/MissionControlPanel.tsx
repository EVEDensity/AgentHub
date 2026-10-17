'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react';
import { AlertTriangle, FileText, Play, RefreshCw, Send, Square } from 'lucide-react';
import { useWorkspaceStore } from '../../stores/workspaceStore';
import { useAuthStore } from '../../stores/authStore';
import { MissionArtifactList } from '../artifacts/MissionArtifactList';

type Mission = {
  id: string;
  workspaceId: string;
  title: string;
  objective: string;
  status: string;
  createdAt?: string;
  updatedAt?: string;
};

type WorkUnit = { id: string; kind: string; status: string; attempt?: number; lease?: { expiresAt?: string } | null };
type RecordItem = Record<string, unknown>;

interface MissionControlPanelProps {
  authHeaders: () => Record<string, string>;
  setNotice: (message: string) => void;
  fmtErr?: (detail: unknown, fallback: string) => string;
}

const DEFAULT_CONTRACT = JSON.stringify({
  id: 'contract-manual-v1',
  version: 1,
  repositoryScopes: [],
  allowedCapabilities: [],
  budgets: { timeSeconds: 300, modelCost: 1, retries: 0 },
  acceptanceCriteria: [{ id: 'manual-review', kind: 'manual', description: '由工作空间操作者审核 Mission 输出。', required: true, configuration: {} }],
  decisionGates: [],
  forbiddenActions: [],
}, null, 2);

function message(body: RecordItem, fallback: string): string {
  return typeof body.detail === 'string' ? body.detail : fallback;
}

async function json(response: Response): Promise<RecordItem> {
  const body = await response.json().catch(() => ({}));
  return body && typeof body === 'object' ? body as RecordItem : {};
}

function records(body: RecordItem, key: string): RecordItem[] {
  const value = body[key];
  if (!Array.isArray(value) || value.some(item => !item || typeof item !== 'object' || Array.isArray(item))) {
    throw new Error('Mission 投影响应格式无效');
  }
  return value as RecordItem[];
}

function statusClass(status: string): string {
  if (['SUCCEEDED', 'READY'].includes(status)) return 'text-success-700 bg-success-50 border-success-200';
  if (['FAILED', 'CANCELLED'].includes(status)) return 'text-danger-700 bg-danger-50 border-danger-200';
  if (['RUNNING', 'VERIFYING', 'WAITING_DECISION'].includes(status)) return 'text-primary-700 bg-primary-50 border-primary-200';
  return 'text-warm-700 bg-warm-50 border-warm-200';
}

export default function MissionControlPanel({ authHeaders, setNotice, fmtErr }: MissionControlPanelProps): JSX.Element {
  const workspaceId = useWorkspaceStore((state) => state.currentWorkspaceId);
  const token = useAuthStore((state) => state.token);
  const user = useAuthStore((state) => state.user);
  const principal = JSON.stringify([user?.id, user?.role]);
  const scope = useRef({ workspaceId, token, principal, version: 0 });
  if (scope.current.workspaceId !== workspaceId || scope.current.token !== token || scope.current.principal !== principal) {
    scope.current = { workspaceId, token, principal, version: scope.current.version + 1 };
  }
  // Remount synchronously: previous authorized bytes cannot flash during an effect cleanup.
  return <WorkspaceMissionPanel key={scope.current.version} workspaceId={workspaceId} authHeaders={authHeaders} setNotice={setNotice} fmtErr={fmtErr} />;
}

function WorkspaceMissionPanel({ workspaceId, authHeaders, setNotice, fmtErr }: MissionControlPanelProps & { workspaceId: string }): JSX.Element {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [workUnits, setWorkUnits] = useState<WorkUnit[]>([]);
  const [artifacts, setArtifacts] = useState<RecordItem[]>([]);
  const [evidence, setEvidence] = useState<RecordItem[]>([]);
  const [events, setEvents] = useState<RecordItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [loadedDetailId, setLoadedDetailId] = useState('');
  const [detailRevision, setDetailRevision] = useState(0);
  const listController = useRef<AbortController | null>(null);
  const [error, setError] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState('');
  const [objective, setObjective] = useState('');
  const [contract, setContract] = useState(DEFAULT_CONTRACT);
  const [guidance, setGuidance] = useState('');
  const [sendingGuidance, setSendingGuidance] = useState(false);

  const selected = useMemo(() => missions.find((mission) => mission.id === selectedId) ?? null, [missions, selectedId]);
  const detailReady = loadedDetailId === selectedId && !detailLoading && !detailError;

  const loadMissions = useCallback(async () => {
    listController.current?.abort();
    const controller = new AbortController();
    listController.current = controller;
    setLoading(true); setError('');
    try {
      const response = await fetch(`/api/v1/missions?workspaceId=${encodeURIComponent(workspaceId)}&limit=100&offset=0`, { headers: authHeaders(), signal: controller.signal, cache: 'no-store' });
      const body = await json(response);
      if (controller.signal.aborted) return;
      if (!response.ok) throw new Error(message(body, `HTTP ${response.status}`));
      const next = records(body, 'missions') as Mission[];
      if (next.some(item => item.workspaceId !== workspaceId || typeof item.id !== 'string'
          || typeof item.title !== 'string' || typeof item.objective !== 'string' || typeof item.status !== 'string')) {
        throw new Error('Mission 投影与当前工作空间不一致');
      }
      setMissions(next);
      setSelectedId((current) => next.some((item) => item.id === current) ? current : (next[0]?.id ?? ''));
    } catch (caught) {
      if (controller.signal.aborted) return;
      const detail = caught instanceof Error ? caught.message : 'Mission 列表加载失败';
      setError(fmtErr?.(detail, 'Mission 列表加载失败') ?? detail);
      setMissions([]); setSelectedId('');
    } finally { if (!controller.signal.aborted) setLoading(false); }
  }, [authHeaders, fmtErr, workspaceId]);

  const loadDetail = useCallback(async (missionId: string, signal: AbortSignal) => {
    if (!missionId) return;
    setDetailLoading(true); setDetailError('');
    try {
      const headers = authHeaders();
      const [unitsResponse, artifactsResponse, evidenceResponse] = await Promise.all([
        fetch(`/api/v1/missions/${encodeURIComponent(missionId)}/work-units?limit=100&offset=0`, { headers, signal, cache: 'no-store' }),
        fetch(`/api/v1/missions/${encodeURIComponent(missionId)}/artifacts?limit=100&offset=0`, { headers, signal, cache: 'no-store' }),
        fetch(`/api/v1/missions/${encodeURIComponent(missionId)}/evidence?limit=100&offset=0`, { headers, signal, cache: 'no-store' }),
      ]);
      const [unitsBody, artifactsBody, evidenceBody] = await Promise.all([json(unitsResponse), json(artifactsResponse), json(evidenceResponse)]);
      if (signal.aborted) return;
      if (!unitsResponse.ok) throw new Error(message(unitsBody, `WorkUnit HTTP ${unitsResponse.status}`));
      if (!artifactsResponse.ok) throw new Error(message(artifactsBody, `Artifact HTTP ${artifactsResponse.status}`));
      if (!evidenceResponse.ok) throw new Error(message(evidenceBody, `Evidence HTTP ${evidenceResponse.status}`));
      const nextUnits = records(unitsBody, 'workUnits');
      const nextArtifacts = records(artifactsBody, 'artifacts');
      const nextEvidence = records(evidenceBody, 'evidence');
      if ([...nextUnits, ...nextArtifacts, ...nextEvidence].some(item => item.missionId !== missionId)) {
        throw new Error('Mission 详情响应不属于当前 Mission');
      }
      setWorkUnits(nextUnits as WorkUnit[]);
      setArtifacts(nextArtifacts);
      setEvidence(nextEvidence);
      setLoadedDetailId(missionId);
    } catch (caught) {
      if (signal.aborted) return;
      const detail = caught instanceof Error ? caught.message : 'Mission 详情加载失败';
      setDetailError(fmtErr?.(detail, 'Mission 详情加载失败') ?? detail);
      setWorkUnits([]); setArtifacts([]); setEvidence([]);
      setLoadedDetailId(missionId);
    } finally { if (!signal.aborted) setDetailLoading(false); }
  }, [authHeaders, fmtErr]);

  useEffect(() => { void loadMissions(); return () => listController.current?.abort(); }, [loadMissions]);
  useEffect(() => {
    const controller = new AbortController();
    void loadDetail(selectedId, controller.signal);
    return () => controller.abort();
  }, [loadDetail, selectedId, detailRevision]);

  // P3-4a: live mission events — prefer the SSE stream; on any stream
  // failure (e.g. 401 because EventSource cannot attach Bearer headers)
  // fall back to the authenticated 1s polling of GET /events.
  useEffect(() => {
    if (!selectedId) { setEvents([]); return undefined; }
    setEvents([]);
    let cancelled = false;
    let source: EventSource | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;

    const mergeEvents = (incoming: RecordItem[]) => {
      if (cancelled) return;
      setEvents((current) => {
        const seen = new Set(current.map((event) => String(event.event_id ?? '')));
        const merged = [...current];
        for (const event of incoming) {
          const id = String(event.event_id ?? '');
          if (!id || seen.has(id)) continue;
          seen.add(id);
          merged.push(event);
        }
        merged.sort((a, b) => String(a.occurred_at ?? '').localeCompare(String(b.occurred_at ?? '')));
        return merged.slice(-100);
      });
    };

    const pollEvents = async () => {
      try {
        const response = await fetch(`/api/v1/missions/${encodeURIComponent(selectedId)}/events?afterSequence=0&limit=100`, { headers: authHeaders() });
        if (!response.ok) return;
        const body = await json(response);
        if (!cancelled && Array.isArray(body.events)) mergeEvents(body.events as RecordItem[]);
      } catch { /* best-effort: the next tick retries */ }
    };

    const startPolling = () => {
      if (pollTimer !== null) return;
      void pollEvents();
      pollTimer = setInterval(() => { void pollEvents(); }, 1000);
    };

    try {
      source = new EventSource(`/api/v1/missions/${encodeURIComponent(selectedId)}/events/stream`);
      source.onmessage = (message: MessageEvent<string>) => {
        try { mergeEvents([JSON.parse(message.data) as RecordItem]); } catch { /* ignore malformed frames */ }
      };
      source.onerror = () => {
        source?.close(); source = null;
        if (!cancelled) startPolling();
      };
    } catch { /* EventSource unavailable — straight to polling */ startPolling(); }

    return () => {
      cancelled = true;
      source?.close();
      if (pollTimer !== null) clearInterval(pollTimer);
    };
  }, [authHeaders, selectedId]);

  const runMissionCommand = useCallback(async (command: 'start' | 'cancel') => {
    if (!selected) return;
    try {
      const response = await fetch(`/api/v1/missions/${encodeURIComponent(selected.id)}/${command}`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() } });
      const body = await json(response);
      if (!response.ok) throw new Error(message(body, `HTTP ${response.status}`));
      setNotice(command === 'start' ? 'Mission 已提交启动' : 'Mission 已取消');
      await loadMissions();
    } catch (caught) {
      const detail = caught instanceof Error ? caught.message : 'Mission 操作失败';
      setError(fmtErr?.(detail, 'Mission 操作失败') ?? detail);
    }
  }, [authHeaders, fmtErr, loadMissions, selected, setNotice]);

  const sendGuidance = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected || !guidance.trim()) return;
    setSendingGuidance(true); setError('');
    try {
      const response = await fetch(`/api/v1/missions/${encodeURIComponent(selected.id)}/guidance`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ content: guidance.trim() }),
      });
      const body = await json(response);
      if (!response.ok) throw new Error(message(body, `HTTP ${response.status}`));
      setGuidance('');
      setNotice('指导已发送，将在下一轮模型调用前注入（不打断执行）');
    } catch (caught) {
      const detail = caught instanceof Error ? caught.message : '指导发送失败';
      setError(fmtErr?.(detail, '指导发送失败') ?? detail);
    } finally { setSendingGuidance(false); }
  }, [authHeaders, fmtErr, guidance, selected, setNotice]);

  const createMission = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setCreating(true); setError('');
    try {
      const parsedContract = JSON.parse(contract) as RecordItem;
      const response = await fetch('/api/v1/missions', {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ title: title.trim(), objective: objective.trim(), workspaceId, source: { type: 'manual' }, contract: parsedContract }),
      });
      const body = await json(response);
      if (!response.ok) throw new Error(message(body, `HTTP ${response.status}`));
      setTitle(''); setObjective(''); setShowCreate(false); setNotice('Mission 已创建'); await loadMissions();
    } catch (caught) {
      const detail = caught instanceof SyntaxError ? 'Contract JSON 格式无效' : caught instanceof Error ? caught.message : 'Mission 创建失败';
      setError(fmtErr?.(detail, 'Mission 创建失败') ?? detail);
    } finally { setCreating(false); }
  }, [authHeaders, contract, fmtErr, loadMissions, objective, setNotice, title, workspaceId]);

  return (
    <section className="space-y-4" aria-labelledby="mission-control-title">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 id="mission-control-title" className="text-h2 text-warm-900">Mission Control</h2><p className="mt-1 text-sm text-warm-500">工作空间 {workspaceId} 的真实 Mission、WorkUnit、Artifact 和 Evidence 投影</p></div>
        <div className="flex gap-2"><button type="button" className="inline-flex h-9 items-center gap-2 rounded border border-warm-200 px-3 text-sm hover:bg-warm-50 disabled:opacity-50" onClick={() => { setDetailRevision(value => value + 1); void loadMissions(); }} disabled={loading}><RefreshCw className={loading ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} />刷新</button><button type="button" className="inline-flex h-9 items-center gap-2 rounded bg-primary-600 px-3 text-sm text-white hover:bg-primary-700" onClick={() => setShowCreate((value) => !value)}>创建 Mission</button></div>
      </header>
      {error && <div role="alert" className="flex items-start gap-2 rounded border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{error}</div>}
      {showCreate && <form onSubmit={createMission} className="space-y-3 rounded border border-warm-200 bg-warm-100 p-4"><div className="grid gap-3 md:grid-cols-2"><label className="text-sm text-warm-700">标题<input required value={title} onChange={(event) => setTitle(event.target.value)} className="mt-1 w-full rounded border border-warm-200 px-3 py-2" /></label><label className="text-sm text-warm-700">目标<textarea required value={objective} onChange={(event) => setObjective(event.target.value)} rows={2} className="mt-1 w-full rounded border border-warm-200 px-3 py-2" /></label></div><label className="block text-sm text-warm-700">Contract JSON<textarea required value={contract} onChange={(event) => setContract(event.target.value)} rows={8} className="mt-1 w-full rounded border border-warm-200 px-3 py-2 font-mono text-xs" /></label><div className="flex justify-end gap-2"><button type="button" className="rounded border border-warm-200 px-3 py-2 text-sm" onClick={() => setShowCreate(false)}>取消</button><button type="submit" disabled={creating} className="rounded bg-primary-600 px-3 py-2 text-sm text-white disabled:opacity-50">{creating ? '创建中' : '提交创建'}</button></div></form>}
      <div className="min-h-[460px] overflow-hidden rounded border border-warm-200 bg-warm-100 md:grid md:grid-cols-[minmax(260px,0.8fr)_minmax(0,1.5fr)]">
        <div className="border-b border-warm-200 md:border-b-0 md:border-r">{loading && missions.length === 0 ? <div className="p-6 text-sm text-warm-500">正在加载 Mission</div> : missions.length === 0 ? <div className="p-6 text-sm text-warm-500">当前工作空间暂无 Mission</div> : <ul className="divide-y divide-warm-150">{missions.map((mission) => <li key={mission.id}><button type="button" className={`w-full px-4 py-3 text-left hover:bg-warm-50 ${selectedId === mission.id ? 'bg-primary-50' : ''}`} onClick={() => setSelectedId(mission.id)}><span className="block truncate text-sm font-medium text-warm-900">{mission.title}</span><span className="mt-1 block truncate font-mono text-xs text-warm-500">{mission.id}</span><span className={`mt-2 inline-flex rounded border px-2 py-0.5 text-xs ${statusClass(mission.status)}`}>{mission.status}</span></button></li>)}</ul>}</div>
        <div className="min-w-0 p-4 md:p-5">{selected ? <div className="space-y-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="text-h3 text-warm-900">{selected.title}</h3><p className="mt-1 text-sm text-warm-600">{selected.objective}</p><p className="mt-2 font-mono text-xs text-warm-500">{selected.id}</p></div><div className="flex gap-2">{selected.status === 'READY' && <button type="button" className="inline-flex items-center gap-1 rounded bg-primary-600 px-3 py-2 text-sm text-white" onClick={() => void runMissionCommand('start')}><Play className="h-4 w-4" />启动</button>}{!['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(selected.status) && <button type="button" className="inline-flex items-center gap-1 rounded border border-danger-200 px-3 py-2 text-sm text-danger-700" onClick={() => void runMissionCommand('cancel')}><Square className="h-4 w-4" />取消</button>}</div></div><div className="grid gap-3 sm:grid-cols-3"><Metric label="WorkUnit" value={detailReady ? String(workUnits.length) : '—'} /><Metric label="Artifact" value={detailReady ? String(artifacts.length) : '—'} /><Metric label="Evidence" value={detailReady ? String(evidence.length) : '—'} /></div>{selected.status === 'RUNNING' && <form onSubmit={sendGuidance} className="space-y-1 rounded border border-warm-200 bg-warm-100 p-3"><label className="text-sm font-medium text-warm-800">运行中指导</label><div className="flex gap-2"><input value={guidance} onChange={(event) => setGuidance(event.target.value)} placeholder="补充要求或纠正方向，将在下一轮模型调用前注入（不打断执行）" className="flex-1 rounded border border-warm-200 px-3 py-2 text-sm" maxLength={2000} /><button type="submit" disabled={sendingGuidance || !guidance.trim()} className="inline-flex h-9 items-center gap-1 rounded bg-primary-600 px-3 text-sm text-white hover:bg-primary-700 disabled:opacity-50"><Send className="h-4 w-4" />{sendingGuidance ? '发送中' : '发送'}</button></div></form>}{detailLoading || loadedDetailId !== selectedId ? <p role="status" className="text-sm text-warm-500">正在加载详情</p> : detailError ? <div role="alert" className="space-y-2 text-sm text-danger-700"><p>{detailError}</p><button type="button" className="rounded border border-warm-200 px-3 py-1" onClick={() => setDetailRevision(value => value + 1)}>重试读取详情</button></div> : <div className="space-y-4"><DataList title="WorkUnit 状态" items={workUnits.map((unit) => `${unit.id} · ${unit.kind} · ${unit.status}${unit.attempt ? ` · attempt ${unit.attempt}` : ''}`)} empty="暂无 WorkUnit" /><MissionArtifactList key={`${selected.id}:${detailRevision}`} missionId={selected.id} items={artifacts} authHeaders={authHeaders} /><DataList title="Evidence" items={evidence.map((item) => String(item.id ?? item.evidenceId ?? '未命名 Evidence'))} empty="暂无 Evidence" /><DataList title="事件流" items={events.slice(-10).map((event) => `${String(event.event_type ?? '事件')} · ${String(event.event_id ?? '')}`)} empty="暂无事件（连接 Mission 事件流中）" /></div>}</div> : <div className="flex min-h-52 items-center justify-center text-sm text-warm-500">选择一个 Mission 查看真实状态</div>}</div>
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }): JSX.Element { return <div className="rounded border border-warm-200 px-3 py-2"><div className="text-xs text-warm-500">{label}</div><div className="mt-1 text-lg font-semibold text-warm-900">{value}</div></div>; }
function DataList({ title, items, empty }: { title: string; items: string[]; empty: string }): JSX.Element { return <section><h4 className="text-sm font-medium text-warm-800">{title}</h4>{items.length ? <ul className="mt-2 divide-y divide-warm-100 rounded border border-warm-200">{items.map((item) => <li key={item} className="flex items-center gap-2 px-3 py-2 text-xs text-warm-700"><FileText className="h-3.5 w-3.5 text-warm-500" />{item}</li>)}</ul> : <p className="mt-2 text-xs text-warm-500">{empty}</p>}</section>; }
