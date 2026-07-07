'use client';

import React, { useEffect, useState, useCallback, type JSX } from 'react';
import { useAuthStore } from '../../stores/authStore';

interface Agent {
  agentId: string;
  displayName?: string;
  domain: string;
  status: string;
  adapterType: string;
  riskLevel?: string;
  description?: string;
  isDefault?: boolean;
  version?: string;
  model?: string;
}

interface AgentManagePanelProps {
  token?: string;
}

// ── Agent color palette ─────────────────────────────────────────────
const AGENT_COLORS: Record<string, string> = {
  Orchestrator: '#22A3C9',
  Architect: '#6B8EB5',
  CodeGen: '#5B9A6B',
  Review: '#8B6BAE',
  Test: '#4A90B0',
  Deploy: '#C4784A',
  Implement: '#5B8B9A',
  Security: '#C45050',
  Default: '#7A7670',
};

function agentColor(name: string): string {
  for (const [key, color] of Object.entries(AGENT_COLORS)) {
    if (name.toLowerCase().includes(key.toLowerCase())) return color;
  }
  // Deterministic color from name hash
  const hue = name.split('').reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
  return `hsl(${hue}, 40%, 48%)`;
}

function agentInitial(name: string): string {
  return (name || 'A')[0].toUpperCase();
}

export default function AgentManagePanel({ token }: AgentManagePanelProps): JSX.Element {
  const authHeaders = useAuthStore((s) => s.authHeaders);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState('');

  const fetchAgents = useCallback(async () => {
    try {
      const headers = authHeaders();
      const res = await fetch('/api/agent/registry', { headers });
      if (res.ok) {
        const data: Agent[] = await res.json();
        setAgents(data.length ? data : MOCK_AGENTS);
      } else {
        setAgents(MOCK_AGENTS);
      }
    } catch {
      setAgents(MOCK_AGENTS);
    } finally {
      setLoading(false);
    }
  }, [authHeaders]);

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  const handleSetDefault = useCallback((agentId: string) => {
    setAgents((prev) =>
      prev.map((a) => ({ ...a, isDefault: a.agentId === agentId })),
    );
    setNotice(`已将 ${agentId} 设为默认对话模型`);
    setTimeout(() => setNotice(''), 3000);
  }, []);

  const handleDelete = useCallback(
    (agentId: string) => {
      if (confirm(`确认删除智能体 ${agentId}？`)) {
        setAgents((prev) => prev.filter((a) => a.agentId !== agentId));
        setNotice(`已删除智能体 ${agentId}`);
        setTimeout(() => setNotice(''), 3000);
      }
    },
    [],
  );

  const handleTest = useCallback((agentId: string) => {
    setNotice(`正在测试智能体 ${agentId}...`);
    setTimeout(() => setNotice(''), 3000);
  }, []);

  // ── SVG icons for action buttons ──────────────────────────────
  const StarIcon = (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
    </svg>
  );
  const EditIcon = (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
  const PlayIcon = (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="5 3 19 12 5 21 5 3" />
    </svg>
  );
  const HistoryIcon = (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  );
  const TrashIcon = (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
  const PlusIcon = (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* ── Notice banner ── */}
      {notice && (
        <div className="admin-notice">
          <span className="admin-notice-icon">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
          </span>
          <span>{notice}</span>
        </div>
      )}

      {/* ── Section header ── */}
      <div className="admin-section-header">
        <div>
          <h2 className="admin-section-title">
            智能体管理
            <span className="admin-section-count"> · {agents.length} 个智能体</span>
          </h2>
        </div>
        <button className="admin-btn-accent" type="button">
          {PlusIcon}
          添加智能体
        </button>
      </div>

      {/* ── Info prompt ── */}
      <div className="admin-notice">
        <span className="admin-notice-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="16" x2="12" y2="12" />
            <line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
        </span>
        <span>智能体是 AgentHub 的核心执行单元，每个智能体具备独立的领域知识、执行策略与风险等级。默认智能体将在未指定 Agent 时自动调用。</span>
      </div>

      {/* ── Loading skeleton ── */}
      {loading && (
        <div className="admin-agent-list">
          {[1, 2, 3].map((i) => (
            <div key={i} className="admin-agent-card" style={{ opacity: 0.5 }}>
              <div className="skeleton skeleton-avatar" />
              <div style={{ flex: 1 }}>
                <div className="skeleton skeleton-text" style={{ width: '40%' }} />
                <div className="skeleton skeleton-text" style={{ width: '60%' }} />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Agent card list ── */}
      {!loading && (
        <div className="admin-agent-list">
          {agents.map((agent) => (
            <div
              key={agent.agentId}
              className={`admin-agent-card${agent.isDefault ? ' default' : ''}`}
            >
              {/* Avatar */}
              <div
                className="admin-agent-card-avatar"
                style={{ background: agentColor(agent.agentId) }}
              >
                {agentInitial(agent.displayName || agent.agentId)}
              </div>

              {/* Info */}
              <div className="admin-agent-card-info">
                <div className="admin-agent-card-name">
                  {agent.displayName || agent.agentId}
                </div>
                <div className="admin-agent-card-desc">
                  {agent.description || `${agent.domain} · ${agent.adapterType}`}
                </div>
                <div className="admin-agent-card-meta">
                  <span>状态: {agent.status}</span>
                  <span>·</span>
                  <span>风险等级: {agent.riskLevel || 'L1'}</span>
                  {agent.version && (
                    <>
                      <span>·</span>
                      <span>v{agent.version}</span>
                    </>
                  )}
                </div>
                {agent.isDefault && (
                  <div style={{ marginTop: 4 }}>
                    <span className="admin-agent-card-badge default">默认对话模型</span>
                  </div>
                )}
              </div>

              {/* Actions */}
              <div className="admin-agent-card-actions">
                <button
                  className={`admin-agent-card-action primary${agent.isDefault ? '' : ''}`}
                  title="设为默认对话模型"
                  onClick={() => handleSetDefault(agent.agentId)}
                  style={agent.isDefault ? { color: 'rgb(var(--primary-500))' } : undefined}
                >
                  {StarIcon}
                </button>
                <button
                  className="admin-agent-card-action"
                  title="编辑"
                  onClick={() => setNotice(`编辑智能体: ${agent.agentId}`)}
                >
                  {EditIcon}
                </button>
                <button
                  className="admin-agent-card-action"
                  title="测试"
                  onClick={() => handleTest(agent.agentId)}
                >
                  {PlayIcon}
                </button>
                <button
                  className="admin-agent-card-action"
                  title="版本历史"
                  onClick={() => setNotice(`查看版本历史: ${agent.agentId}`)}
                >
                  {HistoryIcon}
                </button>
                <button
                  className="admin-agent-card-action danger"
                  title="删除"
                  onClick={() => handleDelete(agent.agentId)}
                >
                  {TrashIcon}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Empty state ── */}
      {!loading && agents.length === 0 && (
        <div className="admin-placeholder">
          <div className="admin-placeholder-icon">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="18" height="14" rx="3" />
              <circle cx="9" cy="10" r="1.5" fill="currentColor" />
              <circle cx="15" cy="10" r="1.5" fill="currentColor" />
              <path d="M9 15c1.5 1.5 4.5 1.5 6 0" />
            </svg>
          </div>
          <h3 className="admin-placeholder-title">暂无智能体</h3>
          <p className="admin-placeholder-desc">点击上方「添加智能体」按钮创建第一个智能体</p>
        </div>
      )}
    </div>
  );
}

// ── Mock data for development / offline ─────────────────────────────
const MOCK_AGENTS: Agent[] = [
  {
    agentId: 'Orchestrator',
    displayName: 'Orchestrator',
    domain: 'orchestration',
    status: 'active',
    adapterType: 'openai',
    riskLevel: 'L2',
    description: '任务编排与调度中心，负责多智能体协作的任务分解与执行协调',
    isDefault: true,
    version: '2.4.1',
    model: 'claude-opus-4-8',
  },
  {
    agentId: 'Architect',
    displayName: 'Architect',
    domain: 'architecture',
    status: 'active',
    adapterType: 'openai',
    riskLevel: 'L1',
    description: '系统架构设计师，负责技术方案设计与架构评审',
    version: '1.8.0',
    model: 'claude-sonnet-5',
  },
  {
    agentId: 'CodeGen',
    displayName: 'CodeGen',
    domain: 'code-generation',
    status: 'active',
    adapterType: 'openai',
    riskLevel: 'L2',
    description: '代码生成与重构专家，支持多语言代码编写与优化',
    version: '3.2.1',
    model: 'claude-sonnet-5',
  },
  {
    agentId: 'Review',
    displayName: 'Review',
    domain: 'code-review',
    status: 'idle',
    adapterType: 'openai',
    riskLevel: 'L1',
    description: '代码审查专家，提供安全性、性能与最佳实践审查',
    version: '2.1.0',
    model: 'claude-haiku-4-5',
  },
  {
    agentId: 'Test',
    displayName: 'Test',
    domain: 'testing',
    status: 'idle',
    adapterType: 'openai',
    riskLevel: 'L1',
    description: '测试工程师，自动生成测试用例与执行测试验证',
    version: '1.5.3',
    model: 'claude-haiku-4-5',
  },
  {
    agentId: 'Deploy',
    displayName: 'Deploy',
    domain: 'deployment',
    status: 'sleeping',
    adapterType: 'openai',
    riskLevel: 'L3',
    description: '部署运维专家，负责应用部署、环境配置与发布管理',
    version: '2.0.2',
    model: 'claude-sonnet-5',
  },
  {
    agentId: 'Security',
    displayName: 'Security',
    domain: 'security',
    status: 'active',
    adapterType: 'openai',
    riskLevel: 'L3',
    description: '安全审计专家，负责代码安全扫描与漏洞检测',
    version: '1.2.0',
    model: 'claude-opus-4-8',
  },
];
