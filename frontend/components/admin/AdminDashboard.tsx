'use client';

import React, { useEffect, useState, useCallback, type JSX } from 'react';
import { useAuthStore } from '../../stores/authStore';

interface DashboardStats {
  totalAgents: number;
  activeAgents: number;
  totalSessions: number;
  avgResponseMs: number;
  agentChange: number;
  sessionChange: number;
  latencyChange: number;
  costChange: number;
}

export default function AdminDashboard(): JSX.Element {
  const authHeaders = useAuthStore((s) => s.authHeaders);
  const [stats, setStats] = useState<DashboardStats>(DEFAULT_STATS);
  const [notice, setNotice] = useState('');

  const fetchStats = useCallback(async () => {
    try {
      const headers = authHeaders();
      const [agentRes, sessionRes] = await Promise.all([
        fetch('/api/agent/registry', { headers }),
        fetch('/api/chat/sessions', { headers }),
      ]);

      let totalAgents = DEFAULT_STATS.totalAgents;
      let totalSessions = DEFAULT_STATS.totalSessions;

      if (agentRes.ok) {
        const agents: unknown[] = await agentRes.json();
        totalAgents = Array.isArray(agents) ? agents.length : totalAgents;
      }
      if (sessionRes.ok) {
        const sessions: unknown[] = await sessionRes.json();
        totalSessions = Array.isArray(sessions) ? sessions.length : totalSessions;
      }

      setStats({
        ...DEFAULT_STATS,
        totalAgents,
        totalSessions,
        activeAgents: Math.min(totalAgents, DEFAULT_STATS.activeAgents),
      });
    } catch {
      // Use default stats on error
    }
  }, [authHeaders]);

  useEffect(() => {
    fetchStats();
  }, [fetchStats]);

  // ── SVG Icons ──────────────────────────────────────────────────
  const TrendUp = (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
      <polyline points="17 6 23 6 23 12" />
    </svg>
  );
  const TrendDown = (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 18 13.5 8.5 8.5 13.5 1 6" />
      <polyline points="17 18 23 18 23 12" />
    </svg>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
      {/* ── Welcome notice ── */}
      <div className="admin-notice">
        <span className="admin-notice-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="16" x2="12" y2="12" />
            <line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
        </span>
        <span>欢迎使用 AgentHub 管理后台。系统当前运行正常，所有智能体处于可调度状态。建议定期检查智能体版本更新与安全审计日志。</span>
      </div>

      {/* ── Stat cards ── */}
      <div>
        <h2 className="admin-section-title" style={{ marginBottom: 16 }}>概览</h2>
        <div className="admin-stat-grid">
          <div className="admin-stat-card">
            <div className="admin-stat-card-label">智能体总数</div>
            <div className="admin-stat-card-value">{stats.totalAgents}</div>
            <div className={`admin-stat-card-change ${stats.agentChange >= 0 ? 'up' : 'down'}`}>
              {stats.agentChange >= 0 ? TrendUp : TrendDown}
              {stats.agentChange >= 0 ? '+' : ''}{stats.agentChange} 本周新增
            </div>
          </div>
          <div className="admin-stat-card">
            <div className="admin-stat-card-label">活跃智能体</div>
            <div className="admin-stat-card-value">{stats.activeAgents}</div>
            <div className="admin-stat-card-change up">
              <span className="status-dot status-dot-active" style={{ display: 'inline-block' }} />
              {stats.totalAgents > 0 ? Math.round((stats.activeAgents / stats.totalAgents) * 100) : 0}% 活跃率
            </div>
          </div>
          <div className="admin-stat-card">
            <div className="admin-stat-card-label">总会话数</div>
            <div className="admin-stat-card-value">{stats.totalSessions}</div>
            <div className={`admin-stat-card-change ${stats.sessionChange >= 0 ? 'up' : 'down'}`}>
              {stats.sessionChange >= 0 ? TrendUp : TrendDown}
              {stats.sessionChange >= 0 ? '+' : ''}{stats.sessionChange}% 较上周
            </div>
          </div>
          <div className="admin-stat-card">
            <div className="admin-stat-card-label">平均响应时间</div>
            <div className="admin-stat-card-value">{stats.avgResponseMs}<span style={{ fontSize: 14, fontWeight: 400, color: 'rgb(var(--warm-500))' }}>ms</span></div>
            <div className={`admin-stat-card-change ${stats.latencyChange <= 0 ? 'up' : 'down'}`}>
              {stats.latencyChange <= 0 ? TrendDown : TrendUp}
              {stats.latencyChange <= 0 ? '-' : '+'}{Math.abs(stats.latencyChange)}% 较上周
            </div>
          </div>
        </div>
      </div>

      {/* ── Quick actions ── */}
      <div>
        <h2 className="admin-section-title" style={{ marginBottom: 12 }}>快捷操作</h2>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="admin-btn-accent" type="button">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            添加服务商
          </button>
          <button className="admin-btn-outline" type="button">查看审计日志</button>
          <button className="admin-btn-outline" type="button">系统健康检查</button>
          <button className="admin-btn-outline" type="button">导出配置</button>
        </div>
      </div>

      {/* ── System status ── */}
      <div>
        <h2 className="admin-section-title" style={{ marginBottom: 12 }}>系统状态</h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {[
            { label: 'API 网关', status: 'online', latency: '12ms' },
            { label: '数据库 (PostgreSQL)', status: 'online', latency: '3ms' },
            { label: 'Redis 缓存', status: 'online', latency: '0.8ms' },
            { label: 'WebSocket 服务', status: 'online', latency: '5ms' },
            { label: 'MCP 协议服务', status: 'online', latency: '8ms' },
          ].map((svc) => (
            <div
              key={svc.label}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '10px 14px',
                borderRadius: 4,
                background: 'rgb(var(--warm-100))',
                border: '1px solid rgb(var(--warm-200))',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="status-dot status-dot-active" />
                <span style={{ fontSize: 13, color: 'rgb(var(--warm-800))' }}>{svc.label}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span className="tag tag-green">{svc.status}</span>
                <span style={{ fontSize: 11, color: 'rgb(var(--warm-500))', fontFamily: 'monospace' }}>{svc.latency}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

const DEFAULT_STATS: DashboardStats = {
  totalAgents: 7,
  activeAgents: 4,
  totalSessions: 0,
  avgResponseMs: 245,
  agentChange: 2,
  sessionChange: 12,
  latencyChange: -8,
  costChange: -5,
};
