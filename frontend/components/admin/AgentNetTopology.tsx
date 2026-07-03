'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import type {
  AgentCapability,
  AgentNetTask,
  AgentNetDAG,
  AgentSpawn,
  SharedMemoryEntry,
  AgentNetStats,
  TopologyResponse,
  TopologyNode,
  TopologyEdge,
} from '../../types';
import ParticleTopologyCanvas from './ParticleTopologyCanvas';

// ── Status color constants ───────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  idle: '#22c55e',
  busy: '#f59e0b',
  overloaded: '#ef4444',
  offline: '#9ca3af',
  pending: '#6b7280',
  assigned: '#3b82f6',
  running: '#f59e0b',
  completed: '#22c55e',
  failed: '#ef4444',
  destroyed: '#9ca3af',
  created: '#8b5cf6',
  ready: '#06b6d4',
  cancelled: '#9ca3af',
};

const STATUS_LABELS: Record<string, string> = {
  idle: '空闲',
  busy: '忙碌',
  overloaded: '过载',
  offline: '离线',
  pending: '等待中',
  assigned: '已分配',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  destroyed: '已销毁',
  created: '已创建',
  ready: '就绪',
  cancelled: '已取消',
};

const STRATEGY_LABELS: Record<string, string> = {
  'round-robin': '轮询',
  'least-loaded': '最少负载',
  'capability-match': '能力匹配',
  'cost-optimized': '成本优化',
};

type TabKey = 'overview' | 'topology' | 'capabilities' | 'tasks' | 'dag' | 'memory';

export default function AgentNetTopology({
  authHeaders,
  setNotice,
}: {
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
}): JSX.Element {
  const [activeTab, setActiveTab] = useState<TabKey>('overview');
  const [stats, setStats] = useState<AgentNetStats | null>(null);
  const [topology, setTopology] = useState<TopologyResponse | null>(null);
  const [capabilities, setCapabilities] = useState<AgentCapability[]>([]);
  const [tasks, setTasks] = useState<AgentNetTask[]>([]);
  const [dags, setDags] = useState<AgentNetDAG[]>([]);
  const [memories, setMemories] = useState<SharedMemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);

  // Filters
  const [taskStatusFilter, setTaskStatusFilter] = useState('');
  const [dagStatusFilter, setDagStatusFilter] = useState('');
  const [memoryAgentFilter, setMemoryAgentFilter] = useState('');

  // DAG creation form
  const [showDagCreate, setShowDagCreate] = useState(false);
  const [newDagName, setNewDagName] = useState('');
  const [newDagStrategy, setNewDagStrategy] = useState('capability-match');

  // Fetch functions
  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch('/agentnet/stats', { headers: authHeaders() });
      if (res.ok) setStats(await res.json());
    } catch { /* ignore */ }
  }, [authHeaders]);

  const fetchTopology = useCallback(async () => {
    try {
      const res = await fetch('/agentnet/topology', { headers: authHeaders() });
      if (res.ok) setTopology(await res.json());
    } catch { /* ignore */ }
  }, [authHeaders]);

  const fetchCapabilities = useCallback(async () => {
    try {
      const res = await fetch('/agentnet/capabilities', { headers: authHeaders() });
      if (res.ok) setCapabilities(await res.json());
    } catch { /* ignore */ }
  }, [authHeaders]);

  const fetchTasks = useCallback(async () => {
    try {
      const url = taskStatusFilter
        ? `/agentnet/tasks?status=${taskStatusFilter}`
        : '/agentnet/tasks';
      const res = await fetch(url, { headers: authHeaders() });
      if (res.ok) setTasks(await res.json());
    } catch { /* ignore */ }
  }, [authHeaders, taskStatusFilter]);

  const fetchDags = useCallback(async () => {
    try {
      const res = await fetch('/agentnet/dag', { headers: authHeaders() });
      if (res.ok) setDags(await res.json());
    } catch { /* ignore */ }
  }, [authHeaders]);

  const fetchMemories = useCallback(async () => {
    try {
      const url = memoryAgentFilter
        ? `/agentnet/memory?agent_id=${encodeURIComponent(memoryAgentFilter)}`
        : '/agentnet/memory';
      const res = await fetch(url, { headers: authHeaders() });
      if (res.ok) setMemories(await res.json());
    } catch { /* ignore */ }
  }, [authHeaders, memoryAgentFilter]);

  useEffect(() => {
    setLoading(true);
    fetchStats();
    fetchCapabilities();
    fetchTasks();
    fetchDags();
    fetchMemories();
    fetchTopology();
    setLoading(false);
  }, [fetchStats, fetchCapabilities, fetchTasks, fetchDags, fetchMemories, fetchTopology]);

  const handleCreateDag = async () => {
    if (!newDagName.trim()) {
      setNotice('请输入 DAG 名称');
      return;
    }
    try {
      const res = await fetch('/agentnet/dag', {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newDagName,
          strategy: newDagStrategy,
          nodes: [],
          edges: [],
        }),
      });
      if (res.ok) {
        setNotice('DAG 创建成功');
        setShowDagCreate(false);
        setNewDagName('');
        fetchDags();
      } else {
        const err = await res.json();
        setNotice('创建失败: ' + (err.error || 'unknown'));
      }
    } catch {
      setNotice('创建 DAG 失败');
    }
  };

  const handleRegisterCapability = async () => {
    const agentId = prompt('Agent ID:');
    if (!agentId) return;
    const displayName = prompt('Display Name:') || agentId;
    const caps = prompt('Capabilities (comma-separated):') || 'general';
    try {
      const res = await fetch('/agentnet/capabilities', {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_id: agentId,
          display_name: displayName,
          capabilities: caps.split(',').map((s) => s.trim()),
          preferred_tools: [],
          quality_score: 0.85,
          current_load: 0,
          max_concurrent: 5,
          cost_per_task: 0.01,
          status: 'idle',
        }),
      });
      if (res.ok) {
        setNotice(`Agent ${agentId} 注册成功`);
        fetchCapabilities();
        fetchStats();
      }
    } catch {
      setNotice('注册失败');
    }
  };

  const handleTriggerHeartbeat = async (agentId: string) => {
    try {
      await fetch(`/agentnet/heartbeat/${encodeURIComponent(agentId)}`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_load: 0, status: 'idle' }),
      });
      fetchCapabilities();
      setNotice(`Heartbeat sent for ${agentId}`);
    } catch {
      setNotice('Heartbeat failed');
    }
  };

  // ── Tab definitions ────────────────────────────────────────────────
  const tabs: { key: TabKey; label: string; icon: string }[] = [
    { key: 'overview', label: '概览', icon: '📊' },
    { key: 'topology', label: '拓扑图', icon: '🕸️' },
    { key: 'capabilities', label: '能力清单', icon: '🤖' },
    { key: 'tasks', label: '任务管理', icon: '📋' },
    { key: 'dag', label: 'DAG 编排', icon: '🧬' },
    { key: 'memory', label: '共享记忆', icon: '💭' },
  ];

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* ── Tab bar ─────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-2 border-b border-gray-200 pb-3">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? 'bg-primary-50 text-primary-700 shadow-sm'
                : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
            }`}
          >
            <span>{tab.icon}</span>
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Tab: Overview ───────────────────────────────────────── */}
      {activeTab === 'overview' && stats && (
        <div className="space-y-6">
          {/* Stat cards */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
            <StatCard icon="🤖" label="注册 Agent" value={stats.total_agents} color="blue" />
            <StatCard icon="🟢" label="活跃 Agent" value={stats.active_agents} color="green" />
            <StatCard icon="📋" label="总任务数" value={stats.total_tasks} color="purple" />
            <StatCard icon="🧬" label="活跃 DAG" value={stats.active_dags} color="amber" />
            <StatCard icon="⭐" label="平均质量分" value={stats.avg_quality_score.toFixed(2)} color="cyan" />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Agent status distribution */}
            <div className="card p-5">
              <h3 className="text-h4 mb-4">🤖 Agent 状态分布</h3>
              <div className="space-y-3">
                {Object.entries(stats.agents_by_status).map(([status, count]) => (
                  <div key={status} className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span
                        className="inline-block w-3 h-3 rounded-full"
                        style={{ backgroundColor: STATUS_COLORS[status] || '#9ca3af' }}
                      />
                      <span className="text-sm text-gray-600">{STATUS_LABELS[status] || status}</span>
                    </div>
                    <div className="flex items-center gap-3">
                      <div className="w-32 h-2 bg-gray-100 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all"
                          style={{
                            width: stats.total_agents > 0 ? `${(count / stats.total_agents) * 100}%` : '0%',
                            backgroundColor: STATUS_COLORS[status] || '#9ca3af',
                          }}
                        />
                      </div>
                      <span className="text-sm font-semibold text-gray-700 w-8 text-right">{count}</span>
                    </div>
                  </div>
                ))}
                {Object.keys(stats.agents_by_status).length === 0 && (
                  <p className="text-sm text-gray-400">暂无数据</p>
                )}
              </div>
            </div>

            {/* Task status distribution */}
            <div className="card p-5">
              <h3 className="text-h4 mb-4">📋 任务状态分布</h3>
              <div className="space-y-3">
                {Object.entries(stats.tasks_by_status).map(([status, count]) => (
                  <div key={status} className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span
                        className="inline-block w-3 h-3 rounded-full"
                        style={{ backgroundColor: STATUS_COLORS[status] || '#9ca3af' }}
                      />
                      <span className="text-sm text-gray-600">{STATUS_LABELS[status] || status}</span>
                    </div>
                    <div className="flex items-center gap-3">
                      <div className="w-32 h-2 bg-gray-100 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all"
                          style={{
                            width: stats.total_tasks > 0 ? `${(count / stats.total_tasks) * 100}%` : '0%',
                            backgroundColor: STATUS_COLORS[status] || '#9ca3af',
                          }}
                        />
                      </div>
                      <span className="text-sm font-semibold text-gray-700 w-8 text-right">{count}</span>
                    </div>
                  </div>
                ))}
                {Object.keys(stats.tasks_by_status).length === 0 && (
                  <p className="text-sm text-gray-400">暂无数据</p>
                )}
              </div>
            </div>
          </div>

          {/* Quick summary */}
          <div className="card p-5">
            <h3 className="text-h4 mb-3">🌐 AgentNet 总览</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <div>
                <span className="text-gray-400">活跃 DAG</span>
                <p className="text-lg font-bold text-gray-800">{stats.active_dags}</p>
              </div>
              <div>
                <span className="text-gray-400">活跃 Spawn</span>
                <p className="text-lg font-bold text-gray-800">{stats.active_spawns}</p>
              </div>
              <div>
                <span className="text-gray-400">共享记忆条目</span>
                <p className="text-lg font-bold text-gray-800">{stats.memory_entries}</p>
              </div>
              <div>
                <span className="text-gray-400">平均质量分数</span>
                <p className="text-lg font-bold text-gray-800">{stats.avg_quality_score.toFixed(3)}</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Tab: Topology ────────────────────────────────────────── */}
      {activeTab === 'topology' && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <h3 className="text-h4">🕸️ AgentNet 拓扑图</h3>
            <button
              onClick={() => { fetchTopology(); setNotice('拓扑已刷新'); }}
              className="btn-secondary text-sm"
            >
              🔄 刷新
            </button>
          </div>

          <ParticleTopologyCanvas nodes={topology?.nodes || []} edges={topology?.edges || []} />

          {/* Node & Edge tables */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="card p-4">
              <h4 className="text-sm font-semibold text-gray-600 mb-3">节点 ({topology?.nodes.length || 0})</h4>
              <div className="max-h-64 overflow-y-auto space-y-2">
                {(topology?.nodes || []).map((n) => (
                  <div key={n.id} className="flex items-center justify-between text-sm border-b border-gray-50 pb-2">
                    <div className="flex items-center gap-2">
                      <span
                        className="inline-block w-2 h-2 rounded-full"
                        style={{ backgroundColor: STATUS_COLORS[n.status] || '#9ca3af' }}
                      />
                      <span className="font-mono text-xs text-gray-500">{n.type}</span>
                      <span className="text-gray-700">{n.label || n.id}</span>
                    </div>
                    <span className="text-xs text-gray-400">{STATUS_LABELS[n.status] || n.status}</span>
                  </div>
                ))}
                {(topology?.nodes || []).length === 0 && (
                  <p className="text-sm text-gray-400">暂无节点数据</p>
                )}
              </div>
            </div>
            <div className="card p-4">
              <h4 className="text-sm font-semibold text-gray-600 mb-3">边 ({topology?.edges.length || 0})</h4>
              <div className="max-h-64 overflow-y-auto space-y-2">
                {(topology?.edges || []).map((e, i) => (
                  <div key={i} className="flex items-center justify-between text-sm border-b border-gray-50 pb-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-gray-400">{e.from}</span>
                      <span className="text-gray-300">→</span>
                      <span className="font-mono text-xs text-gray-400">{e.to}</span>
                    </div>
                    <span className="text-xs px-2 py-0.5 rounded bg-gray-100 text-gray-500">{e.label}</span>
                  </div>
                ))}
                {(topology?.edges || []).length === 0 && (
                  <p className="text-sm text-gray-400">暂无边数据</p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Tab: Capabilities ────────────────────────────────────── */}
      {activeTab === 'capabilities' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-h4">🤖 Agent 能力清单</h3>
            <button onClick={handleRegisterCapability} className="btn-primary text-sm">
              + 注册 Agent
            </button>
          </div>
          <div className="grid gap-4">
            {capabilities.map((cap) => (
              <div key={cap.agent_id} className="card p-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-3 mb-2">
                      <h4 className="font-semibold text-gray-800">{cap.display_name || cap.agent_id}</h4>
                      <span
                        className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium"
                        style={{
                          backgroundColor: (STATUS_COLORS[cap.status] || '#9ca3af') + '20',
                          color: STATUS_COLORS[cap.status] || '#9ca3af',
                        }}
                      >
                        <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: STATUS_COLORS[cap.status] }} />
                        {STATUS_LABELS[cap.status] || cap.status}
                      </span>
                    </div>
                    <p className="text-xs text-gray-400 font-mono mb-2">{cap.agent_id}</p>
                    <div className="flex flex-wrap gap-1 mb-3">
                      {cap.capabilities.map((c, i) => (
                        <span key={i} className="text-xs px-2 py-0.5 rounded bg-primary-50 text-primary-700">
                          {c}
                        </span>
                      ))}
                    </div>
                    <div className="grid grid-cols-4 gap-3 text-xs text-gray-500">
                      <div>
                        <span className="text-gray-400">质量分</span>
                        <p className="font-semibold text-gray-700">{cap.quality_score.toFixed(2)}</p>
                      </div>
                      <div>
                        <span className="text-gray-400">负载</span>
                        <p className="font-semibold text-gray-700">{cap.current_load}/{cap.max_concurrent}</p>
                      </div>
                      <div>
                        <span className="text-gray-400">单任务成本</span>
                        <p className="font-semibold text-gray-700">${cap.cost_per_task.toFixed(4)}</p>
                      </div>
                      <div>
                        <span className="text-gray-400">心跳</span>
                        <p className="font-semibold text-gray-700 text-[11px]">
                          {cap.last_heartbeat ? new Date(cap.last_heartbeat).toLocaleTimeString() : '-'}
                        </p>
                      </div>
                    </div>
                  </div>
                  <button
                    onClick={() => handleTriggerHeartbeat(cap.agent_id)}
                    className="text-xs text-gray-400 hover:text-primary-500 transition-colors shrink-0 ml-3"
                    title="发送心跳"
                  >
                    💓
                  </button>
                </div>
              </div>
            ))}
            {capabilities.length === 0 && (
              <div className="text-center py-12 text-gray-400">
                <p className="text-4xl mb-3">🤖</p>
                <p>暂无注册 Agent，点击"注册 Agent"开始</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Tab: Tasks ───────────────────────────────────────────── */}
      {activeTab === 'tasks' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-h4">📋 任务管理</h3>
            <select
              value={taskStatusFilter}
              onChange={(e) => setTaskStatusFilter(e.target.value)}
              className="input-field text-sm w-40"
            >
              <option value="">全部状态</option>
              <option value="pending">等待中</option>
              <option value="assigned">已分配</option>
              <option value="running">运行中</option>
              <option value="completed">已完成</option>
              <option value="failed">失败</option>
            </select>
          </div>
          <div className="grid gap-3">
            {tasks.map((task) => (
              <div key={task.task_id} className="card p-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span
                        className="inline-block w-2 h-2 rounded-full"
                        style={{ backgroundColor: STATUS_COLORS[task.status] || '#9ca3af' }}
                      />
                      <span className="text-xs font-medium text-gray-500">{STATUS_LABELS[task.status]}</span>
                      {task.category && (
                        <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">{task.category}</span>
                      )}
                      {task.dag_id && (
                        <span className="text-xs text-purple-500 font-mono">{task.dag_id}</span>
                      )}
                    </div>
                    <p className="text-sm font-medium text-gray-800">{task.description}</p>
                    <div className="flex gap-4 mt-2 text-xs text-gray-400">
                      <span>需求: {task.required_capability}</span>
                      {task.assigned_agent && <span>分配给: {task.assigned_agent}</span>}
                      <span>创建: {new Date(task.created_at).toLocaleString()}</span>
                      {task.completed_at && <span>完成: {new Date(task.completed_at).toLocaleString()}</span>}
                    </div>
                    {task.error && (
                      <p className="text-xs text-red-500 mt-1">错误: {task.error}</p>
                    )}
                  </div>
                </div>
              </div>
            ))}
            {tasks.length === 0 && (
              <div className="text-center py-12 text-gray-400">
                <p className="text-4xl mb-3">📋</p>
                <p>暂无任务</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Tab: DAG ──────────────────────────────────────────────── */}
      {activeTab === 'dag' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-h4">🧬 DAG 编排</h3>
            <button onClick={() => setShowDagCreate(true)} className="btn-primary text-sm">
              + 创建 DAG
            </button>
          </div>

          {/* Create DAG modal */}
          {showDagCreate && (
            <div className="card p-5 border-2 border-primary-100 bg-primary-50/30">
              <h4 className="font-semibold text-gray-800 mb-3">创建新 DAG</h4>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">名称</label>
                  <input
                    type="text"
                    value={newDagName}
                    onChange={(e) => setNewDagName(e.target.value)}
                    placeholder="例如: 微服务构建流水线"
                    className="input-field text-sm w-full"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">调度策略</label>
                  <select
                    value={newDagStrategy}
                    onChange={(e) => setNewDagStrategy(e.target.value)}
                    className="input-field text-sm w-full"
                  >
                    <option value="capability-match">能力匹配</option>
                    <option value="least-loaded">最少负载</option>
                    <option value="round-robin">轮询</option>
                    <option value="cost-optimized">成本优化</option>
                  </select>
                </div>
              </div>
              <div className="flex gap-2">
                <button onClick={handleCreateDag} className="btn-primary text-sm">创建</button>
                <button onClick={() => setShowDagCreate(false)} className="btn-secondary text-sm">取消</button>
              </div>
            </div>
          )}

          <div className="grid gap-4">
            {dags.map((dag) => (
              <div key={dag.dag_id} className="card p-4">
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <span
                        className="inline-block w-2 h-2 rounded-full"
                        style={{ backgroundColor: STATUS_COLORS[dag.status] || '#9ca3af' }}
                      />
                      <h4 className="font-semibold text-gray-800">{dag.name}</h4>
                      <span className="text-xs text-gray-400 font-mono">{dag.dag_id}</span>
                    </div>
                    <p className="text-xs text-gray-400">
                      策略: {STRATEGY_LABELS[dag.strategy] || dag.strategy} · 节点: {dag.nodes.length} · 边: {dag.edges.length} · 创建: {new Date(dag.created_at).toLocaleString()}
                    </p>
                  </div>
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                    dag.status === 'running' ? 'bg-amber-50 text-amber-600' :
                    dag.status === 'completed' ? 'bg-green-50 text-green-600' :
                    dag.status === 'failed' ? 'bg-red-50 text-red-600' :
                    'bg-gray-100 text-gray-500'
                  }`}>
                    {STATUS_LABELS[dag.status] || dag.status}
                  </span>
                </div>

                {/* DAG nodes visualization */}
                {dag.nodes.length > 0 && (
                  <div className="mt-3 space-y-1">
                    <p className="text-xs text-gray-400 mb-2">DAG 节点 ({dag.nodes.length})</p>
                    {dag.nodes.map((node) => (
                      <div
                        key={node.id}
                        className="flex items-center justify-between text-sm py-2 px-3 rounded bg-gray-50"
                      >
                        <div className="flex items-center gap-2">
                          <span
                            className="inline-block w-2 h-2 rounded-full"
                            style={{ backgroundColor: STATUS_COLORS[node.status] || '#9ca3af' }}
                          />
                          <span className="font-mono text-xs text-gray-500">{node.id}</span>
                          <span className="text-gray-700">{node.description}</span>
                          {node.required_capability && (
                            <span className="text-xs text-primary-500">{node.required_capability}</span>
                          )}
                        </div>
                        <div className="flex items-center gap-3 text-xs text-gray-400">
                          {node.dependencies.length > 0 && (
                            <span>依赖: {node.dependencies.join(', ')}</span>
                          )}
                          <span>优先级: {node.priority}</span>
                          <span>{STATUS_LABELS[node.status] || node.status}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {dag.nodes.length === 0 && (
                  <p className="text-xs text-gray-400 mt-2">暂无节点，使用 API 添加节点</p>
                )}
              </div>
            ))}
            {dags.length === 0 && !showDagCreate && (
              <div className="text-center py-12 text-gray-400">
                <p className="text-4xl mb-3">🧬</p>
                <p>暂无 DAG，点击"创建 DAG"开始</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Tab: Shared Memory ────────────────────────────────────── */}
      {activeTab === 'memory' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-h4">💭 共享记忆通道</h3>
            <input
              type="text"
              value={memoryAgentFilter}
              onChange={(e) => setMemoryAgentFilter(e.target.value)}
              placeholder="按 Agent ID 过滤..."
              className="input-field text-sm w-48"
            />
          </div>
          <div className="grid gap-3">
            {memories.map((mem) => (
              <div key={mem.id} className="card p-4">
                <div className="flex items-start gap-3">
                  <span className="text-lg">💭</span>
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-mono text-gray-400">{mem.agent_id}</span>
                      {mem.intent && (
                        <span className="text-xs px-1.5 py-0.5 rounded bg-purple-50 text-purple-600">{mem.intent}</span>
                      )}
                      {mem.target && (
                        <span className="text-xs text-gray-400">→ {mem.target}</span>
                      )}
                      <span className="text-xs text-gray-300">{new Date(mem.timestamp).toLocaleTimeString()}</span>
                    </div>
                    <p className="text-sm text-gray-700">{mem.content}</p>
                  </div>
                </div>
              </div>
            ))}
            {memories.length === 0 && (
              <div className="text-center py-12 text-gray-400">
                <p className="text-4xl mb-3">💭</p>
                <p>共享记忆通道为空 — Agent 之间通过此通道进行自发通信</p>
                <p className="text-xs mt-2">当 Agent 开始协作时，自发指令传递模式将出现在此</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── StatCard sub-component ───────────────────────────────────────────

function StatCard({
  icon,
  label,
  value,
  color,
}: {
  icon: string;
  label: string;
  value: string | number;
  color: 'blue' | 'green' | 'purple' | 'amber' | 'cyan';
}): JSX.Element {
  const colorMap = {
    blue: 'border-blue-200 bg-blue-50/50',
    green: 'border-green-200 bg-green-50/50',
    purple: 'border-purple-200 bg-purple-50/50',
    amber: 'border-amber-200 bg-amber-50/50',
    cyan: 'border-cyan-200 bg-cyan-50/50',
  };

  const iconMap = {
    blue: 'text-blue-600',
    green: 'text-green-600',
    purple: 'text-purple-600',
    amber: 'text-amber-600',
    cyan: 'text-cyan-600',
  };

  return (
    <div className={`card p-4 border-l-4 ${colorMap[color]}`}>
      <div className="flex items-center gap-2 mb-2">
        <span className={iconMap[color]}>{icon}</span>
        <span className="text-xs text-gray-400">{label}</span>
      </div>
      <p className="text-2xl font-bold text-gray-800">{value}</p>
    </div>
  );
}
