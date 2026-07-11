'use client';

import { useState, useEffect, useCallback } from 'react';
import type { SandboxContainer, SandboxExecLog, SandboxStats } from '../../types';

const STATUS_COLORS: Record<string, string> = {
  created: '#8b5cf6',
  starting: '#f59e0b',
  running: '#22c55e',
  stopped: '#9ca3af',
  failed: '#ef4444',
  destroyed: '#6b7280',
};

const STATUS_LABELS: Record<string, string> = {
  created: '已创建',
  starting: '启动中',
  running: '运行中',
  stopped: '已停止',
  failed: '失败',
  destroyed: '已销毁',
};

export default function AgentSandboxPanel({
  authHeaders,
  setNotice,
}: {
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
}): JSX.Element {
  const [containers, setContainers] = useState<SandboxContainer[]>([]);
  const [stats, setStats] = useState<SandboxStats | null>(null);
  const [logs, setLogs] = useState<Record<string, SandboxExecLog[]>>({});
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [execInput, setExecInput] = useState<Record<string, string>>({});
  const [activeLogsId, setActiveLogsId] = useState<string | null>(null);

  // Create form
  const [createForm, setCreateForm] = useState({
    agent_id: '',
    tenant_id: 'default',
    cpu_limit: 1.0,
    memory_mb: 512,
    disk_mb: 10240,
    image: 'agenthub/sandbox:latest',
    network_allow: '',
  });

  const headers = authHeaders();

  const fetchContainers = useCallback(async () => {
    try {
      const res = await fetch('/digital/sandbox/containers', { headers });
      if (res.ok) setContainers(await res.json());
    } catch { /* ignore */ }
  }, [headers]);

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch('/digital/sandbox-stats', { headers });
      if (res.ok) setStats(await res.json());
    } catch { /* ignore */ }
  }, [headers]);

  const fetchLogs = useCallback(async (containerId: string) => {
    try {
      const res = await fetch(`/digital/sandbox/containers/${encodeURIComponent(containerId)}/logs`, { headers });
      if (res.ok) {
        const data = await res.json();
        setLogs((prev) => ({ ...prev, [containerId]: data }));
      }
    } catch { /* ignore */ }
  }, [headers]);

  useEffect(() => {
    Promise.all([fetchContainers(), fetchStats()]).then(() => setLoading(false));
  }, [fetchContainers, fetchStats]);

  const handleCreate = async () => {
    if (!createForm.agent_id.trim()) { setNotice('请输入 Agent ID'); return; }
    try {
      const res = await fetch('/digital/sandbox/containers', {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...createForm,
          network_allow: createForm.network_allow ? createForm.network_allow.split(',').map((s) => s.trim()) : [],
        }),
      });
      if (res.ok) {
        setNotice('沙箱容器创建成功');
        setShowCreate(false);
        setCreateForm({ agent_id: '', tenant_id: 'default', cpu_limit: 1.0, memory_mb: 512, disk_mb: 10240, image: 'agenthub/sandbox:latest', network_allow: '' });
        fetchContainers();
        fetchStats();
      }
    } catch { setNotice('创建失败'); }
  };

  const handleStart = async (containerId: string) => {
    try {
      await fetch(`/digital/sandbox/containers/${encodeURIComponent(containerId)}/start`, { method: 'POST', headers });
      setNotice('容器启动中...');
      setTimeout(() => { fetchContainers(); fetchStats(); }, 500);
    } catch { setNotice('启动失败'); }
  };

  const handleStop = async (containerId: string) => {
    try {
      await fetch(`/digital/sandbox/containers/${encodeURIComponent(containerId)}/stop`, { method: 'POST', headers });
      setNotice('容器已停止');
      fetchContainers();
      fetchStats();
    } catch { setNotice('停止失败'); }
  };

  const handleExec = async (containerId: string) => {
    const cmd = execInput[containerId]?.trim();
    if (!cmd) { setNotice('请输入命令'); return; }
    try {
      const res = await fetch(`/digital/sandbox/containers/${encodeURIComponent(containerId)}/exec`, {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: cmd }),
      });
      if (res.ok) {
        const result = await res.json();
        setNotice(`执行完成 (exit: ${result.exit_code}, ${result.duration_ms}ms)`);
        setExecInput((prev) => ({ ...prev, [containerId]: '' }));
        fetchLogs(containerId);
      }
    } catch { setNotice('执行失败'); }
  };

  const handleDelete = async (containerId: string) => {
    if (!confirm('确认销毁此容器?')) return;
    try {
      await fetch(`/digital/sandbox/containers/${encodeURIComponent(containerId)}`, { method: 'DELETE', headers });
      setNotice('容器已销毁');
      fetchContainers();
      fetchStats();
    } catch { setNotice('销毁失败'); }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-h4">[whale] Docker 安全沙箱</h3>
          <p className="text-xs text-warm-400 mt-1">隔离的 Agent 执行环境 · Seccomp 安全策略 · 资源配额管理</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary text-sm">
          + 创建容器
        </button>
      </div>

      {/* Stats bar */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <MiniStatCard icon="[package]" label="总容器" value={stats.total_containers} />
          <MiniStatCard icon="[green]" label="运行中" value={stats.active_containers} color="green" />
          <MiniStatCard icon="[bolt]" label="总执行" value={stats.total_execs} />
          <MiniStatCard icon="[timer]" label="平均耗时" value={`${stats.avg_duration_ms.toFixed(0)}ms`} />
          <MiniStatCard
            icon="[chart]"
            label="状态分布"
            value={Object.entries(stats.by_status)
              .map(([k, v]) => `${STATUS_LABELS[k] || k}:${v}`)
              .join(' ')}
          />
        </div>
      )}

      {/* Create form */}
      {showCreate && (
        <div className="card p-5 border-2 border-primary-100 bg-primary-50/30">
          <h4 className="font-semibold text-warm-800 mb-3">创建沙箱容器</h4>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <div>
              <label className="block text-xs text-warm-500 mb-1">Agent ID *</label>
              <input type="text" value={createForm.agent_id} onChange={(e) => setCreateForm((f) => ({ ...f, agent_id: e.target.value }))} className="input-field text-sm w-full" placeholder="coder-agent-01" />
            </div>
            <div>
              <label className="block text-xs text-warm-500 mb-1">CPU 限制 (核)</label>
              <input type="number" value={createForm.cpu_limit} onChange={(e) => setCreateForm((f) => ({ ...f, cpu_limit: +e.target.value }))} className="input-field text-sm w-full" step="0.5" min="0.5" max="4" />
            </div>
            <div>
              <label className="block text-xs text-warm-500 mb-1">内存 (MB)</label>
              <input type="number" value={createForm.memory_mb} onChange={(e) => setCreateForm((f) => ({ ...f, memory_mb: +e.target.value }))} className="input-field text-sm w-full" step="128" min="128" max="4096" />
            </div>
            <div>
              <label className="block text-xs text-warm-500 mb-1">磁盘 (MB)</label>
              <input type="number" value={createForm.disk_mb} onChange={(e) => setCreateForm((f) => ({ ...f, disk_mb: +e.target.value }))} className="input-field text-sm w-full" step="1024" min="1024" max="102400" />
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={handleCreate} className="btn-primary text-sm">创建</button>
            <button onClick={() => setShowCreate(false)} className="btn-secondary text-sm">取消</button>
          </div>
        </div>
      )}

      {/* Container list */}
      <div className="grid gap-4">
        {containers.map((c) => (
          <div key={c.id} className="card overflow-hidden">
            <div className="flex items-center justify-between p-4">
              <div className="flex items-center gap-3">
                <span className="text-2xl">[whale]</span>
                <div>
                  <div className="flex items-center gap-2">
                    <h4 className="font-semibold text-warm-800 font-mono text-sm">{c.id}</h4>
                    <span
                      className="text-xs px-2 py-0.5 rounded-full font-medium"
                      style={{ backgroundColor: (STATUS_COLORS[c.status] || '#9ca3af') + '20', color: STATUS_COLORS[c.status] }}
                    >
                      {STATUS_LABELS[c.status] || c.status}
                    </span>
                  </div>
                  <p className="text-xs text-warm-400">
                    Agent: {c.agent_id} · {c.cpu_limit} CPU · {c.memory_mb}MB · {c.disk_mb}MB disk
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {c.status === 'created' && (
                  <button onClick={() => handleStart(c.id)} className="btn-primary text-xs">[play] 启动</button>
                )}
                {c.status === 'running' && (
                  <button onClick={() => handleStop(c.id)} className="btn-secondary text-xs">[stop] 停止</button>
                )}
                {(c.status === 'stopped' || c.status === 'failed') && (
                  <button onClick={() => handleStart(c.id)} className="btn-ghost text-xs">[sync] 重启</button>
                )}
                <button onClick={() => handleDelete(c.id)} className="btn-ghost text-xs text-danger-500">[delete]</button>
                <button
                  onClick={() => {
                    const nextId = activeLogsId === c.id ? null : c.id;
                    setActiveLogsId(nextId);
                    if (nextId) fetchLogs(c.id);
                  }}
                  className="btn-ghost text-xs"
                >
                  {activeLogsId === c.id ? '[clipboard] [up]' : '[clipboard]'}
                </button>
              </div>
            </div>

            {/* Exec bar for running containers */}
            {c.status === 'running' && (
              <div className="border-t border-warm-100 px-4 py-2 bg-warm-50/50 flex gap-2">
                <input
                  type="text"
                  value={execInput[c.id] || ''}
                  onChange={(e) => setExecInput((prev) => ({ ...prev, [c.id]: e.target.value }))}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleExec(c.id); }}
                  placeholder="输入命令... (pip install numpy, python main.py, ls)"
                  className="input-field text-sm flex-1 font-mono"
                />
                <button onClick={() => handleExec(c.id)} className="btn-primary text-xs shrink-0">执行</button>
              </div>
            )}

            {/* Exec logs */}
            {activeLogsId === c.id && (
              <div className="border-t border-warm-100 p-4 bg-warm-50/50 max-h-64 overflow-y-auto">
                <h5 className="text-xs font-semibold text-warm-500 mb-2">执行日志</h5>
                {(logs[c.id] || []).length === 0 ? (
                  <p className="text-xs text-warm-400">暂无执行记录</p>
                ) : (
                  <div className="space-y-2">
                    {logs[c.id]!.map((log) => (
                      <div key={log.id} className="bg-warm-900 text-success-400 rounded p-3 font-mono text-xs">
                        <div className="flex items-center justify-between text-warm-500 mb-1">
                          <span>$ {log.command}</span>
                          <span className="text-[10px]">
                            exit={log.exit_code} · {log.duration_ms}ms · {new Date(log.executed_at).toLocaleTimeString()}
                          </span>
                        </div>
                        {log.stdout && <pre className="text-success-300 whitespace-pre-wrap">{log.stdout}</pre>}
                        {log.stderr && <pre className="text-danger-400 whitespace-pre-wrap">{log.stderr}</pre>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Seccomp / workspace info */}
            <div className="border-t border-warm-100 px-4 py-2 flex gap-4 text-[11px] text-warm-400">
              <span>[lock] Seccomp: {c.seccomp_profile}</span>
              <span>[folder] Workspace: {c.workspace_path}</span>
              <span>[timer] 空闲超时: {c.idle_timeout_s}s</span>
              <span>[alarm] 最大运行: {c.max_runtime_s}s</span>
            </div>
          </div>
        ))}

        {containers.length === 0 && !showCreate && (
          <div className="text-center py-16 text-warm-400">
            <p className="text-5xl mb-4">[whale]</p>
            <p className="text-lg mb-1">暂无沙箱容器</p>
            <p className="text-sm">创建隔离容器后，Agent 可在安全环境中执行代码</p>
          </div>
        )}
      </div>
    </div>
  );
}

function MiniStatCard({ icon, label, value, color }: { icon: string; label: string; value: string | number; color?: string }) {
  return (
    <div className="card p-3 flex items-center gap-3">
      <span className="text-xl">{icon}</span>
      <div>
        <p className="text-[10px] text-warm-400">{label}</p>
        <p className={`text-sm font-bold ${color === 'green' ? 'text-success-600' : 'text-warm-700'}`}>{value}</p>
      </div>
    </div>
  );
}
