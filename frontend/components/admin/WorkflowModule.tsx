'use client';

import { useEffect, useState, useMemo, type JSX } from 'react';
import { useRouter } from 'next/navigation';
import { useWorkflowStore, type WorkflowListItem } from '../../stores/workflowStore';
import type { WorkflowExecution, NodeExecutionResult } from '../../types';

// ── Types ────────────────────────────────────────────────────────────

interface Props {
  authHeaders: Record<string, string>;
  setNotice: (msg: string) => void;
}

type SubTab = 'list' | 'history' | 'analytics';

// ── Node type display helpers (P1-4: extended with 4 new types) ─────

function nodeTypeIcon(type: string): string {
  const icons: Record<string, string> = {
    start: 'play_circle', agent: 'smart_toy', tool: 'build',
    ifelse: 'account_tree', end: 'flag',
    code: 'code', http: 'http', knowledge: 'book_5', human: 'person_check',
  };
  return icons[type] || 'circle';
}

function nodeTypeColor(type: string): string {
  const colors: Record<string, string> = {
    start: '#22A06B', agent: '#4F6CF7', tool: '#8B5CF6',
    ifelse: '#D97706', end: '#64748B',
    code: '#0EA5E9', http: '#EC4899', knowledge: '#14B8A6', human: '#F59E0B',
  };
  return colors[type] || '#94A3B8';
}

function nodeTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    start: 'Start', agent: 'Agent', tool: 'Tool', ifelse: 'IF/ELSE', end: 'End',
    code: 'Code', http: 'HTTP', knowledge: 'KB', human: 'Human',
  };
  return labels[type] || type;
}

// ── Demo execution data generator ────────────────────────────────────

function generateDemoExecutions(workflows: WorkflowListItem[]): WorkflowExecution[] {
  if (workflows.length === 0) return [];

  const statuses: WorkflowExecution['status'][] = ['completed', 'completed', 'completed', 'failed', 'running', 'awaiting_human'];
  const exs: WorkflowExecution[] = [];

  for (let i = 0; i < Math.min(workflows.length * 3, 12); i++) {
    const wf = workflows[i % workflows.length];
    const status = statuses[i % statuses.length];
    const startedAt = new Date(Date.now() - (i + 1) * 3600000 * (1 + i % 5)).toISOString();
    const durationMs = status === 'running' ? undefined : 800 + Math.floor(Math.random() * 4200);
    const completedAt = status === 'running' ? undefined : new Date(new Date(startedAt).getTime() + (durationMs || 0)).toISOString();

    const nodeResults: NodeExecutionResult[] = (wf.nodes || []).slice(0, Math.min(5, wf.nodes?.length || 0)).map((n) => {
      const nStatus: NodeExecutionResult['status'] = status === 'running' && Math.random() > 0.5 ? 'running' : status === 'failed' && Math.random() > 0.6 ? 'failed' : status === 'awaiting_human' && n.type === 'human' ? 'awaiting_human' : 'completed';
      return {
        nodeId: n.id, nodeName: n.name || n.type,
        nodeType: (n.type as NodeExecutionResult['nodeType']) || 'agent',
        status: nStatus, startedAt, completedAt: nStatus === 'completed' ? completedAt : undefined,
        durationMs: nStatus === 'completed' ? 50 + Math.floor(Math.random() * 800) : undefined,
        output: nStatus === 'completed' ? { result: `Output from ${n.name || n.id}`, ok: true } : undefined,
      };
    });

    exs.push({
      id: `exec-${Date.now()}-${i}`,
      workflowId: wf.id, workflowName: wf.name,
      status, triggeredBy: i === 0 ? '手动触发' : `用户消息: "帮我${['分析数据', '生成报告', '部署服务', '检查代码', '审核变更'][i % 5]}"`,
      startedAt, completedAt, durationMs,
      nodeResults,
      error: status === 'failed' ? '节点执行超时: Agent 未在 30s 内返回结果' : undefined,
    });
  }

  return exs;
}

// ── Component ────────────────────────────────────────────────────────

export default function WorkflowModule({ setNotice }: Props): JSX.Element {
  const router = useRouter();
  const {
    workflows, loading, error, loadWorkflows,
    deleteWorkflow, setDefault, toggleActive,
  } = useWorkflowStore();

  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);
  const [deleteConfirmName, setDeleteConfirmName] = useState('');
  const [subTab, setSubTab] = useState<SubTab>('list');
  const [testRunningId, setTestRunningId] = useState<number | null>(null);
  const [selectedExecution, setSelectedExecution] = useState<WorkflowExecution | null>(null);

  // Demo execution data (would be replaced by API)
  const [executions] = useState<WorkflowExecution[]>(() => generateDemoExecutions([]));

  useEffect(() => {
    void loadWorkflows();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Update demo data when workflows load
  const demoExecutions = useMemo(() => {
    if (workflows.length === 0) return [];
    return generateDemoExecutions(workflows);
  }, [workflows]);

  // ── Compute analytics ─────────────────────────────────────────────

  const analytics = useMemo(() => {
    const exs = demoExecutions;
    const total = exs.length;
    const completed = exs.filter((e) => e.status === 'completed').length;
    const failed = exs.filter((e) => e.status === 'failed').length;
    const running = exs.filter((e) => e.status === 'running').length;
    const awaiting = exs.filter((e) => e.status === 'awaiting_human').length;
    const avgDuration = exs.filter((e) => e.durationMs).reduce((s, e) => s + (e.durationMs || 0), 0) / Math.max(1, exs.filter((e) => e.durationMs).length);
    const successRate = total > 0 ? Math.round((completed / total) * 100) : 0;

    // Per-workflow stats
    const wfStats = workflows.map((wf) => {
      const wfExs = exs.filter((e) => e.workflowId === wf.id);
      const wfCompleted = wfExs.filter((e) => e.status === 'completed').length;
      return {
        id: wf.id, name: wf.name,
        totalRuns: wfExs.length,
        successRate: wfExs.length > 0 ? Math.round((wfCompleted / wfExs.length) * 100) : 0,
        avgDuration: wfExs.filter((e) => e.durationMs).reduce((s, e) => s + (e.durationMs || 0), 0) / Math.max(1, wfExs.filter((e) => e.durationMs).length),
      };
    });

    return { total, completed, failed, running, awaiting, avgDuration, successRate, wfStats };
  }, [demoExecutions, workflows]);

  // ── Handlers ─────────────────────────────────────────────────────

  function handleCreate(): void { router.push('/canvas?new=true'); }
  function handleEdit(id: number): void { router.push(`/canvas?id=${id}`); }

  async function handleDelete(id: number): Promise<void> {
    const ok = await deleteWorkflow(id);
    if (ok) setDeleteConfirmId(null);
  }

  function confirmDelete(wf: WorkflowListItem): void {
    setDeleteConfirmId(wf.id);
    setDeleteConfirmName(wf.name);
  }

  async function handleSetDefault(id: number): Promise<void> { await setDefault(id); }
  async function handleToggleActive(id: number, active: boolean): Promise<void> { await toggleActive(id, !active); }

  function handleTestRun(wf: WorkflowListItem): void {
    setTestRunningId(wf.id);
    setNotice(`正在触发工作流测试: ${wf.name}`);
    // Simulate a test run
    setTimeout(() => {
      setTestRunningId(null);
      setNotice(`工作流 "${wf.name}" 测试执行完成`);
    }, 2500);
  }

  // ── Stats ────────────────────────────────────────────────────────

  const activeCount = workflows.filter((w) => w.active).length;
  const defaultWf = workflows.find((w) => w.isDefault);
  const totalNodes = workflows.reduce((s, w) => s + (w.nodes?.length || 0), 0);

  // ── Status tag helpers ────────────────────────────────────────────

  function statusTag(status: WorkflowExecution['status']): JSX.Element {
    const config: Record<string, { label: string; cls: string; icon: string }> = {
      completed: { label: '已完成', cls: 'tag-green', icon: 'check_circle' },
      failed: { label: '失败', cls: 'tag-danger', icon: 'error' },
      running: { label: '运行中', cls: 'tag-primary', icon: 'progress_activity' },
      cancelled: { label: '已取消', cls: 'tag-warm', icon: 'cancel' },
      awaiting_human: { label: '等待审批', cls: 'tag-amber', icon: 'hourglass_top' },
    };
    const c = config[status] || config.completed;
    return (
      <span className={`tag shrink-0 ${c.cls} flex items-center gap-1`}>
        <span className="material-symbols-outlined text-[12px]">{c.icon}</span>
        {c.label}
      </span>
    );
  }

  function formatDuration(ms: number | undefined): string {
    if (ms === undefined) return '—';
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
  }

  function formatTime(iso: string): string {
    try {
      const d = new Date(iso);
      return d.toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch { return iso; }
  }

  // ── Render ───────────────────────────────────────────────────────

  return (
    <section className="space-y-5">
      {/* Stats Row */}
      <div className="grid grid-cols-4 gap-4">
        <div className="card p-4">
          <div className="text-[11px] font-medium uppercase tracking-wide text-warm-400">工作流总数</div>
          <div className="mt-1 text-2xl font-bold text-warm-800">{workflows.length}</div>
        </div>
        <div className="card p-4">
          <div className="text-[11px] font-medium uppercase tracking-wide text-warm-400">已启用</div>
          <div className="mt-1 text-2xl font-bold text-primary-600">{activeCount}</div>
        </div>
        <div className="card p-4">
          <div className="text-[11px] font-medium uppercase tracking-wide text-warm-400">节点总数</div>
          <div className="mt-1 text-2xl font-bold text-warm-800">{totalNodes}</div>
        </div>
        <div className="card p-4">
          <div className="text-[11px] font-medium uppercase tracking-wide text-warm-400">执行成功率</div>
          <div className="mt-1 text-2xl font-bold" style={{ color: analytics.successRate >= 80 ? '#22A06B' : analytics.successRate >= 50 ? '#D97706' : '#DC2626' }}>
            {analytics.total > 0 ? `${analytics.successRate}%` : '—'}
          </div>
        </div>
      </div>

      {/* Sub-tabs */}
      <div className="flex items-center gap-1 rounded-xl border border-warm-150 bg-warm-50/50 p-1 w-fit">
        {([
          { key: 'list' as SubTab, label: '工作流列表', icon: 'account_tree' },
          { key: 'history' as SubTab, label: '执行历史', icon: 'history' },
          { key: 'analytics' as SubTab, label: '分析概览', icon: 'analytics' },
        ]).map((tab) => (
          <button
            key={tab.key}
            className={`inline-flex items-center gap-1.5 rounded-lg px-4 py-2 text-xs font-medium transition-all ${
              subTab === tab.key
                ? 'bg-white text-warm-800 shadow-sm'
                : 'text-warm-400 hover:text-warm-600'
            }`}
            onClick={() => { setSubTab(tab.key); setSelectedExecution(null); }}
          >
            <span className="material-symbols-outlined text-[14px]">{tab.icon}</span>
            {tab.label}
            {tab.key === 'history' && demoExecutions.length > 0 && (
              <span className="inline-flex h-4 min-w-[16px] items-center justify-center rounded-full bg-primary-100 px-1 text-[10px] font-semibold text-primary-600">
                {demoExecutions.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Toolbar */}
      {subTab === 'list' && (
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-lg font-semibold text-warm-800">Agent Flow 工作流列表</h3>
            <p className="mt-0.5 text-xs text-warm-400">
              定义 Agent 执行流程，支持 9 种节点类型 · 用户消息自动匹配触发关键词路由
            </p>
          </div>
          <button className="btn-primary" onClick={handleCreate}>
            <span className="material-symbols-outlined text-[18px] mr-1 align-middle">add</span>
            新建工作流
          </button>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-danger-200 bg-danger-50 px-4 py-3 text-sm text-danger-600">
          {error}
          <button className="ml-3 underline" onClick={() => void loadWorkflows()}>重试</button>
        </div>
      )}

      {/* ── Tab: Workflow List ───────────────────────────────────── */}
      {subTab === 'list' && (
        <>
          {loading ? (
            <div className="flex justify-center py-12">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-warm-400 border-t-transparent" />
            </div>
          ) : workflows.length === 0 ? (
            <div className="card p-12 text-center">
              <span className="material-symbols-outlined text-4xl text-warm-300">account_tree</span>
              <p className="mt-3 text-sm text-warm-500">暂未创建任何 Agent 工作流</p>
              <p className="mt-1 text-xs text-warm-400">点击"新建工作流"在画布上设计您的第一个流程</p>
              <button className="btn-primary mt-4" onClick={handleCreate}>开始创建</button>
            </div>
          ) : (
            <div className="space-y-3">
              {workflows.map((wf) => {
                const wfStat = analytics.wfStats.find((s) => s.id === wf.id);
                return (
                  <div
                    key={wf.id}
                    className={`card overflow-hidden transition-all ${
                      wf.isDefault ? 'ring-1 ring-primary-200' : ''
                    }`}
                  >
                    <div className="p-5">
                      <div className="flex items-start justify-between gap-4">
                        {/* Left: Info */}
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <h4 className="text-base font-semibold text-warm-800 truncate">{wf.name}</h4>
                            {wf.isDefault && <span className="tag tag-primary shrink-0">默认</span>}
                            <span className={`tag shrink-0 ${wf.active ? 'tag-green' : 'tag-warm'}`}>
                              {wf.active ? '启用' : '禁用'}
                            </span>
                          </div>
                          {wf.description && (
                            <p className="mt-1 text-sm text-warm-500 line-clamp-2">{wf.description}</p>
                          )}

                          {/* Keywords */}
                          {wf.triggerKeywords && wf.triggerKeywords.length > 0 && (
                            <div className="mt-2 flex flex-wrap items-center gap-1">
                              <span className="text-[10px] text-warm-400 mr-1">触发词：</span>
                              {wf.triggerKeywords.map((kw) => (
                                <span key={kw} className="inline-block rounded-md bg-warm-100 px-2 py-0.5 text-[10px] text-warm-600">
                                  {kw}
                                </span>
                              ))}
                            </div>
                          )}

                          {/* Node preview with icons */}
                          {wf.nodes && wf.nodes.length > 0 && (
                            <div className="mt-3 flex items-center gap-1.5 flex-wrap">
                              <span className="text-[10px] text-warm-400 mr-1">{wf.nodes.length} 节点：</span>
                              {wf.nodes.slice(0, 8).map((n) => (
                                <span
                                  key={n.id}
                                  className="inline-flex items-center gap-0.5 rounded-full border border-warm-150 bg-white px-2 py-0.5 text-[10px] text-warm-600"
                                >
                                  <span className="material-symbols-outlined text-[11px]" style={{ color: nodeTypeColor(n.type) }}>
                                    {nodeTypeIcon(n.type)}
                                  </span>
                                  {n.name || nodeTypeLabel(n.type)}
                                </span>
                              ))}
                              {wf.nodes.length > 8 && (
                                <span className="text-[10px] text-warm-400">+{wf.nodes.length - 8}</span>
                              )}
                            </div>
                          )}

                          {/* P1-4: Mini runtime stats */}
                          {wfStat && wfStat.totalRuns > 0 && (
                            <div className="mt-2 flex items-center gap-3 text-[10px] text-warm-400">
                              <span>📊 {wfStat.totalRuns} 次运行</span>
                              <span style={{ color: wfStat.successRate >= 80 ? '#22A06B' : '#D97706' }}>
                                ✅ {wfStat.successRate}% 成功率
                              </span>
                              <span>⏱ 平均 {formatDuration(wfStat.avgDuration)}</span>
                            </div>
                          )}
                        </div>

                        {/* Right: Actions */}
                        <div className="flex items-center gap-1.5 shrink-0">
                          {/* P1-4: Test run button */}
                          <button
                            className="btn-secondary text-xs flex items-center gap-0.5"
                            onClick={() => handleTestRun(wf)}
                            disabled={testRunningId === wf.id || !wf.active}
                            title={wf.active ? '测试运行' : '请先启用工作流'}
                          >
                            {testRunningId === wf.id ? (
                              <span className="h-3 w-3 animate-spin rounded-full border-2 border-primary-400 border-t-transparent" />
                            ) : (
                              <span className="material-symbols-outlined text-[14px]">play_arrow</span>
                            )}
                          </button>
                          <button className="btn-secondary text-xs" onClick={() => handleEdit(wf.id)} title="在画布中编辑">
                            <span className="material-symbols-outlined text-[16px]">edit</span>
                          </button>
                          {!wf.isDefault && (
                            <button className="btn-ghost text-xs text-amber-600" onClick={() => handleSetDefault(wf.id)} title="设为默认">
                              <span className="material-symbols-outlined text-[16px]">star</span>
                            </button>
                          )}
                          <button
                            className={`btn-ghost text-xs ${wf.active ? 'text-warm-500' : 'text-green-600'}`}
                            onClick={() => handleToggleActive(wf.id, wf.active)}
                            title={wf.active ? '禁用' : '启用'}
                          >
                            <span className="material-symbols-outlined text-[16px]">{wf.active ? 'pause_circle' : 'play_circle'}</span>
                          </button>
                          <button className="btn-ghost text-xs text-danger-500" onClick={() => confirmDelete(wf)} title="删除">
                            <span className="material-symbols-outlined text-[16px]">delete</span>
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}

      {/* ── Tab: Execution History ────────────────────────────────── */}
      {subTab === 'history' && (
        <div className="flex gap-4">
          {/* Execution list */}
          <div className={`flex-1 min-w-0 ${selectedExecution ? 'hidden lg:block' : ''}`}>
            {demoExecutions.length === 0 ? (
              <div className="card p-8 text-center">
                <span className="material-symbols-outlined text-3xl text-warm-300">history</span>
                <p className="mt-2 text-sm text-warm-500">暂无执行记录</p>
                <p className="text-xs text-warm-400">运行工作流后，执行历史将显示在此处</p>
              </div>
            ) : (
              <div className="space-y-2">
                {demoExecutions.map((ex) => (
                  <div
                    key={ex.id}
                    className={`card cursor-pointer p-4 transition-all hover:shadow-card ${
                      selectedExecution?.id === ex.id ? 'ring-1 ring-primary-200' : ''
                    }`}
                    onClick={() => setSelectedExecution(selectedExecution?.id === ex.id ? null : ex)}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold text-warm-800 truncate">{ex.workflowName}</span>
                          {statusTag(ex.status)}
                        </div>
                        <div className="mt-1 flex items-center gap-3 text-[10px] text-warm-400">
                          <span>{formatTime(ex.startedAt)}</span>
                          <span>触发: {ex.triggeredBy.slice(0, 30)}</span>
                          {ex.durationMs && <span>耗时: {formatDuration(ex.durationMs)}</span>}
                        </div>
                      </div>
                      <span className="material-symbols-outlined text-[16px] text-warm-300 shrink-0">
                        {selectedExecution?.id === ex.id ? 'expand_less' : 'expand_more'}
                      </span>
                    </div>

                    {/* Node execution detail */}
                    {selectedExecution?.id === ex.id && (
                      <div className="mt-3 border-t border-warm-100 pt-3">
                        <div className="text-[10px] font-medium text-warm-400 mb-2 uppercase tracking-wide">
                          节点执行详情 ({ex.nodeResults.length} 节点)
                        </div>
                        <div className="space-y-1.5">
                          {ex.nodeResults.map((nr) => (
                            <div key={nr.nodeId} className="flex items-center gap-2 text-[11px]">
                              <span className="material-symbols-outlined text-[12px]" style={{ color: nodeTypeColor(nr.nodeType) }}>
                                {nodeTypeIcon(nr.nodeType)}
                              </span>
                              <span className="flex-1 text-warm-700 truncate">{nr.nodeName}</span>
                              <span className={`shrink-0 inline-flex h-4 min-w-[16px] items-center justify-center rounded-full px-1 text-[9px] font-medium ${
                                nr.status === 'completed' ? 'bg-green-100 text-green-700' :
                                nr.status === 'failed' ? 'bg-red-100 text-red-700' :
                                nr.status === 'running' ? 'bg-primary-100 text-primary-700' :
                                nr.status === 'awaiting_human' ? 'bg-amber-100 text-amber-700' :
                                'bg-warm-100 text-warm-500'
                              }`}>
                                {nr.status === 'completed' ? '✓' : nr.status === 'failed' ? '✗' : nr.status === 'running' ? '⟳' : '…'}
                              </span>
                              {nr.durationMs && <span className="text-warm-300 w-12 text-right">{formatDuration(nr.durationMs)}</span>}
                            </div>
                          ))}
                        </div>
                        {/* Error info */}
                        {ex.error && (
                          <div className="mt-2 rounded-lg border border-danger-100 bg-danger-50/50 px-3 py-2 text-[10px] text-danger-600">
                            <span className="font-medium">错误：</span>{ex.error}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Execution detail panel (wider screens) */}
          {selectedExecution && (
            <div className="hidden lg:block w-[320px] shrink-0">
              <div className="card p-4 sticky top-4">
                <div className="flex items-center justify-between mb-3">
                  <h4 className="text-sm font-semibold text-warm-800">执行详情</h4>
                  <button className="text-warm-300 hover:text-warm-500" onClick={() => setSelectedExecution(null)}>
                    <span className="material-symbols-outlined text-[16px]">close</span>
                  </button>
                </div>
                <dl className="space-y-2 text-[11px]">
                  <div className="flex justify-between">
                    <dt className="text-warm-400">状态</dt>
                    <dd>{statusTag(selectedExecution.status)}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-warm-400">触发来源</dt>
                    <dd className="text-warm-700 max-w-[180px] truncate">{selectedExecution.triggeredBy}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-warm-400">开始时间</dt>
                    <dd className="text-warm-700">{formatTime(selectedExecution.startedAt)}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-warm-400">完成时间</dt>
                    <dd className="text-warm-700">{selectedExecution.completedAt ? formatTime(selectedExecution.completedAt) : '—'}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-warm-400">总耗时</dt>
                    <dd className="text-warm-700 font-mono">{formatDuration(selectedExecution.durationMs)}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-warm-400">节点数</dt>
                    <dd className="text-warm-700">{selectedExecution.nodeResults.length}</dd>
                  </div>
                </dl>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Tab: Analytics ────────────────────────────────────────── */}
      {subTab === 'analytics' && (
        <div className="space-y-5">
          {/* Summary cards */}
          <div className="grid grid-cols-5 gap-3">
            {[
              { label: '总执行次数', value: analytics.total, color: 'text-warm-800' },
              { label: '成功', value: analytics.completed, color: 'text-green-600' },
              { label: '失败', value: analytics.failed, color: 'text-red-600' },
              { label: '运行中', value: analytics.running, color: 'text-primary-600' },
              { label: '等待审批', value: analytics.awaiting, color: 'text-amber-600' },
            ].map((item) => (
              <div key={item.label} className="card p-3 text-center">
                <div className={`text-xl font-bold ${item.color}`}>{item.value}</div>
                <div className="text-[10px] text-warm-400 mt-0.5">{item.label}</div>
              </div>
            ))}
          </div>

          {/* Per-workflow breakdown */}
          <div className="card p-5">
            <h4 className="text-sm font-semibold text-warm-800 mb-3">工作流执行统计</h4>
            {analytics.wfStats.length === 0 ? (
              <p className="text-xs text-warm-400">暂无数据</p>
            ) : (
              <div className="space-y-3">
                {analytics.wfStats.map((stat) => {
                  const wf = workflows.find((w) => w.id === stat.id);
                  if (!stat.totalRuns) return null;
                  return (
                    <div key={stat.id} className="flex items-center gap-3">
                      <span className="text-xs text-warm-700 w-[140px] truncate shrink-0">{stat.name}</span>
                      {/* Success rate bar */}
                      <div className="flex-1 h-5 rounded-full bg-warm-100 overflow-hidden relative">
                        <div
                          className="absolute inset-y-0 left-0 rounded-full transition-all"
                          style={{
                            width: `${stat.successRate}%`,
                            background: stat.successRate >= 80 ? '#22A06B' : stat.successRate >= 50 ? '#D97706' : '#DC2626',
                          }}
                        />
                        {/* Failed portion */}
                        {stat.successRate < 100 && (
                          <div
                            className="absolute inset-y-0 right-0 rounded-r-full"
                            style={{
                              width: `${100 - stat.successRate}%`,
                              background: '#FEE2E2',
                              left: `${stat.successRate}%`,
                            }}
                          />
                        )}
                      </div>
                      <span className="text-[10px] text-warm-500 w-[72px] text-right shrink-0">
                        {stat.successRate}% ({stat.totalRuns}次)
                      </span>
                      <span className="text-[10px] text-warm-400 w-[60px] text-right shrink-0">
                        {formatDuration(stat.avgDuration)}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Node type distribution */}
          <div className="card p-5">
            <h4 className="text-sm font-semibold text-warm-800 mb-3">节点类型分布 (全部工作流)</h4>
            <div className="flex flex-wrap gap-2">
              {['start', 'agent', 'tool', 'ifelse', 'end', 'code', 'http', 'knowledge', 'human'].map((type) => {
                const count = workflows.reduce((s, w) => s + (w.nodes || []).filter((n) => n.type === type).length, 0);
                if (count === 0) return null;
                return (
                  <div key={type} className="flex items-center gap-1.5 rounded-lg border border-warm-150 bg-white px-3 py-1.5">
                    <span className="material-symbols-outlined text-[12px]" style={{ color: nodeTypeColor(type) }}>
                      {nodeTypeIcon(type)}
                    </span>
                    <span className="text-[11px] font-medium text-warm-700">{nodeTypeLabel(type)}</span>
                    <span className="text-[10px] text-warm-400 ml-0.5">{count}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation Dialog */}
      {deleteConfirmId !== null && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
          <div className="card w-full max-w-md p-6 shadow-xl">
            <h3 className="text-lg font-semibold text-warm-800">确认删除</h3>
            <p className="mt-2 text-sm text-warm-600">
              确定要删除工作流 <span className="font-semibold text-warm-800">"{deleteConfirmName}"</span> 吗？此操作不可撤销。
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button className="btn-secondary" onClick={() => setDeleteConfirmId(null)}>取消</button>
              <button className="btn-danger" onClick={() => void handleDelete(deleteConfirmId)}>确认删除</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
