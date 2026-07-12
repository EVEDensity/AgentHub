import { memo, useState } from 'react';
import type { Agent } from '../../types';

interface AgentCollaborationPanelProps {
  agents: Agent[];
  sessionId: string;
  onAskAgent?: (agentId: string) => void;
  onPauseAgent?: (agentId: string) => void;
  onResetMemory?: (agentId: string) => void;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

const AGENT_COLORS = ['#6366f1', '#f59e0b', '#22c55e', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899'];

function getAgentColor(agentId: string): string {
  const idx = (agentId || 'A').split('').reduce((a, c) => a + c.charCodeAt(0), 0) % AGENT_COLORS.length;
  return AGENT_COLORS[idx];
}

const AgentCollaborationPanel = memo(function AgentCollaborationPanel({
  agents,
  sessionId,
  onAskAgent,
  onPauseAgent,
  onResetMemory,
  collapsed = false,
  onToggleCollapse,
}: AgentCollaborationPanelProps) {
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);

  const activeAgents = agents.filter((a) => a.status === 'active' || a.status === 'idle');
  const sleepingAgents = agents.filter((a) => a.status === 'sleeping' || a.status === 'offline');

  if (collapsed) {
    return (
      <aside className="agent-panel collapsed">
        <button className="agent-panel-expand-btn" onClick={onToggleCollapse} title="展开智能体面板">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
        <div className="agent-panel-collapsed-dots">
          {activeAgents.slice(0, 5).map((a) => (
            <div
              key={a.agentId}
              className="agent-panel-mini-dot"
              style={{ background: getAgentColor(a.agentId) }}
              title={a.displayName || a.agentId}
            />
          ))}
        </div>
      </aside>
    );
  }

  return (
    <aside className="agent-panel">
      {/* Header */}
      <div className="agent-panel-header">
        <div className="agent-panel-title-row">
          <h3 className="agent-panel-title">智能体团队</h3>
          <span className="agent-panel-count">{activeAgents.length} 在线</span>
        </div>
        <div className="agent-panel-header-actions">
          <button className="agent-panel-header-btn" title="添加智能体">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
          <button className="agent-panel-header-btn" onClick={onToggleCollapse} title="折叠面板">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>
        </div>
      </div>

      {/* Active agents */}
      <div className="agent-panel-body">
        {activeAgents.length === 0 && sleepingAgents.length === 0 && (
          <div className="agent-panel-empty">
            <span className="material-symbols-outlined" style={{ fontSize: 32, opacity: 0.3 }}>smart_toy</span>
            <p>暂无智能体</p>
            <button className="btn-secondary" style={{ padding: '6px 14px', fontSize: 12 }}>+ 添加智能体</button>
          </div>
        )}

        {activeAgents.map((agent) => {
          const color = getAgentColor(agent.agentId);
          const isExpanded = expandedAgent === agent.agentId;
          return (
            <div key={agent.agentId} className={`agent-card ${isExpanded ? 'expanded' : ''}`}>
              <div
                className="agent-card-main"
                onClick={() => setExpandedAgent(isExpanded ? null : agent.agentId)}
              >
                <div className="agent-card-avatar" style={{ background: color }}>
                  {agent.agentId[0].toUpperCase()}
                </div>
                <div className="agent-card-info">
                  <span className="agent-card-name">{agent.displayName || agent.agentId}</span>
                  <span className="agent-card-role">{agent.domain || 'Agent'}</span>
                </div>
                <span className="agent-card-status online" title="在线" />
              </div>

              {isExpanded && (
                <div className="agent-card-actions">
                  <button
                    className="agent-card-action-btn"
                    onClick={(e) => { e.stopPropagation(); onAskAgent?.(agent.agentId); }}
                    title={`向 ${agent.agentId} 提问`}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                    </svg>
                    <span>单独提问</span>
                  </button>
                  <button
                    className="agent-card-action-btn"
                    onClick={(e) => { e.stopPropagation(); onPauseAgent?.(agent.agentId); }}
                    title={`暂停 ${agent.agentId}`}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                      <rect x="6" y="4" width="4" height="16" rx="1" />
                      <rect x="14" y="4" width="4" height="16" rx="1" />
                    </svg>
                    <span>暂停</span>
                  </button>
                  <button
                    className="agent-card-action-btn"
                    onClick={(e) => { e.stopPropagation(); onResetMemory?.(agent.agentId); }}
                    title={`重置 ${agent.agentId} 记忆`}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                      <polyline points="1 4 1 10 7 10" />
                      <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
                    </svg>
                    <span>重置记忆</span>
                  </button>
                </div>
              )}
            </div>
          );
        })}

        {/* Sleeping agents */}
        {sleepingAgents.length > 0 && (
          <>
            <div className="agent-panel-section-label">
              休眠中
              <span className="agent-panel-section-count">{sleepingAgents.length}</span>
            </div>
            {sleepingAgents.map((agent) => {
              const color = getAgentColor(agent.agentId);
              return (
                <div key={agent.agentId} className="agent-card sleeping">
                  <div className="agent-card-main">
                    <div className="agent-card-avatar" style={{ background: color, opacity: 0.5 }}>
                      {agent.agentId[0].toUpperCase()}
                    </div>
                    <div className="agent-card-info">
                      <span className="agent-card-name">{agent.displayName || agent.agentId}</span>
                      <span className="agent-card-role">{agent.domain || 'Agent'}</span>
                    </div>
                    <span className="agent-card-status offline" title="离线" />
                  </div>
                </div>
              );
            })}
          </>
        )}
      </div>
    </aside>
  );
});

export default AgentCollaborationPanel;
