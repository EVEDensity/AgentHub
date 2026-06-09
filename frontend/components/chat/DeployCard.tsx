'use client';

import { memo, useState, useEffect, type JSX } from 'react';
import { Rocket, RotateCcw, History, GitBranch, Clock, FileText, ChevronRight } from 'lucide-react';
import type { DeployCardEvent, DeployRequest } from '../../types';
import DeployModal from './DeployModal';

// ── Constants ─────────────────────────────────────────────────────────────

const MOCK_PROJECTS = [
  { id: 'proj-1', name: 'agenthub-frontend', defaultBranch: 'main', defaultDomain: 'agenthub' },
  { id: 'proj-2', name: 'agenthub-blog', defaultBranch: 'main', defaultDomain: 'blog' },
] as const;

// ── Props ─────────────────────────────────────────────────────────────────

interface DeployCardProps {
  data: DeployCardEvent;
}

// ── Helpers ───────────────────────────────────────────────────────────────

/** Format an ISO timestamp into a relative time string (Chinese). */
function formatRelativeTime(isoString: string): string {
  try {
    const then = new Date(isoString).getTime();
    const now = Date.now();
    const diffMs = now - then;
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return '刚刚';
    if (diffMin < 60) return `${diffMin} 分钟前`;
    const diffHours = Math.floor(diffMin / 60);
    if (diffHours < 24) return `${diffHours} 小时前`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 30) return `${diffDays} 天前`;
    const diffMonths = Math.floor(diffDays / 30);
    return `${diffMonths} 个月前`;
  } catch {
    return isoString;
  }
}

/** Format timestamp to localized date string. */
function formatDateTime(isoString: string): string {
  try {
    return new Date(isoString).toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return isoString;
  }
}

// ── Component ─────────────────────────────────────────────────────────────

const DeployCard = memo(function DeployCard({ data }: DeployCardProps): JSX.Element {
  const [deployModalOpen, setDeployModalOpen] = useState(false);
  const [rollbackConfirm, setRollbackConfirm] = useState(false);
  const [deployed, setDeployed] = useState(false);
  const [rolledBack, setRolledBack] = useState(false);

  // ── Debug event listener ────────────────────────────────────────────

  useEffect(() => {
    function onDeployReq(e: Event) {
      const ce = e as CustomEvent<DeployRequest>;
      console.info('[DeployCard] deploy_request dispatched:', ce.detail);
    }
    window.addEventListener('agenthub:deploy:request', onDeployReq);
    return () => window.removeEventListener('agenthub:deploy:request', onDeployReq);
  }, []);

  // ── Handlers ────────────────────────────────────────────────────────

  function handleDeploy() {
    setDeployModalOpen(true);
  }

  function handleDeployConfirm(details: {
    projectId: string;
    branch: string;
    domain: string;
    deployType: 'preview' | 'production' | 'custom';
    environment: string;
    notes: string;
    targets: string[];
  }) {
    const payload: DeployRequest = {
      event: 'deploy_request',
      sessionId: data.sessionId,
      messageId: data.messageId,
      version: data.version,
      projectId: details.projectId,
      branch: details.branch,
      domain: details.domain,
      deployType: details.deployType,
      environment: details.environment,
      notes: details.notes,
      targets: details.targets,
      timestamp: new Date().toISOString(),
    };
    window.dispatchEvent(new CustomEvent('agenthub:deploy:request', { detail: payload }));
    setDeployModalOpen(false);
    setDeployed(true);
  }

  function handleDeployCancel() {
    setDeployModalOpen(false);
  }

  function handleRollback() {
    if (!rollbackConfirm) {
      setRollbackConfirm(true);
      // Auto-dismiss confirmation after 5s
      setTimeout(() => setRollbackConfirm(false), 5000);
      return;
    }
    setRolledBack(true);
    setRollbackConfirm(false);
  }

  function handleViewHistory() {
    // Dispatch a custom event to open git history in the preview panel
    window.dispatchEvent(
      new CustomEvent('agenthub:openGitHistory', {
        detail: { version: data.version, sessionId: data.sessionId },
      })
    );
  }

  const fileCount = data.affectedFiles?.length || 0;
  const hasFiles = fileCount > 0;
  const isActionable = !deployed && !rolledBack;

  return (
    <>
      <div className="mb-4 flex justify-start">
        <div className="max-w-[90%] min-w-[380px] rounded-2xl overflow-hidden bg-white border border-warm-200 shadow-md">
          {/* ── Header: version + time ──────────────────────────── */}
          <div className="bg-gradient-to-r from-indigo-500 to-purple-600 px-5 py-3.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Rocket className="h-5 w-5 text-white" />
                <span className="text-sm font-bold text-white">部署卡片</span>
                {deployed && (
                  <span className="rounded-full bg-white/20 px-2 py-0.5 text-[10px] font-medium text-white">
                    已部署
                  </span>
                )}
                {rolledBack && (
                  <span className="rounded-full bg-amber-300/30 px-2 py-0.5 text-[10px] font-medium text-amber-100">
                    已回退
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1 text-white/70 text-[10px]">
                <GitBranch className="h-3 w-3" />
                <code className="font-mono text-white/90">{data.version || 'unknown'}</code>
              </div>
            </div>
          </div>

          {/* ── Body: description + time + files ─────────────────── */}
          <div className="px-5 py-4 space-y-3">
            {/* Description */}
            <div>
              <p className="text-sm text-warm-700 leading-relaxed line-clamp-3">
                {data.description}
              </p>
            </div>

            {/* Meta: completed time */}
            <div className="flex items-center gap-4 text-xs text-warm-400">
              <span className="inline-flex items-center gap-1">
                <Clock className="h-3.5 w-3.5" />
                {formatRelativeTime(data.completedAt)}
              </span>
              <span className="text-warm-300">|</span>
              <span className="inline-flex items-center gap-1">
                {formatDateTime(data.completedAt)}
              </span>
            </div>

            {/* Affected files */}
            {hasFiles && (
              <div className="rounded-lg bg-warm-50 border border-warm-100 px-3 py-2">
                <div className="flex items-center gap-1.5 mb-1.5 text-[11px] font-medium text-warm-500">
                  <FileText className="h-3.5 w-3.5" />
                  涉及文件 ({fileCount})
                </div>
                <div className="flex flex-wrap gap-1">
                  {data.affectedFiles.slice(0, 8).map((f, i) => (
                    <code
                      key={i}
                      className="rounded bg-white border border-warm-150 px-1.5 py-0.5 text-[10px] font-mono text-warm-600 max-w-[200px] truncate"
                      title={f}
                    >
                      {f}
                    </code>
                  ))}
                  {fileCount > 8 && (
                    <span className="text-[10px] text-warm-400 self-center">
                      +{fileCount - 8} 更多
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* ── Actions ──────────────────────────────────────────── */}
          <div className="border-t border-warm-100 px-5 py-3 flex items-center gap-2 flex-wrap">
            {isActionable ? (
              <>
                {/* Deploy button */}
                <button
                  type="button"
                  onClick={handleDeploy}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 active:scale-[0.97] transition-all shadow-sm"
                >
                  <Rocket className="h-4 w-4" />
                  部署
                  <ChevronRight className="h-4 w-4" />
                </button>

                {/* Rollback button */}
                <button
                  type="button"
                  onClick={handleRollback}
                  className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-medium transition-all active:scale-[0.97] ${
                    rollbackConfirm
                      ? 'border-red-400 bg-red-50 text-red-700 hover:bg-red-100'
                      : 'border-warm-200 bg-white text-warm-600 hover:bg-warm-50'
                  }`}
                >
                  <RotateCcw className={`h-4 w-4 ${rollbackConfirm ? 'text-red-500' : ''}`} />
                  {rollbackConfirm ? '确认回退？' : '回到该版本'}
                </button>

                {/* View history button */}
                <button
                  type="button"
                  onClick={handleViewHistory}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-warm-200 bg-white px-3 py-2 text-sm font-medium text-warm-600 hover:bg-warm-50 active:scale-[0.97] transition-all"
                >
                  <History className="h-4 w-4" />
                  查看修改记录
                </button>
              </>
            ) : (
              <div className="text-xs text-warm-400">
                {deployed ? '该版本已部署' : '该版本已回退'}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Deploy detail modal ──────────────────────────────────── */}
      <DeployModal
        open={deployModalOpen}
        version={data.version}
        description={data.description}
        projects={[...MOCK_PROJECTS]}
        defaultProjectId={data.projectId || 'proj-1'}
        defaultBranch={data.defaultBranch || 'main'}
        defaultDomain={data.defaultDomain || 'preview'}
        onConfirm={handleDeployConfirm}
        onCancel={handleDeployCancel}
      />
    </>
  );
});

export default DeployCard;
