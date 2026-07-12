import { useState, useEffect, useCallback } from 'react';
import type { LocalAgentCandidate } from '../../types';

interface LocalAgentModalProps {
  visible: boolean;
  onClose: () => void;
  /** Called after a successful registration to refresh the agent list */
  onRegistered: () => void;
  /** Auth header factory */
  authHeaders: (extra?: Record<string, string>) => Record<string, string>;
}

type DiscoveryState = 'idle' | 'scanning' | 'done' | 'error';

const PLATFORM_ICONS: Record<string, string> = {
  local_claude: '[brain]',
  local_codex: '[bot]',
  local_openclaw: '[lobster]',
};

export default function LocalAgentModal({
  visible,
  onClose,
  onRegistered,
  authHeaders,
}: LocalAgentModalProps) {
  const [discoveryState, setDiscoveryState] = useState<DiscoveryState>('idle');
  const [candidates, setCandidates] = useState<LocalAgentCandidate[]>([]);
  const [error, setError] = useState('');
  const [registering, setRegistering] = useState<Record<string, boolean>>({});
  const [notice, setNotice] = useState('');

  // ── Discover local agents ────────────────────────────────────────
  const discover = useCallback(async () => {
    setDiscoveryState('scanning');
    setError('');
    setNotice('');

    try {
      const res = await fetch('/api/agent/local/discover', {
        headers: authHeaders(),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({})) as { detail?: string };
        throw new Error((data as { detail?: string }).detail || `HTTP ${res.status}`);
      }
      const data = await res.json() as { candidates: LocalAgentCandidate[] };
      setCandidates(data.candidates || []);
      setDiscoveryState('done');
    } catch (err) {
      setError(err instanceof Error ? err.message : '发现扫描失败');
      setDiscoveryState('error');
    }
  }, [authHeaders]);

  // Auto-scan on mount
  useEffect(() => {
    if (visible && discoveryState === 'idle') {
      void discover();
    }
  }, [visible, discoveryState, discover]);

  // Reset state when modal closes
  useEffect(() => {
    if (!visible) {
      setDiscoveryState('idle');
      setCandidates([]);
      setError('');
      setRegistering({});
      setNotice('');
    }
  }, [visible]);

  // ── Register a local agent ───────────────────────────────────────
  const register = useCallback(
    async (candidate: LocalAgentCandidate) => {
      setRegistering((p) => ({ ...p, [candidate.adapterType]: true }));
      setNotice('');

      try {
        const res = await fetch('/api/agent/local/register', {
          method: 'POST',
          headers: authHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            adapterType: candidate.adapterType,
            domain: candidate.capabilities[0] || 'general',
            displayName: candidate.displayName,
            riskLevel: 'L1',
            baseModelName: candidate.displayName,
            capabilityTags: candidate.capabilities,
          }),
        });

        const data = await res.json() as { status?: string; agentId?: string; error?: string };
        if (!res.ok) {
          throw new Error((data as { detail?: string }).detail || data.error || `HTTP ${res.status}`);
        }

        if (data.status === 'skipped') {
          setNotice(`「${candidate.displayName}」已注册，跳过重复添加`);
        } else {
          setNotice(`✅ 成功接入「${candidate.displayName}」（Agent ID: ${data.agentId}）`);
          // Refresh the candidates to show registered status
          await discover();
          onRegistered();
        }
      } catch (err) {
        setNotice(`❌ 接入失败：${err instanceof Error ? err.message : '未知错误'}`);
      } finally {
        setRegistering((p) => ({ ...p, [candidate.adapterType]: false }));
      }
    },
    [authHeaders, discover, onRegistered],
  );

  if (!visible) return null;

  const installedCount = candidates.filter((c) => c.installed).length;
  const healthyCount = candidates.filter((c) => c.healthy).length;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-warm-100 shadow-modal"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-warm-150 px-6 py-4">
          <div>
            <h3 className="text-lg font-semibold text-warm-900">[laptop] 接入本地 Agent</h3>
            <p className="mt-1 text-sm text-warm-500">
              自动扫描系统中已安装的 AI CLI 工具，一键接入 AgentHub 任务调度
            </p>
          </div>
          <button
            className="rounded-lg px-3 py-1.5 text-sm text-warm-500 hover:bg-warm-100 transition-colors"
            onClick={onClose}
          >
            关闭
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-4">
          {/* Controls bar */}
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <button
                className="btn-primary text-sm px-4 py-2"
                onClick={discover}
                disabled={discoveryState === 'scanning'}
              >
                {discoveryState === 'scanning' ? '[hourglass] 扫描中...' : '[sync] 重新扫描'}
              </button>
              {discoveryState === 'done' && (
                <span className="text-sm text-warm-500">
                  发现 {installedCount} 个可用工具（{healthyCount} 个健康）
                </span>
              )}
            </div>
          </div>

          {/* Error banner */}
          {error && (
            <div className="mb-4 rounded-lg bg-danger-50 px-4 py-3 text-sm text-danger-600">
              {error}
            </div>
          )}

          {/* Notice banner */}
          {notice && (
            <div
              className={`mb-4 rounded-lg px-4 py-3 text-sm ${
                notice.startsWith('✅')
                  ? 'bg-success-50 text-success-700'
                  : notice.startsWith('❌')
                  ? 'bg-danger-50 text-danger-600'
                  : 'bg-primary-50 text-primary-600'
              }`}
            >
              {notice}
            </div>
          )}

          {/* Candidate list */}
          {discoveryState === 'scanning' && (
            <div className="flex items-center justify-center py-12">
              <div className="flex items-center gap-3 text-warm-500">
                <svg className="h-5 w-5 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                  />
                </svg>
                <span className="text-sm">正在扫描系统中已安装的 AI CLI 工具...</span>
              </div>
            </div>
          )}

          {discoveryState === 'done' && (
            <div className="space-y-3">
              {candidates.map((candidate) => (
                <div
                  key={candidate.adapterType}
                  className={`rounded-xl border p-4 transition-colors ${
                    candidate.healthy
                      ? 'border-success-200 bg-success-50/30'
                      : candidate.installed
                      ? 'border-warning-200 bg-warning-50/30'
                      : 'border-warm-150 bg-warm-100 opacity-60'
                  }`}
                >
                  <div className="flex items-center justify-between gap-4">
                    {/* Left: icon + info */}
                    <div className="flex items-start gap-3 min-w-0">
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-xl bg-warm-100 border border-warm-150 shadow-sm">
                        {PLATFORM_ICONS[candidate.adapterType] || '[wrench]'}
                      </span>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <h4 className="text-sm font-semibold text-warm-900">
                            {candidate.displayName}
                          </h4>
                          {/* Status indicator */}
                          {candidate.healthy ? (
                            <span className="inline-flex items-center gap-1 rounded-full bg-success-100 px-2 py-0.5 text-[11px] font-medium text-success-700">
                              <span className="h-1.5 w-1.5 rounded-full bg-success-500" />
                              在线
                            </span>
                          ) : candidate.installed ? (
                            <span className="inline-flex items-center gap-1 rounded-full bg-warning-100 px-2 py-0.5 text-[11px] font-medium text-warning-700">
                              <span className="h-1.5 w-1.5 rounded-full bg-warning-500" />
                              检测异常
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 rounded-full bg-warm-100 px-2 py-0.5 text-[11px] font-medium text-warm-500">
                              <span className="h-1.5 w-1.5 rounded-full bg-warm-400" />
                              未安装
                            </span>
                          )}
                          {candidate.registered && (
                            <span className="rounded-full bg-primary-100 px-2 py-0.5 text-[11px] font-medium text-primary-700">
                              已接入
                            </span>
                          )}
                        </div>
                        {/* Path + version */}
                        {candidate.installed && (
                          <p className="mt-1 text-[12px] text-warm-500 truncate">
                            {candidate.installPath}
                            {candidate.version && (
                              <span className="ml-2 text-warm-400">
                                v{candidate.version.slice(0, 40)}
                              </span>
                            )}
                          </p>
                        )}
                        {!candidate.installed && (
                          <p className="mt-1 text-[12px] text-warm-400">
                            未在系统 PATH 中找到「{candidate.binary}」命令
                          </p>
                        )}
                        {/* Error message */}
                        {candidate.errorMessage && (
                          <p className="mt-1 text-[12px] text-danger-500 truncate">
                            {candidate.errorMessage}
                          </p>
                        )}
                        {/* Headless command hint */}
                        {candidate.headlessCommand && (
                          <p className="mt-1 text-[11px] text-warm-400 font-mono truncate">
                            {candidate.headlessCommand}
                          </p>
                        )}
                        {/* Capabilities */}
                        {candidate.capabilities.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {candidate.capabilities.map((cap) => (
                              <span
                                key={cap}
                                className="rounded bg-warm-100 px-1.5 py-0.5 text-[10px] text-warm-500"
                              >
                                {cap}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Right: action button */}
                    <div className="flex shrink-0 flex-col items-end gap-2">
                      {candidate.healthy && !candidate.registered && (
                        <button
                          className="btn-primary px-4 py-2 text-sm whitespace-nowrap"
                          onClick={() => register(candidate)}
                          disabled={registering[candidate.adapterType]}
                        >
                          {registering[candidate.adapterType] ? '接入中...' : '[inbox] 接入'}
                        </button>
                      )}
                      {candidate.registered && (
                        <span className="rounded bg-primary-50 px-3 py-1.5 text-xs text-primary-600 font-medium">
                          ✓ 已接入 ({candidate.registeredAgentId})
                        </span>
                      )}
                      {!candidate.healthy && candidate.installed && (
                        <button
                          className="btn-secondary px-4 py-2 text-sm whitespace-nowrap"
                          onClick={discover}
                        >
                          [sync] 重新检测
                        </button>
                      )}
                      {!candidate.installed && (
                        <span className="text-[11px] text-warm-400 text-right leading-relaxed">
                          请先安装<br />对应 CLI 工具
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {discoveryState === 'error' && !error && (
            <div className="flex flex-col items-center justify-center py-12 text-warm-500">
              <p className="text-sm">扫描失败，请重试</p>
              <button className="btn-primary mt-3 px-4 py-2 text-sm" onClick={discover}>
                重试
              </button>
            </div>
          )}

          {/* Empty state */}
          {discoveryState === 'idle' && (
            <div className="flex flex-col items-center justify-center py-12 text-warm-400">
              <p className="text-sm">点击「扫描」开始检测本地 AI CLI 工具</p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-warm-150 px-6 py-3">
          <p className="text-[11px] text-warm-400 leading-relaxed">
            本地 Agent 运行在您的设备上，AgentHub 仅通过 subprocess 与 CLI 通信，所有代码和数据均保留在本地，不会上传至云端。
            支持的 CLI：Claude Code（<code className="text-[11px] bg-warm-100 px-1 rounded">claude</code>）、
            Codex CLI（<code className="text-[11px] bg-warm-100 px-1 rounded">codex</code>）、
            OpenClaw（<code className="text-[11px] bg-warm-100 px-1 rounded">openclaw-cli</code>）
          </p>
        </div>
      </div>
    </div>
  );
}
