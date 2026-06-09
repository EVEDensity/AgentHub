'use client';

import { useEffect, useState, type JSX } from 'react';
import { useRouter } from 'next/navigation';
import { useWorkflowStore, type WorkflowListItem } from '../../stores/workflowStore';

interface Props {
  authHeaders: Record<string, string>;
  setNotice: (msg: string) => void;
}

export default function WorkflowModule({ setNotice }: Props): JSX.Element {
  const router = useRouter();
  const {
    workflows,
    loading,
    error,
    loadWorkflows,
    deleteWorkflow,
    setDefault,
    toggleActive,
  } = useWorkflowStore();

  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);
  const [deleteConfirmName, setDeleteConfirmName] = useState('');

  useEffect(() => {
    void loadWorkflows();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Handlers ─────────────────────────────────────────────────────

  function handleCreate(): void {
    router.push('/canvas?new=true');
  }

  function handleEdit(id: number): void {
    router.push(`/canvas?id=${id}`);
  }

  async function handleDelete(id: number): Promise<void> {
    const ok = await deleteWorkflow(id);
    if (ok) setDeleteConfirmId(null);
  }

  function confirmDelete(wf: WorkflowListItem): void {
    setDeleteConfirmId(wf.id);
    setDeleteConfirmName(wf.name);
  }

  async function handleSetDefault(id: number): Promise<void> {
    await setDefault(id);
  }

  async function handleToggleActive(id: number, active: boolean): Promise<void> {
    await toggleActive(id, !active);
  }

  // ── Render helpers ───────────────────────────────────────────────

  function nodeTypeIcon(type: string): string {
    const icons: Record<string, string> = {
      start: 'play_circle',
      agent: 'smart_toy',
      tool: 'build',
      ifelse: 'account_tree',
      end: 'flag',
    };
    return icons[type] || 'circle';
  }

  function nodeTypeColor(type: string): string {
    const colors: Record<string, string> = {
      start: '#22A06B',
      agent: '#4F6CF7',
      tool: '#8B5CF6',
      ifelse: '#D97706',
      end: '#64748B',
    };
    return colors[type] || '#94A3B8';
  }

  // ── Stats ────────────────────────────────────────────────────────

  const activeCount = workflows.filter((w) => w.active).length;
  const defaultWf = workflows.find((w) => w.isDefault);

  // ── Render ───────────────────────────────────────────────────────

  return (
    <section className="space-y-5">
      {/* Stats Row */}
      <div className="grid grid-cols-3 gap-4">
        <div className="card p-4">
          <div className="text-[11px] font-medium uppercase tracking-wide text-warm-400">工作流总数</div>
          <div className="mt-1 text-2xl font-bold text-warm-800">{workflows.length}</div>
        </div>
        <div className="card p-4">
          <div className="text-[11px] font-medium uppercase tracking-wide text-warm-400">已启用</div>
          <div className="mt-1 text-2xl font-bold text-primary-600">{activeCount}</div>
        </div>
        <div className="card p-4">
          <div className="text-[11px] font-medium uppercase tracking-wide text-warm-400">默认工作流</div>
          <div className="mt-1 text-lg font-semibold text-warm-800 truncate">
            {defaultWf ? defaultWf.name : '—'}
          </div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-warm-800">Agent Flow 工作流列表</h3>
          <p className="mt-0.5 text-xs text-warm-400">
            定义 Agent 执行流程，用户消息自动匹配触发关键词来路由到对应工作流
          </p>
        </div>
        <button className="btn-primary" onClick={handleCreate}>
          <span className="material-symbols-outlined text-[18px] mr-1 align-middle">add</span>
          新建工作流
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-danger-200 bg-danger-50 px-4 py-3 text-sm text-danger-600">
          {error}
          <button className="ml-3 underline" onClick={() => void loadWorkflows()}>重试</button>
        </div>
      )}

      {/* Workflow List */}
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
          {workflows.map((wf) => (
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
                      {wf.isDefault && (
                        <span className="tag tag-primary shrink-0">默认</span>
                      )}
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
                          <span
                            key={kw}
                            className="inline-block rounded-md bg-warm-100 px-2 py-0.5 text-[10px] text-warm-600"
                          >
                            {kw}
                          </span>
                        ))}
                      </div>
                    )}

                    {/* Node preview */}
                    {wf.nodes && wf.nodes.length > 0 && (
                      <div className="mt-3 flex items-center gap-1.5 flex-wrap">
                        <span className="text-[10px] text-warm-400 mr-1">
                          {wf.nodes.length} 节点：
                        </span>
                        {wf.nodes.slice(0, 8).map((n, i) => (
                          <span
                            key={n.id}
                            className="inline-flex items-center gap-1 rounded-full border border-warm-150 bg-white px-2 py-0.5 text-[10px] text-warm-600"
                          >
                            <span
                              className="h-1.5 w-1.5 rounded-full"
                              style={{ background: nodeTypeColor(n.type) }}
                            />
                            {n.name || n.type}
                          </span>
                        ))}
                        {wf.nodes.length > 8 && (
                          <span className="text-[10px] text-warm-400">
                            +{wf.nodes.length - 8} more
                          </span>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Right: Actions */}
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button
                      className="btn-secondary text-xs"
                      onClick={() => handleEdit(wf.id)}
                      title="在画布中编辑"
                    >
                      <span className="material-symbols-outlined text-[16px]">edit</span>
                    </button>
                    {!wf.isDefault && (
                      <button
                        className="btn-ghost text-xs text-amber-600"
                        onClick={() => handleSetDefault(wf.id)}
                        title="设为默认"
                      >
                        <span className="material-symbols-outlined text-[16px]">star</span>
                      </button>
                    )}
                    <button
                      className={`btn-ghost text-xs ${wf.active ? 'text-warm-500' : 'text-green-600'}`}
                      onClick={() => handleToggleActive(wf.id, wf.active)}
                      title={wf.active ? '禁用' : '启用'}
                    >
                      <span className="material-symbols-outlined text-[16px]">
                        {wf.active ? 'pause_circle' : 'play_circle'}
                      </span>
                    </button>
                    <button
                      className="btn-ghost text-xs text-danger-500"
                      onClick={() => confirmDelete(wf)}
                      title="删除"
                    >
                      <span className="material-symbols-outlined text-[16px]">delete</span>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ))}
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
              <button
                className="btn-secondary"
                onClick={() => setDeleteConfirmId(null)}
              >
                取消
              </button>
              <button
                className="btn-danger"
                onClick={() => void handleDelete(deleteConfirmId)}
              >
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
