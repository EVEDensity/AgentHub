'use client';

import { useCallback, useEffect, useMemo, useState, type FormEvent, type JSX } from 'react';
import { Plus, RefreshCw, ShieldAlert, ShieldCheck, ShieldEllipsis, Trash2, ToggleLeft, ToggleRight } from 'lucide-react';
import type { PermissionRule } from '../../types';

interface PermissionModuleProps {
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
  fmtErr?: (detail: unknown, fallback: string) => string;
}

type RuleDraft = {
  agentId: string;
  toolPattern: string;
  pathPattern: string;
  behavior: PermissionRule['behavior'];
  priority: string;
};

type RuleInput = Partial<PermissionRule> & Record<string, unknown>;

const BEHAVIOR_META: Record<PermissionRule['behavior'], { label: string; cls: string; icon: JSX.Element }> = {
  allow: {
    label: '允许',
    cls: 'bg-success-50 text-success-700 border-success-200',
    icon: <ShieldCheck className="h-3.5 w-3.5" />,
  },
  deny: {
    label: '拒绝',
    cls: 'bg-danger-50 text-danger-700 border-danger-200',
    icon: <ShieldAlert className="h-3.5 w-3.5" />,
  },
  ask: {
    label: '确认',
    cls: 'bg-warning-50 text-warning-700 border-warning-200',
    icon: <ShieldEllipsis className="h-3.5 w-3.5" />,
  },
};

function normalizeRule(rule: RuleInput): PermissionRule {
  return {
    id: Number(rule.id ?? 0),
    agentId: String(rule.agentId ?? rule.agent_id ?? '*'),
    toolPattern: String(rule.toolPattern ?? rule.tool_pattern ?? '*'),
    pathPattern: String(rule.pathPattern ?? rule.path_pattern ?? '*'),
    behavior: (String(rule.behavior ?? 'ask') as PermissionRule['behavior']),
    source: String(rule.source ?? 'user'),
    priority: Number(rule.priority ?? 0),
    enabled: rule.enabled === undefined ? true : Boolean(rule.enabled),
    createdAt: String(rule.createdAt ?? rule.created_at ?? ''),
  };
}

function formatDate(value: string): string {
  if (!value) return '-';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN');
}

function formatAgent(agentId: string): string {
  return agentId === '*' ? '全局' : agentId;
}

export default function PermissionModule({ authHeaders, setNotice, fmtErr }: PermissionModuleProps): JSX.Element {
  const [rules, setRules] = useState<PermissionRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [savingId, setSavingId] = useState<number | null>(null);
  const [draft, setDraft] = useState<RuleDraft>({
    agentId: '*',
    toolPattern: '*',
    pathPattern: '*',
    behavior: 'ask',
    priority: '0',
  });

  const loadRules = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch('/api/admin/permissions/rules', { headers: { ...authHeaders() } });
      const data = await res.json();
      if (!res.ok) throw new Error((data && (data.detail || data.message)) || `HTTP ${res.status}`);
      const list = Array.isArray(data) ? data : Array.isArray(data.rules) ? data.rules : [];
      setRules(list.map((item: RuleInput) => normalizeRule(item)));
    } catch (err) {
      const message = fmtErr?.(err, '权限规则加载失败') ?? (err instanceof Error ? err.message : '权限规则加载失败');
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [authHeaders, fmtErr]);

  useEffect(() => {
    void loadRules();
  }, [loadRules]);

  const summary = useMemo(() => {
    const enabled = rules.filter((rule) => rule.enabled);
    return {
      total: rules.length,
      enabled: enabled.length,
      allow: rules.filter((rule) => rule.behavior === 'allow').length,
      deny: rules.filter((rule) => rule.behavior === 'deny').length,
      ask: rules.filter((rule) => rule.behavior === 'ask').length,
    };
  }, [rules]);

  const mutateRule = useCallback(async (ruleId: number, payload: Record<string, unknown>) => {
    setSavingId(ruleId);
    setError('');
    try {
      const res = await fetch(`/api/admin/permissions/rules/${ruleId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error((data && (data.detail || data.message)) || `HTTP ${res.status}`);
      await loadRules();
      setNotice('权限规则已更新');
    } catch (err) {
      setError(fmtErr?.(err, '更新权限规则失败') ?? (err instanceof Error ? err.message : '更新权限规则失败'));
    } finally {
      setSavingId(null);
    }
  }, [authHeaders, fmtErr, loadRules, setNotice]);

  const handleCreateRule = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError('');
    try {
      const res = await fetch('/api/admin/permissions/rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          agent_id: draft.agentId.trim() || '*',
          tool_pattern: draft.toolPattern.trim() || '*',
          path_pattern: draft.pathPattern.trim() || '*',
          behavior: draft.behavior,
          priority: Number(draft.priority) || 0,
        }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error((data && (data.detail || data.message)) || `HTTP ${res.status}`);
      setDraft({ agentId: '*', toolPattern: '*', pathPattern: '*', behavior: 'ask', priority: '0' });
      await loadRules();
      setNotice('权限规则已创建');
    } catch (err) {
      setError(fmtErr?.(err, '创建权限规则失败') ?? (err instanceof Error ? err.message : '创建权限规则失败'));
    }
  }, [authHeaders, draft, fmtErr, loadRules, setNotice]);

  const handleToggleEnabled = useCallback(async (rule: PermissionRule) => {
    await mutateRule(rule.id, { enabled: !rule.enabled });
  }, [mutateRule]);

  const handleDelete = useCallback(async (rule: PermissionRule) => {
    if (typeof window !== 'undefined' && !window.confirm(`删除权限规则 ${rule.toolPattern}？`)) return;
    setSavingId(rule.id);
    setError('');
    try {
      const res = await fetch(`/api/admin/permissions/rules/${rule.id}`, {
        method: 'DELETE',
        headers: { ...authHeaders() },
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error((data && (data.detail || data.message)) || `HTTP ${res.status}`);
      await loadRules();
      setNotice('权限规则已删除');
    } catch (err) {
      setError(fmtErr?.(err, '删除权限规则失败') ?? (err instanceof Error ? err.message : '删除权限规则失败'));
    } finally {
      setSavingId(null);
    }
  }, [authHeaders, fmtErr, loadRules, setNotice]);

  return (
    <section className="space-y-6">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
        <SummaryCard label="规则总数" value={summary.total} tone="primary" />
        <SummaryCard label="已启用" value={summary.enabled} tone="success" />
        <SummaryCard label="允许" value={summary.allow} tone="success" />
        <SummaryCard label="确认 / 拒绝" value={`${summary.ask} / ${summary.deny}`} tone="warm" />
      </div>

      <div className="rounded-2xl border border-warm-200 bg-warm-100">
        <div className="flex items-center justify-between gap-3 border-b border-warm-150 px-5 py-4">
          <div>
            <h3 className="text-base font-semibold text-warm-900">权限规则中心</h3>
            <p className="mt-1 text-xs text-warm-500">接入后端 `/api/admin/permissions/rules`，用于管理工具执行的 allow / deny / ask 规则。</p>
          </div>
          <button className="btn-ghost flex items-center gap-2 px-3 py-2 text-sm" onClick={() => { void loadRules(); }} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>

        {error && (
          <div className="border-b border-danger-100 bg-danger-50 px-5 py-3 text-sm text-danger-600">
            {error}
          </div>
        )}

        <form className="grid gap-3 border-b border-warm-150 bg-warm-50/60 px-5 py-4 lg:grid-cols-5" onSubmit={(e) => { void handleCreateRule(e); }}>
          <label className="flex flex-col gap-1 text-xs text-warm-500">
            Agent
            <input className="input-field" value={draft.agentId} onChange={(e) => setDraft((s) => ({ ...s, agentId: e.target.value }))} placeholder="*" />
          </label>
          <label className="flex flex-col gap-1 text-xs text-warm-500">
            工具模式
            <input className="input-field" value={draft.toolPattern} onChange={(e) => setDraft((s) => ({ ...s, toolPattern: e.target.value }))} placeholder="file_*" />
          </label>
          <label className="flex flex-col gap-1 text-xs text-warm-500">
            路径模式
            <input className="input-field" value={draft.pathPattern} onChange={(e) => setDraft((s) => ({ ...s, pathPattern: e.target.value }))} placeholder="*" />
          </label>
          <label className="flex flex-col gap-1 text-xs text-warm-500">
            动作
            <select className="input-field" value={draft.behavior} onChange={(e) => setDraft((s) => ({ ...s, behavior: e.target.value as PermissionRule['behavior'] }))}>
              <option value="allow">允许</option>
              <option value="ask">确认</option>
              <option value="deny">拒绝</option>
            </select>
          </label>
          <div className="flex items-end gap-2">
            <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs text-warm-500">
              优先级
              <input className="input-field" type="number" value={draft.priority} onChange={(e) => setDraft((s) => ({ ...s, priority: e.target.value }))} />
            </label>
            <button type="submit" className="btn-primary flex items-center gap-2 px-4 py-2" disabled={loading}>
              <Plus className="h-4 w-4" />
              创建
            </button>
          </div>
        </form>

        <div className="overflow-x-auto">
          {loading ? (
            <div className="px-5 py-8 text-sm text-warm-500">正在加载权限规则...</div>
          ) : rules.length === 0 ? (
            <div className="px-5 py-8 text-sm text-warm-500">暂无权限规则。可以先添加一条 allow / ask / deny 规则。</div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-warm-150 text-left text-xs font-medium text-warm-500">
                  <th className="px-5 py-2.5">Agent</th>
                  <th className="px-5 py-2.5">工具</th>
                  <th className="px-5 py-2.5">路径</th>
                  <th className="px-5 py-2.5">动作</th>
                  <th className="px-5 py-2.5">优先级</th>
                  <th className="px-5 py-2.5">来源</th>
                  <th className="px-5 py-2.5">状态</th>
                  <th className="px-5 py-2.5">创建时间</th>
                  <th className="px-5 py-2.5 text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => {
                  const behavior = BEHAVIOR_META[rule.behavior] || BEHAVIOR_META.ask;
                  const isBusy = savingId === rule.id;
                  return (
                    <tr key={rule.id} className="border-b border-warm-50 hover:bg-warm-50/60">
                      <td className="px-5 py-2.5 font-medium text-warm-800">{formatAgent(rule.agentId)}</td>
                      <td className="px-5 py-2.5 text-warm-600">{rule.toolPattern}</td>
                      <td className="px-5 py-2.5 text-warm-600">{rule.pathPattern}</td>
                      <td className="px-5 py-2.5">
                        <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium ${behavior.cls}`}>
                          {behavior.icon}
                          {behavior.label}
                        </span>
                      </td>
                      <td className="px-5 py-2.5 text-warm-600">{rule.priority}</td>
                      <td className="px-5 py-2.5 text-warm-500">{rule.source}</td>
                      <td className="px-5 py-2.5">
                        <button
                          className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors ${
                            rule.enabled ? 'bg-success-50 text-success-700' : 'bg-warm-100 text-warm-500'
                          }`}
                          onClick={() => { void handleToggleEnabled(rule); }}
                          disabled={isBusy}
                        >
                          {rule.enabled ? <ToggleRight className="h-4 w-4" /> : <ToggleLeft className="h-4 w-4" />}
                          {rule.enabled ? '启用' : '停用'}
                        </button>
                      </td>
                      <td className="px-5 py-2.5 text-warm-500">{formatDate(rule.createdAt)}</td>
                      <td className="px-5 py-2.5 text-right">
                        <button
                          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-danger-500 hover:bg-danger-50 disabled:opacity-50"
                          onClick={() => { void handleDelete(rule); }}
                          disabled={isBusy}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          删除
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </section>
  );
}

function SummaryCard({ label, value, tone }: { label: string; value: number | string; tone: 'primary' | 'success' | 'warm' }): JSX.Element {
  const toneClass: Record<'primary' | 'success' | 'warm', string> = {
    primary: 'border-primary-100 bg-primary-50 text-primary-700',
    success: 'border-success-100 bg-success-50 text-success-700',
    warm: 'border-warm-200 bg-warm-50 text-warm-700',
  };

  return (
    <div className={`rounded-xl border px-4 py-3 ${toneClass[tone]}`}>
      <div className="text-[11px] font-medium opacity-75">{label}</div>
      <div className="mt-1 text-xl font-semibold">{value}</div>
    </div>
  );
}
