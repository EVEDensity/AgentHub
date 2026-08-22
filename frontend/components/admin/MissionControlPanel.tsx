'use client';

import { useCallback, useEffect, useMemo, useState, type JSX } from 'react';
import { AlertTriangle, CheckCircle2, Play, RefreshCw, Square } from 'lucide-react';
import { useWorkspaceStore } from '../../stores/workspaceStore';

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

function statusClass(status: string): string {
  if (['SUCCEEDED', 'READY'].includes(status)) return 'text-success-700 bg-success-50 border-success-200';
  if (['FAILED', 'CANCELLED'].includes(status)) return 'text-danger-700 bg-danger-50 border-danger-200';
  if (['RUNNING', 'VERIFYING', 'WAITING_DECISION'].includes(status)) return 'text-primary-700 bg-primary-50 border-primary-200';
  return 'text-warm-700 bg-warm-50 border-warm-200';
}

export default function MissionControlPanel({ authHeaders, setNotice, fmtErr }: MissionControlPanelProps): JSX.Element {
  const workspaceId = useWorkspaceStore((state) => state.currentWorkspaceId);
  const [missions, setMissions] = useState<Mission[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [workUnits, setWorkUnits] = useState<WorkUnit[]>([]);
  const [artifacts, setArtifacts] = useState<RecordItem[]>([]);
  const [evidence, setEvidence] = useState<RecordItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState('');
  const [objective, setObjective] = useState('');
  const [contract, setContract] = useState(DEFAULT_CONTRACT);

  const selected = useMemo(() => missions.find((mission) => mission.id === selectedId) ?? null, [missions, selectedId]);

  const loadMissions = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const response = await fetch(`/api/v1/missions?workspaceId=${encodeURIComponent(workspaceId)}&limit=100&offset=0`, { headers: authHeaders() });
      const body = await json(response);
      if (!response.ok) throw new Error(message(body, `HTTP ${response.status}`));
      const next = Array.isArray(body.missions) ? body.missions as Mission[] : [];
      setMissions(next);
      setSelectedId((current) => next.some((item) => item.id === current) ? current : (next[0]?.id ?? ''));
    } catch (caught) {
      const detail = caught instanceof Error ? caught.message : 'Mission 列表加载失败';
      setError(fmtErr?.(detail, 'Mission 列表加载失败') ?? detail);
      setMissions([]); setSelectedId('');
    } finally { setLoading(false); }
  }, [authHeaders, fmtErr, workspaceId]);

  const loadDetail = useCallback(async (missionId: string) => {
    if (!missionId) return;
    setDetailLoading(true);
    try {
      const headers = authHeaders();
      const [unitsResponse, artifactsResponse, evidenceResponse] = await Promise.all([
        fetch(`/api/v1/missions/${encodeURIComponent(missionId)}/work-units?limit=100&offset=0`, { headers }),
        fetch(`/api/v1/missions/${encodeURIComponent(missionId)}/artifacts?limit=100&offset=0`, { headers }),
        fetch(`/api/v1/missions/${encodeURIComponent(missionId)}/evidence?limit=100&offset=0`, { headers }),
      ]);
      const [unitsBody, artifactsBody, evidenceBody] = await Promise.all([json(unitsResponse), json(artifactsResponse), json(evidenceResponse)]);
      if (!unitsResponse.ok) throw new Error(message(unitsBody, `WorkUnit HTTP ${unitsResponse.status}`));
      if (!artifactsResponse.ok) throw new Error(message(artifactsBody, `Artifact HTTP ${artifactsResponse.status}`));
      if (!evidenceResponse.ok) throw new Error(message(evidenceBody, `Evidence HTTP ${evidenceResponse.status}`));
      setWorkUnits(Array.isArray(unitsBody.workUnits) ? unitsBody.workUnits as WorkUnit[] : []);
      setArtifacts(Array.isArray(artifactsBody.artifacts) ? artifactsBody.artifacts : []);
      setEvidence(Array.isArray(evidenceBody.evidence) ? evidenceBody.evidence : []);
    } catch (caught) {
      const detail = caught instanceof Error ? caught.message : 'Mission 详情加载失败';
      setError(fmtErr?.(detail, 'Mission 详情加载失败') ?? detail);
      setWorkUnits([]); setArtifacts([]); setEvidence([]);
    } finally { setDetailLoading(false); }
  }, [authHeaders, fmtErr]);

  useEffect(() => { void loadMissions(); }, [loadMissions]);
  useEffect(() => { void loadDetail(selectedId); }, [loadDetail, selectedId]);

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
        <div className="flex gap-2"><button type="button" className="inline-flex h-9 items-center gap-2 rounded border border-warm-200 px-3 text-sm hover:bg-warm-50 disabled:opacity-50" onClick={() => void loadMissions()} disabled={loading}><RefreshCw className={loading ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} />刷新</button><button type="button" className="inline-flex h-9 items-center gap-2 rounded bg-primary-600 px-3 text-sm text-white hover:bg-primary-700" onClick={() => setShowCreate((value) => !value)}>创建 Mission</button></div>
      </header>
      {error && <div role="alert" className="flex items-start gap-2 rounded border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{error}</div>}
      {showCreate && <form onSubmit={createMission} className="space-y-3 rounded border border-warm-200 bg-white p-4"><div className="grid gap-3 md:grid-cols-2"><label className="text-sm text-warm-700">标题<input required value={title} onChange={(event) => setTitle(event.target.value)} className="mt-1 w-full rounded border border-warm-200 px-3 py-2" /></label><label className="text-sm text-warm-700">目标<textarea required value={objective} onChange={(event) => setObjective(event.target.value)} rows={2} className="mt-1 w-full rounded border border-warm-200 px-3 py-2" /></label></div><label className="block text-sm text-warm-700">Contract JSON<textarea required value={contract} onChange={(event) => setContract(event.target.value)} rows={8} className="mt-1 w-full rounded border border-warm-200 px-3 py-2 font-mono text-xs" /></label><div className="flex justify-end gap-2"><button type="button" className="rounded border border-warm-200 px-3 py-2 text-sm" onClick={() => setShowCreate(false)}>取消</button><button type="submit" disabled={creating} className="rounded bg-primary-600 px-3 py-2 text-sm text-white disabled:opacity-50">{creating ? '创建中' : '提交创建'}</button></div></form>}
      <div className="min-h-[460px] overflow-hidden rounded border border-warm-200 bg-white md:grid md:grid-cols-[minmax(260px,0.8fr)_minmax(0,1.5fr)]">
        <div className="border-b border-warm-200 md:border-b-0 md:border-r">{loading && missions.length === 0 ? <div className="p-6 text-sm text-warm-500">正在加载 Mission</div> : missions.length === 0 ? <div className="p-6 text-sm text-warm-500">当前工作空间暂无 Mission</div> : <ul className="divide-y divide-warm-150">{missions.map((mission) => <li key={mission.id}><button type="button" className={`w-full px-4 py-3 text-left hover:bg-warm-50 ${selectedId === mission.id ? 'bg-primary-50' : ''}`} onClick={() => setSelectedId(mission.id)}><span className="block truncate text-sm font-medium text-warm-900">{mission.title}</span><span className="mt-1 block truncate font-mono text-xs text-warm-500">{mission.id}</span><span className={`mt-2 inline-flex rounded border px-2 py-0.5 text-xs ${statusClass(mission.status)}`}>{mission.status}</span></button></li>)}</ul>}</div>
        <div className="min-w-0 p-4 md:p-5">{selected ? <div className="space-y-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="text-h3 text-warm-900">{selected.title}</h3><p className="mt-1 text-sm text-warm-600">{selected.objective}</p><p className="mt-2 font-mono text-xs text-warm-500">{selected.id}</p></div><div className="flex gap-2">{selected.status === 'READY' && <button type="button" className="inline-flex items-center gap-1 rounded bg-primary-600 px-3 py-2 text-sm text-white" onClick={() => void runMissionCommand('start')}><Play className="h-4 w-4" />启动</button>}{!['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(selected.status) && <button type="button" className="inline-flex items-center gap-1 rounded border border-danger-200 px-3 py-2 text-sm text-danger-700" onClick={() => void runMissionCommand('cancel')}><Square className="h-4 w-4" />取消</button>}</div></div><div className="grid gap-3 sm:grid-cols-3"><Metric label="WorkUnit" value={String(workUnits.length)} /><Metric label="Artifact" value={String(artifacts.length)} /><Metric label="Evidence" value={String(evidence.length)} /></div>{detailLoading ? <p className="text-sm text-warm-500">正在加载详情</p> : <div className="space-y-4"><DataList title="WorkUnit 状态" items={workUnits.map((unit) => `${unit.id} · ${unit.kind} · ${unit.status}${unit.attempt ? ` · attempt ${unit.attempt}` : ''}`)} empty="暂无 WorkUnit" /><DataList title="Artifact" items={artifacts.map((item) => String(item.id ?? item.artifactId ?? '未命名 Artifact'))} empty="暂无 Artifact" /><DataList title="Evidence" items={evidence.map((item) => String(item.id ?? item.evidenceId ?? '未命名 Evidence'))} empty="暂无 Evidence" /></div>}</div> : <div className="flex min-h-52 items-center justify-center text-sm text-warm-500">选择一个 Mission 查看真实状态</div>}</div>
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }): JSX.Element { return <div className="rounded border border-warm-200 px-3 py-2"><div className="text-xs text-warm-500">{label}</div><div className="mt-1 text-lg font-semibold text-warm-900">{value}</div></div>; }
function DataList({ title, items, empty }: { title: string; items: string[]; empty: string }): JSX.Element { return <section><h4 className="text-sm font-medium text-warm-800">{title}</h4>{items.length ? <ul className="mt-2 divide-y divide-warm-100 rounded border border-warm-200">{items.map((item) => <li key={item} className="flex items-center gap-2 px-3 py-2 text-xs text-warm-700"><CheckCircle2 className="h-3.5 w-3.5 text-success-600" />{item}</li>)}</ul> : <p className="mt-2 text-xs text-warm-500">{empty}</p>}</section>; }
