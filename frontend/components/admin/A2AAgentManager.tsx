'use client';

// A2A Agent Manager (P2-2)
// Management UI for the Agent-to-Agent protocol: agent card viewer,
// discovery, registration, and task testing.

import { useState, useEffect, useRef, type JSX } from 'react';
import { useA2AStore } from '../../stores/a2aStore';
import type { A2AAgentCard } from '../../types';
import A2ASecurityPanel from './A2ASecurityPanel';

// ── Sub-components ────────────────────────────────────────────────────

function StatusBadge({ status }: { status?: string }): JSX.Element {
  const map: Record<string, { label: string; cls: string }> = {
    active: { label: '活跃', cls: 'bg-green-100 text-green-700 border-green-200' },
    inactive: { label: '离线', cls: 'bg-warm-100 text-warm-500 border-warm-200' },
    error: { label: '异常', cls: 'bg-red-100 text-red-700 border-red-200' },
  };
  const s = map[status || ''] || { label: status || '未知', cls: 'bg-warm-50 text-warm-500 border-warm-150' };
  return <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${s.cls}`}>{s.label}</span>;
}

function SourceBadge({ source }: { source?: string }): JSX.Element {
  const isInternal = source === 'internal';
  return (
    <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${
      isInternal ? 'bg-blue-50 text-blue-600 border-blue-200' : 'bg-purple-50 text-purple-600 border-purple-200'
    }`}>
      {isInternal ? '内部' : '外部'}
    </span>
  );
}

function AgentCard({ agent, isSelected, onSelect, onDelete }: {
  agent: A2AAgentCard;
  isSelected: boolean;
  onSelect: () => void;
  onDelete?: () => void;
}): JSX.Element {
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left rounded-xl border p-4 transition-all duration-200 hover:shadow-sm ${
        isSelected
          ? 'border-primary-300 bg-primary-50/50 shadow-sm'
          : 'border-warm-200 bg-white hover:border-primary-200'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-1">
            <h4 className="text-sm font-semibold text-warm-900 truncate">{agent.name}</h4>
            <StatusBadge status={agent.status} />
            <SourceBadge source={agent.source} />
          </div>
          <p className="text-xs text-warm-500 line-clamp-2 mb-2">{agent.description}</p>
        </div>
      </div>

      {/* Skills */}
      <div className="flex flex-wrap gap-1 mt-2">
        {agent.skills.slice(0, 4).map((skill) => (
          <span key={skill.id} className="text-[10px] px-1.5 py-0.5 rounded bg-warm-100 text-warm-600 font-medium">
            {skill.name}
          </span>
        ))}
        {agent.skills.length > 4 && (
          <span className="text-[10px] text-warm-400">+{agent.skills.length - 4}</span>
        )}
      </div>

      {/* Endpoint */}
      <div className="mt-2 flex items-center gap-1">
        <span className="material-symbols-outlined text-[12px] text-warm-400">link</span>
        <span className="text-[10px] text-warm-400 font-mono truncate">{agent.endpoints.taskApi}</span>
      </div>
    </button>
  );
}

function AgentDetail({ agent }: { agent: A2AAgentCard }): JSX.Element {
  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <StatusBadge status={agent.status} />
          <SourceBadge source={agent.source} />
          {agent.version && <span className="text-[11px] text-warm-500">v{agent.version}</span>}
        </div>
        <p className="text-sm text-warm-600">{agent.description}</p>
      </div>

      {/* Provider */}
      {agent.provider && (
        <div className="rounded-lg bg-warm-50 p-3">
          <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-1">Provider</h5>
          <p className="text-sm text-warm-800">{agent.provider.name || agent.provider.organization}</p>
          {agent.provider.url && <p className="text-xs text-warm-400 font-mono">{agent.provider.url}</p>}
        </div>
      )}

      {/* Capabilities */}
      <div>
        <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">Capabilities</h5>
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(agent.capabilities).map(([key, val]) => (
            <span key={key} className={`text-[10px] px-2 py-0.5 rounded-full border ${
              val ? 'bg-green-50 text-green-600 border-green-200' : 'bg-warm-50 text-warm-400 border-warm-100'
            }`}>
              {key}
            </span>
          ))}
        </div>
      </div>

      {/* Skills */}
      <div>
        <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">
          Skills ({agent.skills.length})
        </h5>
        <div className="space-y-2">
          {agent.skills.map((skill) => (
            <div key={skill.id} className="rounded-lg border border-warm-200 p-3">
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium text-warm-800">{skill.name}</span>
                <span className="text-[10px] text-warm-400 font-mono">{skill.id}</span>
              </div>
              {skill.description && <p className="text-xs text-warm-500 mb-1">{skill.description}</p>}
              <div className="flex flex-wrap gap-1">
                {skill.tags.map((tag) => (
                  <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-primary-100 text-primary-600">
                    {tag}
                  </span>
                ))}
              </div>
              {skill.examples && skill.examples.length > 0 && (
                <div className="mt-2 space-y-1">
                  {skill.examples.map((ex, i) => (
                    <p key={i} className="text-[11px] text-warm-500 font-mono bg-warm-50 px-2 py-1 rounded">
                      {ex}
                    </p>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Endpoints */}
      <div>
        <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">Endpoints</h5>
        <div className="rounded-lg bg-warm-50 p-3 space-y-1.5">
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-warm-400 w-16 shrink-0">Task API:</span>
            <code className="text-xs text-warm-700 font-mono">{agent.endpoints.taskApi}</code>
          </div>
          {agent.endpoints.streaming && (
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-warm-400 w-16 shrink-0">Streaming:</span>
              <code className="text-xs text-warm-700 font-mono">{agent.endpoints.streaming}</code>
            </div>
          )}
        </div>
      </div>

      {/* Authentication */}
      {agent.authSchemes && agent.authSchemes.length > 0 && (
        <div>
          <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">Auth</h5>
          {agent.authSchemes.map((scheme, i) => (
            <div key={i} className="flex items-center gap-2 text-xs text-warm-600">
              <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">
                {scheme.type}
              </span>
              {scheme.description && <span>{scheme.description}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────

export interface A2AAgentManagerProps {
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
}

export default function A2AAgentManager({ authHeaders, setNotice }: A2AAgentManagerProps): JSX.Element {
  const {
    agents, selfCard, discoveryResults, selectedAgentUrl, taskResult,
    loading, discoveryLoading, taskLoading, demoMode,
    loadAgents, loadSelfCard, discoverAgents, registerAgent, unregisterAgent, sendTask, selectAgent,
  } = useA2AStore();

  const [registerUrl, setRegisterUrl] = useState('');
  const [discoveryCapabilities, setDiscoveryCapabilities] = useState('');
  const [taskMessage, setTaskMessage] = useState('');
  const [activeTab, setActiveTab] = useState<'agents' | 'discover' | 'test' | 'security'>('agents');
  const [taskLatencyMs, setTaskLatencyMs] = useState<number | null>(null);
  const taskStartRef = useRef<number>(0);

  useEffect(() => {
    void loadAgents();
    void loadSelfCard();
  }, []);

  // Track task latency: when taskLoading transitions from true to false, record elapsed time
  useEffect(() => {
    if (!taskLoading && taskStartRef.current > 0 && taskResult) {
      const elapsed = performance.now() - taskStartRef.current;
      setTaskLatencyMs(elapsed);
      taskStartRef.current = 0;
    }
  }, [taskLoading, taskResult]);

  const selectedAgent = agents.find((a) => a.url === selectedAgentUrl) || null;

  return (
    <section className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-warm-900 flex items-center gap-2">
            <span className="material-symbols-outlined text-primary-500">hub</span>
            A2A Agent 互操作
          </h3>
          <p className="text-sm text-warm-500 mt-0.5">
            Agent-to-Agent 开放协议 — Agent Card 发现、注册、跨 Agent 任务调度
          </p>
        </div>
        {demoMode && (
          <span className="tag tag-amber text-[11px] flex items-center gap-1">
            <span className="material-symbols-outlined text-[14px]">info</span>
            Demo
          </span>
        )}
      </div>

      {/* Info Banner */}
      <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3">
        <p className="text-xs text-blue-700 flex items-start gap-2">
          <span className="material-symbols-outlined text-[14px] shrink-0 mt-0.5">info</span>
          <span>
            <strong>A2A (Agent-to-Agent)</strong> 是 Google 发布的 Agent 互操作开放标准。
            AgentHub 自带的 Agent Card 发布于 <code className="text-blue-800 bg-blue-100 px-1 rounded">/.well-known/agent-card.json</code>，
            可通过 <code className="text-blue-800 bg-blue-100 px-1 rounded">/platform/a2a/</code> 端点管理外部 A2A Agent。
          </span>
        </p>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-warm-200">
        {([
          ['agents', '已注册 Agent'],
          ['discover', '发现 Agent'],
          ['test', '任务测试'],
          ['security', '安全'],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setActiveTab(key)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              activeTab === key
                ? 'border-primary-500 text-primary-600'
                : 'border-transparent text-warm-500 hover:text-warm-700'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* ── Agents Tab ──────────────────────────────────────────────── */}
      {activeTab === 'agents' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Left: Agent List */}
          <div className="lg:col-span-1 space-y-3">
            {/* Register form */}
            <div className="rounded-xl border border-warm-200 bg-warm-50/50 p-3">
              <h5 className="text-xs font-semibold text-warm-600 mb-2">注册外部 A2A Agent</h5>
              <div className="flex gap-1.5">
                <input
                  className="input flex-1 text-xs"
                  placeholder="https://agent.example.com"
                  value={registerUrl}
                  onChange={(e) => setRegisterUrl(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') { void registerAgent(registerUrl); setRegisterUrl(''); } }}
                />
                <button
                  className="btn-primary text-xs px-3 py-1.5"
                  disabled={!registerUrl.trim()}
                  onClick={() => { void registerAgent(registerUrl); setRegisterUrl(''); }}
                >
                  注册
                </button>
              </div>
            </div>

            {/* Agent list */}
            {loading ? (
              <div className="space-y-2">
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="skeleton skeleton-text h-24 rounded-xl" />
                ))}
              </div>
            ) : (
              <div className="space-y-2 max-h-[500px] overflow-y-auto">
                {agents.map((agent) => (
                  <AgentCard
                    key={agent.url}
                    agent={agent}
                    isSelected={selectedAgentUrl === agent.url}
                    onSelect={() => selectAgent(agent.url)}
                    onDelete={agent.source === 'external' ? () => unregisterAgent(agent.url) : undefined}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Right: Agent Detail */}
          <div className="lg:col-span-2">
            {selectedAgent ? (
              <div className="rounded-xl border border-warm-200 bg-white p-5">
                <div className="flex items-center justify-between mb-4">
                  <h4 className="text-base font-semibold text-warm-900">{selectedAgent.name}</h4>
                  {selectedAgent.source === 'external' && (
                    <button
                      className="btn-danger text-xs px-3 py-1"
                      onClick={() => unregisterAgent(selectedAgent.url)}
                    >
                      注销
                    </button>
                  )}
                </div>
                <AgentDetail agent={selectedAgent} />
              </div>
            ) : (
              <div className="rounded-xl border border-dashed border-warm-300 bg-warm-50 px-6 py-16 text-center">
                <span className="material-symbols-outlined text-4xl text-warm-300 mb-2 block">select_window</span>
                <p className="text-sm text-warm-500">选择一个 Agent 查看详情</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Discover Tab ────────────────────────────────────────────── */}
      {activeTab === 'discover' && (
        <div className="space-y-4">
          <div className="flex gap-2">
            <input
              className="input flex-1"
              placeholder="输入能力标签搜索，如: rag, code, review, data..."
              value={discoveryCapabilities}
              onChange={(e) => setDiscoveryCapabilities(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void discoverAgents(discoveryCapabilities.split(',').map((s) => s.trim()).filter(Boolean)); }}
            />
            <button
              className="btn-primary px-4"
              disabled={discoveryLoading || !discoveryCapabilities.trim()}
              onClick={() => void discoverAgents(discoveryCapabilities.split(',').map((s) => s.trim()).filter(Boolean))}
            >
              {discoveryLoading ? (
                <span className="material-symbols-outlined text-sm animate-spin">progress_activity</span>
              ) : '发现'}
            </button>
          </div>

          {discoveryResults.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {discoveryResults.map((agent) => (
                <AgentCard
                  key={agent.url}
                  agent={agent}
                  isSelected={false}
                  onSelect={() => {
                    selectAgent(agent.url);
                    setActiveTab('agents');
                  }}
                />
              ))}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-warm-300 bg-warm-50 px-6 py-12 text-center">
              <span className="material-symbols-outlined text-4xl text-warm-300 mb-2 block">travel_explore</span>
              <p className="text-sm text-warm-500">输入能力标签以发现匹配的 A2A Agent</p>
              <p className="text-xs text-warm-400 mt-1">常用标签: rag, code, review, data, orchestration, search</p>
            </div>
          )}
        </div>
      )}

      {/* ── Test Tab ────────────────────────────────────────────────── */}
      {activeTab === 'test' && (
        <div className="space-y-4 max-w-2xl">
          <div className="rounded-xl border border-warm-200 bg-white p-4">
            <h5 className="text-sm font-semibold text-warm-800 mb-3">发送 A2A 任务</h5>

            <div className="space-y-3">
              <div>
                <label className="text-xs text-warm-500 mb-1 block">目标 Agent URL</label>
                <select
                  className="input w-full text-sm"
                  value={selectedAgentUrl || ''}
                  onChange={(e) => selectAgent(e.target.value || null)}
                >
                  <option value="">选择 Agent...</option>
                  {agents.map((a) => (
                    <option key={a.url} value={a.url}>{a.name} ({a.url})</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="text-xs text-warm-500 mb-1 block">任务消息</label>
                <textarea
                  className="input w-full text-sm"
                  rows={3}
                  placeholder="输入任务描述..."
                  value={taskMessage}
                  onChange={(e) => setTaskMessage(e.target.value)}
                />
              </div>

              <button
                className="btn-primary"
                disabled={taskLoading || !selectedAgentUrl || !taskMessage.trim()}
                onClick={() => {
                  taskStartRef.current = performance.now();
                  setTaskLatencyMs(null);
                  void sendTask(selectedAgentUrl!, taskMessage);
                }}
              >
                {taskLoading ? (
                  <span className="material-symbols-outlined text-sm animate-spin">progress_activity</span>
                ) : '发送任务'}
              </button>
            </div>
          </div>

          {/* Task Latency */}
          {taskLatencyMs !== null && (
            <div className="rounded-lg border border-primary-150 bg-primary-50/50 px-4 py-2.5 flex items-center gap-2">
              <span className="material-symbols-outlined text-primary-500 text-sm">timer</span>
              <span className="text-xs text-primary-700">
                往返延迟: <strong>{taskLatencyMs.toFixed(0)} ms</strong>
              </span>
            </div>
          )}

          {/* Task Result */}
          {taskResult && (
            <div className="rounded-xl border border-warm-200 bg-warm-50 p-4">
              <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">任务结果</h5>
              <pre className="text-xs text-warm-700 font-mono whitespace-pre-wrap bg-white rounded-lg p-3 border border-warm-150 max-h-[300px] overflow-y-auto">
                {JSON.stringify(taskResult, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
      {/* ── Security Tab ────────────────────────────────────────────── */}
      {activeTab === 'security' && <A2ASecurityPanel />}

    </section>
  );
}
