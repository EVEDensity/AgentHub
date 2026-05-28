import { memo } from 'react';
import type { DagState } from '../../types';

interface DagModalProps {
  dag: DagState;
  onClose: () => void;
}

const STATUS_CONFIG: Record<string, { label: string; color: string; bg: string; icon: string }> = {
  completed: { label: '完成', color: '#5B8C5A', bg: '#F2F8F2', icon: '' },
  SUCCESS:    { label: '成功', color: '#5B8C5A', bg: '#F2F8F2', icon: '' },
  running:    { label: '运行中', color: '#4F6CF7', bg: '#F0F3FF', icon: '' },
  RUNNING:    { label: '运行中', color: '#4F6CF7', bg: '#F0F3FF', icon: '' },
  failed:     { label: '失败', color: '#C4675A', bg: '#FBF0EE', icon: '' },
  FAILED:     { label: '失败', color: '#C4675A', bg: '#FBF0EE', icon: '' },
};

function statusInfo(s?: string) {
  const key = (s || '').toLowerCase();
  return STATUS_CONFIG[key] || { label: s || '等待中', color: '#ADABA3', bg: '#F5F4F0', icon: '' };
}

const DagModal = memo(function DagModal({ dag, onClose }: DagModalProps) {
  const pct = dag.total > 0 ? Math.round((dag.completed / dag.total) * 100) : 0;

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-warm-900/30 backdrop-blur-sm p-6" onClick={onClose}>
      <div
        className="w-[560px] max-h-[80vh] flex flex-col rounded-2xl bg-white shadow-modal border border-warm-150"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-warm-150 px-6 py-4">
          <div>
            <h3 className="text-base font-semibold text-warm-800">DAG 任务详情</h3>
            <p className="mt-0.5 text-[11px] text-warm-400">
              {dag.completed}/{dag.total} 已完成
            </p>
          </div>
          <button
            className="flex h-8 w-8 items-center justify-center rounded-lg text-warm-400 transition-colors hover:bg-warm-100 hover:text-warm-600"
            onClick={onClose}
            aria-label="关闭"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* Progress bar */}
        <div className="px-6 pt-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[11px] font-medium text-warm-400 uppercase tracking-wide">执行进度</span>
            <span className="text-[11px] font-semibold text-warm-600 tabular-nums">{pct}%</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-warm-100">
            <div
              className="h-full rounded-full transition-all duration-700 ease-out"
              style={{
                width: `${pct}%`,
                background: pct === 100
                  ? 'linear-gradient(90deg, #5B8C5A, #6DA66C)'
                  : 'linear-gradient(90deg, #4F6CF7, #8099FB)',
              }}
            />
          </div>
        </div>

        {/* Node list */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          <div className="space-y-2.5">
            {dag.nodes.map((n, i) => {
              const si = statusInfo(n.status);
              const isCompleted = (n.status || '').toLowerCase() === 'completed' || n.status === 'SUCCESS';
              const isRunning = (n.status || '').toLowerCase() === 'running' || n.status === 'RUNNING';

              return (
                <div
                  key={n.id || i}
                  className="relative flex items-center gap-4 rounded-xl border border-warm-150 bg-white px-4 py-3.5 transition-all duration-300"
                >
                  {/* Status indicator */}
                  <div className="relative flex h-9 w-9 flex-shrink-0 items-center justify-center">
                    {isRunning && (
                      <span className="absolute inset-0 rounded-full bg-primary-400/20 animate-ping" style={{ animationDuration: '2s' }} />
                    )}
                    <span
                      className="relative flex h-9 w-9 items-center justify-center rounded-full text-[11px] font-bold"
                      style={{ background: si.bg, color: si.color }}
                    >
                      {isCompleted ? (
                        <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                      ) : isRunning ? (
                        <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                          <path d="M12 2a10 10 0 1 0 10 10" strokeLinecap="round" />
                        </svg>
                      ) : (
                        i + 1
                      )}
                    </span>
                  </div>

                  {/* Node info */}
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-semibold text-warm-800">{n.agent || n.name || `Task ${i + 1}`}</div>
                    {n.description && (
                      <div className="mt-0.5 text-[11px] text-warm-400 truncate">{n.description}</div>
                    )}
                  </div>

                  {/* Status badge */}
                  <span
                    className="flex-shrink-0 rounded-full px-3 py-1 text-[11px] font-medium"
                    style={{ background: si.bg, color: si.color }}
                  >
                    {si.label}
                  </span>

                  {/* Dependency connector */}
                  {(n.dependencies && n.dependencies.length > 0) && (
                    <div className="absolute -top-1.5 left-6 text-[9px] text-warm-400">
                      ← {n.dependencies.join(', ')}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {dag.nodes.length === 0 && (
            <div className="flex flex-col items-center justify-center py-12 text-warm-400">
              <div className="mb-3 flex h-16 w-16 items-center justify-center rounded-2xl bg-warm-50">
                <svg className="h-7 w-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                  <circle cx="12" cy="12" r="10" /><path d="M12 6v6l4 2" />
                </svg>
              </div>
              <p className="text-sm">暂无任务节点</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
});

export default DagModal;
