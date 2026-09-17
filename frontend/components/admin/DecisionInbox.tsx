'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type JSX } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  RefreshCw,
  RotateCcw,
  XCircle,
} from 'lucide-react';
import { useWorkspaceStore } from '../../stores/workspaceStore';
import type {
  DecisionListResponse,
  DecisionResolution,
  DecisionResolutionResponse,
  MissionDecision,
} from '../../types';

interface DecisionInboxProps {
  authHeaders: () => Record<string, string>;
  setNotice: (message: string) => void;
  fmtErr?: (detail: unknown, fallback: string) => string;
}

const RESOLUTION_META: Record<DecisionResolution, { label: string; description: string }> = {
  RETRY_WORK_UNIT: {
    label: '重试 WorkUnit',
    description: '创建受控重试机会，由调度链路重新领取执行。',
  },
  FAIL_MISSION: {
    label: '终止 Mission',
    description: '将该问题判定为不可恢复，并使 Mission 进入失败状态。',
  },
};

const REASON_LABELS: Record<MissionDecision['reasonCode'], string> = {
  no_applicable_policy: '无适用评估策略',
  ambiguous_policy: '评估策略存在歧义',
  invalid_configuration: '评估配置无效',
  unsupported_evaluator: '评估器不受支持',
  artifact_requirements_not_met: 'Artifact 要求未满足',
};

function formatDate(value?: string): string {
  if (!value) return '-';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN');
}

async function readResponseBody(response: Response): Promise<Record<string, unknown>> {
  const body = await response.json().catch(() => null);
  return body && typeof body === 'object' ? body as Record<string, unknown> : {};
}

function responseMessage(body: Record<string, unknown>, fallback: string): string {
  const detail = body.detail ?? body.message;
  return typeof detail === 'string' && detail ? detail : fallback;
}

export default function DecisionInbox({ authHeaders, setNotice, fmtErr }: DecisionInboxProps): JSX.Element {
  const workspaceId = useWorkspaceStore((state) => state.currentWorkspaceId);
  const [decisions, setDecisions] = useState<MissionDecision[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [resolution, setResolution] = useState<DecisionResolution>('RETRY_WORK_UNIT');
  const [rationale, setRationale] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const activeLoad = useRef<AbortController | null>(null);

  const selected = useMemo(
    () => decisions.find((decision) => decision.id === selectedId) ?? null,
    [decisions, selectedId],
  );

  const loadDecisions = useCallback(async (options: { preserveError?: boolean } = {}) => {
    activeLoad.current?.abort();
    const controller = new AbortController();
    activeLoad.current = controller;
    setLoading(true);
    if (!options.preserveError) setError('');
    try {
      const query = new URLSearchParams({ workspaceId, status: 'PENDING', limit: '100', offset: '0' });
      const response = await fetch(`/api/v1/missions/decisions?${query.toString()}`, {
        headers: { ...authHeaders() },
        signal: controller.signal,
      });
      const body = await readResponseBody(response);
      if (!response.ok) throw new Error(responseMessage(body, `HTTP ${response.status}`));
      const next = Array.isArray((body as Partial<DecisionListResponse>).decisions)
        ? (body as unknown as DecisionListResponse).decisions
        : [];
      setDecisions(next);
      setSelectedId((current) => next.some((decision) => decision.id === current) ? current : (next[0]?.id ?? ''));
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return;
      const fallback = caught instanceof Error ? caught.message : '待决策列表加载失败';
      setError(fmtErr?.(fallback, '待决策列表加载失败') ?? fallback);
      setDecisions([]);
      setSelectedId('');
    } finally {
      if (activeLoad.current === controller) {
        activeLoad.current = null;
        if (!controller.signal.aborted) setLoading(false);
      }
    }
  }, [authHeaders, fmtErr, workspaceId]);

  useEffect(() => {
    void loadDecisions();
    return () => {
      activeLoad.current?.abort();
      activeLoad.current = null;
    };
  }, [loadDecisions]);

  useEffect(() => {
    if (!selected) return;
    setResolution(selected.recommendedOption);
    setRationale('');
  }, [selected?.id, selected?.version]);

  const handleResolve = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected || submitting) return;
    const normalizedRationale = rationale.trim();
    if (!normalizedRationale) {
      setError('请填写决策依据');
      return;
    }
    if (!selected.options.includes(resolution)) {
      setError('该 Decision 不支持所选处理方式，请刷新后重试');
      return;
    }
    if (
      resolution === 'FAIL_MISSION'
      && typeof window !== 'undefined'
      && !window.confirm(`确认终止 Mission ${selected.missionId}？此操作会结束整个 Mission。`)
    ) return;

    setSubmitting(true);
    setError('');
    try {
      const response = await fetch(
        `/api/v1/missions/${encodeURIComponent(selected.missionId)}/decisions/${encodeURIComponent(selected.id)}/resolve`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({
            expectedVersion: selected.version,
            resolution,
            rationale: normalizedRationale,
          }),
        },
      );
      const body = await readResponseBody(response);
      if (response.status === 409) {
        setError(`Decision 已被其他操作者更新：${responseMessage(body, '请基于最新状态重新处理')}`);
        await loadDecisions({ preserveError: true });
        return;
      }
      if (!response.ok) throw new Error(responseMessage(body, `HTTP ${response.status}`));
      const result = body as unknown as DecisionResolutionResponse;
      if (!result.decision || result.decision.status !== 'RESOLVED') {
        throw new Error('服务端未返回已处理的 Decision');
      }
      setNotice(`Decision ${selected.id} 已处理：${RESOLUTION_META[resolution].label}`);
      await loadDecisions();
    } catch (caught) {
      const fallback = caught instanceof Error ? caught.message : 'Decision 处理失败';
      setError(fmtErr?.(fallback, 'Decision 处理失败') ?? fallback);
    } finally {
      setSubmitting(false);
    }
  }, [authHeaders, fmtErr, loadDecisions, rationale, resolution, selected, setNotice, submitting]);

  return (
    <section className="space-y-4" aria-labelledby="decision-inbox-title">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="decision-inbox-title" className="text-h2 text-warm-900">决策收件箱</h2>
          <p className="mt-1 text-sm text-warm-500">工作空间 {workspaceId} 的待处理执行决策</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded border border-warning-200 bg-warning-50 px-2 py-1 text-xs text-warning-700">
            待处理 {decisions.length}
          </span>
          <button
            type="button"
            className="inline-flex h-8 w-8 items-center justify-center rounded border border-warm-200 text-warm-600 hover:bg-warm-50 disabled:opacity-50"
            onClick={() => void loadDecisions()}
            disabled={loading || submitting}
            aria-label="刷新待决策列表"
            title="刷新待决策列表"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </header>

      {error && (
        <div role="alert" className="flex items-start justify-between gap-3 rounded border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          <span className="flex items-start gap-2"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{error}</span>
          {!submitting && (
            <button type="button" className="shrink-0 font-medium hover:underline" onClick={() => void loadDecisions()}>
              重试
            </button>
          )}
        </div>
      )}

      <div className="min-h-[420px] overflow-hidden rounded border border-warm-200 bg-white md:grid md:grid-cols-[minmax(260px,0.8fr)_minmax(0,1.4fr)]">
        <div className="border-b border-warm-200 md:border-b-0 md:border-r">
          {loading && decisions.length === 0 ? (
            <div className="flex min-h-52 items-center justify-center text-sm text-warm-500">
              <RefreshCw className="mr-2 h-4 w-4 animate-spin" />正在加载待决策事项
            </div>
          ) : decisions.length === 0 ? (
            <div className="flex min-h-52 flex-col items-center justify-center px-5 text-center">
              <CheckCircle2 className="h-7 w-7 text-success-500" />
              <p className="mt-3 text-sm font-medium text-warm-800">当前没有待处理 Decision</p>
              <p className="mt-1 text-xs text-warm-500">新决策由真实评估和执行链路产生。</p>
            </div>
          ) : (
            <ul aria-label="待处理 Decision" className="divide-y divide-warm-150">
              {decisions.map((decision) => (
                <li key={decision.id}>
                  <button
                    type="button"
                    className={`w-full px-4 py-3 text-left transition-colors ${selectedId === decision.id ? 'bg-primary-50' : 'hover:bg-warm-50'}`}
                    onClick={() => setSelectedId(decision.id)}
                    aria-pressed={selectedId === decision.id}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium text-warm-900">{REASON_LABELS[decision.reasonCode]}</span>
                      <span className="shrink-0 text-xs text-warm-500">v{decision.version}</span>
                    </span>
                    <span className="mt-1 block truncate font-mono text-xs text-warm-500">{decision.missionId}</span>
                    <span className="mt-2 flex items-center gap-1 text-xs text-warm-500">
                      <Clock3 className="h-3.5 w-3.5" />{formatDate(decision.requestedAt)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="min-w-0 p-4 md:p-5">
          {selected ? (
            <form onSubmit={handleResolve} className="space-y-5">
              <div>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-h3 text-warm-900">{REASON_LABELS[selected.reasonCode]}</h3>
                  <span className="rounded border border-warning-200 bg-warning-50 px-2 py-1 text-xs text-warning-700">PENDING</span>
                </div>
                <p className="mt-2 text-sm leading-6 text-warm-700">{selected.riskSummary}</p>
              </div>

              <dl className="grid grid-cols-1 gap-x-5 gap-y-3 text-sm sm:grid-cols-2">
                <Detail label="Mission" value={selected.missionId} mono />
                <Detail label="WorkUnit" value={selected.workUnitId} mono />
                <Detail label="执行尝试" value={String(selected.attempt)} />
                <Detail label="过期时间" value={formatDate(selected.expiresAt)} />
                <Detail label="请求者" value={selected.requestedBy.displayName || selected.requestedBy.id} />
                <Detail label="验收标准" value={selected.criterionIds.join(', ') || '-'} mono />
              </dl>

              <fieldset disabled={submitting} className="space-y-2">
                <legend className="mb-2 text-sm font-medium text-warm-800">处理方式</legend>
                {selected.options.map((option) => (
                  <label key={option} className={`flex cursor-pointer gap-3 rounded border p-3 ${resolution === option ? 'border-primary-400 bg-primary-50' : 'border-warm-200'}`}>
                    <input
                      type="radio"
                      name="resolution"
                      value={option}
                      checked={resolution === option}
                      onChange={() => setResolution(option)}
                      className="mt-1"
                    />
                    <span>
                      <span className="flex items-center gap-2 text-sm font-medium text-warm-900">
                        {option === 'RETRY_WORK_UNIT' ? <RotateCcw className="h-4 w-4" /> : <XCircle className="h-4 w-4 text-danger-600" />}
                        {RESOLUTION_META[option].label}
                        {selected.recommendedOption === option && <span className="text-xs font-normal text-primary-600">推荐</span>}
                      </span>
                      <span className="mt-1 block text-xs text-warm-500">{RESOLUTION_META[option].description}</span>
                    </span>
                  </label>
                ))}
              </fieldset>

              <div>
                <label htmlFor="decision-rationale" className="text-sm font-medium text-warm-800">决策依据</label>
                <textarea
                  id="decision-rationale"
                  value={rationale}
                  onChange={(event) => setRationale(event.target.value)}
                  maxLength={10000}
                  required
                  disabled={submitting}
                  rows={4}
                  className="mt-2 w-full resize-y rounded border border-warm-200 bg-white px-3 py-2 text-sm text-warm-900 outline-none focus:border-primary-400 disabled:opacity-60"
                  placeholder="记录判断依据、风险取舍和后续预期"
                />
                <div className="mt-1 text-right text-xs text-warm-400">{rationale.length} / 10000</div>
              </div>

              <button
                type="submit"
                disabled={submitting || !rationale.trim()}
                className={`inline-flex min-h-9 items-center justify-center gap-2 rounded px-4 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50 ${resolution === 'FAIL_MISSION' ? 'bg-danger-600 hover:bg-danger-700' : 'bg-primary-600 hover:bg-primary-700'}`}
              >
                {submitting && <RefreshCw className="h-4 w-4 animate-spin" />}
                {submitting ? '正在提交' : RESOLUTION_META[resolution].label}
              </button>
            </form>
          ) : (
            <div className="flex min-h-52 items-center justify-center text-sm text-warm-500">选择一项 Decision 查看详情</div>
          )}
        </div>
      </div>
    </section>
  );
}

function Detail({ label, value, mono = false }: { label: string; value: string; mono?: boolean }): JSX.Element {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-warm-500">{label}</dt>
      <dd className={`mt-1 break-words text-warm-800 ${mono ? 'font-mono text-xs' : ''}`}>{value}</dd>
    </div>
  );
}
