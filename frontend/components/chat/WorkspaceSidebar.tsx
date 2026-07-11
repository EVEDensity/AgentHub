import { memo, useState, useMemo } from 'react';
import type { ChatSession, Agent, User } from '../../types';
import MemberList from '../collaboration/MemberList';

/* ─────────────────────────────────────────────
   Type definitions
   ───────────────────────────────────────────── */

interface WorkspaceSidebarProps {
  /* ── Global Nav props ── */
  user: User | null;
  connected: boolean;
  agents: Agent[];
  onNavigate: (target: string) => void;
  onLogout: () => void;

  /* ── Session Sidebar props ── */
  filteredSessions: ChatSession[];
  sessionId: string;
  sessionQuery: string;
  editingId: string;
  editName: string;
  notice: string;
  isAutoNaming: boolean;
  sessionsLength: number;
  width?: number;
  onCreateSession: () => void;
  onSelectSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onRenameSession: (id: string) => void;
  onTogglePin: (id: string, current: number) => void;
  onStartRename: (s: ChatSession) => void;
  onRegenerateName: (id: string) => void;
  onSessionQueryChange: (q: string) => void;
  onEditNameChange: (name: string) => void;
  onEditNameKeyDown: (e: React.KeyboardEvent<HTMLInputElement>, id: string) => void;
  onEditNameBlur: (id: string) => void;
  onOpenShare?: () => void;
  currentRole?: string;
  currentVisibility?: string;
  authHeaders?: Record<string, string>;
}

/* ─────────────────────────────────────────────
   Constants
   ───────────────────────────────────────────── */

const NAV_ITEMS = [
  { id: 'admin', label: '管理面板', desc: '仪表盘、数据统计、智能体资源' },
  { id: 'canvas', label: '智能体画布', desc: '拖拽搭建多智能体工作流' },
  { id: 'memory', label: '记忆管理', desc: '全局向量库、长期记忆检索' },
  { id: 'tasks', label: '任务中心', desc: '批量任务调度与监控' },
] as const;

/* Icon paths for nav items — inline SVGs avoid Material Icons font dependency */
const NAV_ICON_PATHS: Record<string, string> = {
  admin: 'M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z',
  canvas: 'M22 9V7h-2V5a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-2h2v-2h-2v-2h2v-2h-2V9h2zm-4 10H4V5h14v14z',
  memory: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z',
  tasks: 'M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-7 14l-5-5 1.41-1.41L12 14.17l4.59-4.58L18 11l-6 6z',
};

type FilterTag = 'all' | 'recent' | 'archived' | 'multi' | 'single';

interface FilterOption {
  key: FilterTag;
  label: string;
}

const FILTER_OPTIONS: FilterOption[] = [
  { key: 'all', label: '全部' },
  { key: 'recent', label: '最近30天' },
  { key: 'multi', label: '多智能体' },
  { key: 'single', label: '单人' },
  { key: 'archived', label: '归档' },
];

const GENERIC_NAME_PATTERNS = ['untitled session', 'new session', '新建会话', '默认会话'];

/* ─────────────────────────────────────────────
   Helpers
   ───────────────────────────────────────────── */

function looksGeneric(name: string): boolean {
  const lower = (name || '').trim().toLowerCase();
  if (!lower) return true;
  return GENERIC_NAME_PATTERNS.some((p) => lower.startsWith(p));
}

function isMultiAgent(s: ChatSession): boolean {
  return (s.memberCount || 0) > 1;
}

function getTimeAgo(dateStr?: string): string {
  if (!dateStr) return '';
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return '刚刚';
  if (mins < 60) return `${mins}分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}天前`;
  return new Date(dateStr).toLocaleDateString('zh-CN');
}

/* ─────────────────────────────────────────────
   Component
   ───────────────────────────────────────────── */

const WorkspaceSidebar = memo(function WorkspaceSidebar(props: WorkspaceSidebarProps) {
  const {
    user, connected, agents, onNavigate, onLogout,
    filteredSessions, sessionId, sessionQuery, editingId, editName, notice,
    isAutoNaming, sessionsLength, onCreateSession, onSelectSession, onDeleteSession,
    onRenameSession, onTogglePin, onStartRename, onRegenerateName,
    onSessionQueryChange, onEditNameChange,
    onEditNameKeyDown, onEditNameBlur,
    onOpenShare, currentRole, currentVisibility, authHeaders, width,
  } = props;

  /* ── Global Nav state ── */
  const [activeNav, setActiveNav] = useState('admin');

  /* ── Session filter state ── */
  const [activeFilter, setActiveFilter] = useState<FilterTag>('recent');
  const [showArchived, setShowArchived] = useState(false);

  const onlineAgents = agents.filter((a) => a.status === 'active' || a.status === 'idle');
  const memberCount = onlineAgents.length + 1;

  /* ── Group sessions ── */
  const { multiSessions, singleSessions, archivedSessions } = useMemo(() => {
    const multi: ChatSession[] = [];
    const single: ChatSession[] = [];
    const archived: ChatSession[] = [];
    for (const s of filteredSessions) {
      if (s.archived) archived.push(s);
      else if (isMultiAgent(s)) multi.push(s);
      else single.push(s);
    }
    return { multiSessions: multi, singleSessions: single, archivedSessions: archived };
  }, [filteredSessions]);

  /* ── Session item renderer ── */
  const renderSessionItem = (s: ChatSession) => {
    const isActive = s.id === sessionId;
    const multi = isMultiAgent(s);
    const timeAgo = getTimeAgo(s.lastMessageAt || s.createdAt);

    return (
      <div
        key={s.id}
        className={`session-card group ${isActive ? 'active' : ''}`}
        onClick={() => onSelectSession(s.id)}
      >
        <div className="session-card-top">
          {editingId === s.id ? (
            <input
              className="session-card-edit-input"
              value={editName}
              onChange={(e) => onEditNameChange(e.target.value)}
              onKeyDown={(e) => onEditNameKeyDown(e, s.id)}
              onBlur={() => onEditNameBlur(s.id)}
              autoFocus
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <>
              <div className="session-card-name-row">
                {s.isPinned ? (
                  <svg className="session-card-pin-icon" viewBox="0 0 24 24" fill="currentColor" stroke="none">
                    <path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z" />
                  </svg>
                ) : null}
                <span className="session-card-name">{s.name || '未命名会话'}</span>
                {s.id === sessionId && isAutoNaming && (
                  <span className="session-card-naming-dot" />
                )}
              </div>
              <div className="session-card-tags">
                <span className={`session-card-type-tag ${multi ? 'multi' : 'single'}`}>
                  {multi ? `${s.memberCount || 0} 人协同` : '单人'}
                </span>
                {s.unreadCount ? (
                  <span className="session-card-unread">{s.unreadCount}</span>
                ) : null}
              </div>
            </>
          )}
        </div>

        {!editingId && (
          <div className="session-card-meta">
            <span className="session-card-preview">{s.lastMessage || '暂无消息'}</span>
            {timeAgo && <span className="session-card-time">{timeAgo}</span>}
          </div>
        )}

        {!editingId && (
          <div className="session-card-actions">
            <button className="session-card-action-btn" title={s.isPinned ? '取消置顶' : '置顶'}
              onClick={(e) => { e.stopPropagation(); onTogglePin(s.id, s.isPinned || 0); }}>
              <svg viewBox="0 0 24 24" className="session-card-action-icon" fill={s.isPinned ? 'currentColor' : 'none'}
                stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2l3 7h5l-4 6 1 7-5-3-5 3 1-7-4-6h5z" />
              </svg>
            </button>
            <button className="session-card-action-btn" title="重命名"
              onClick={(e) => { e.stopPropagation(); onStartRename(s); }}>
              <svg viewBox="0 0 24 24" className="session-card-action-icon" fill="none"
                stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />
              </svg>
            </button>
            {looksGeneric(s.name) && (
              <button className="session-card-action-btn" title="AI 生成名称"
                onClick={(e) => { e.stopPropagation(); onRegenerateName(s.id); }}>
                <svg viewBox="0 0 24 24" className="session-card-action-icon" fill="none"
                  stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="1 4 1 10 7 10" />
                  <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
                </svg>
              </button>
            )}
            <button className="session-card-action-btn danger" title="删除"
              onClick={(e) => { e.stopPropagation(); onDeleteSession(s.id); }}>
              <svg viewBox="0 0 24 24" className="session-card-action-icon" fill="none"
                stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" />
              </svg>
            </button>
          </div>
        )}
      </div>
    );
  };

  /* ───────────────────────────────────────────
     Render: single unified sidebar column
     ─────────────────────────────────────────── */

  return (
    <aside className="workspace-sidebar" style={width ? { width: `${width}px` } : undefined}>
      {/* ═══════════════════════════════════════
          Section 1: Brand Header
          ═══════════════════════════════════════ */}
      <div className="ws-brand">
        <div className="ws-brand-logo">
          <img src="/logo.png" alt="AgentHub" className="w-full h-full object-contain" />
        </div>
        <div className="ws-brand-info">
          <span className="ws-brand-name">AgentHub</span>
          <span className="ws-brand-user">{user?.name || 'admin'} · {user?.role || 'admin'}</span>
        </div>
        <button className="ws-brand-settings" title="设置">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
      </div>

      {/* ═══════════════════════════════════════
          Section 2: System Navigation
          ═══════════════════════════════════════ */}
      <div className="ws-nav-section">
        <div className="ws-nav-label">系统工作台</div>
        <div className="ws-nav-items">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              className={`ws-nav-item ${activeNav === item.id ? 'active' : ''}`}
              onClick={() => { setActiveNav(item.id); onNavigate(item.id); }}
              title={item.desc}
            >
              <svg className="ws-nav-icon" viewBox="0 0 24 24" fill="currentColor">
                <path d={NAV_ICON_PATHS[item.id]} />
              </svg>
              <span className="ws-nav-text">{item.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* ═══════════════════════════════════════
          Section 3: Status Bar
          ═══════════════════════════════════════ */}
      <div className="ws-status">
        <div className="ws-status-item">
          <span className={`ws-status-dot ${connected ? 'online' : 'offline'}`} />
          <span className="ws-status-text">{connected ? '实时连接正常' : '连接断开'}</span>
        </div>
        <div className="ws-status-item">
          <span className="ws-status-dot online" />
          <span className="ws-status-text">在线成员 {memberCount}</span>
        </div>
        <div className="ws-status-item">
          <span className="ws-status-dot online" />
          <span className="ws-status-text">在线智能体 {onlineAgents.length}</span>
        </div>
      </div>

      {/* ═══════════════════════════════════════
          Section 4: Online Members
          ═══════════════════════════════════════ */}
      <div className="ws-members">
        <div className="ws-members-label">
          在线成员 <span className="ws-members-count">{memberCount}</span>
        </div>
        <div className="ws-members-list">
          <div className="ws-member-item">
            <div className="ws-member-avatar" style={{ background: 'hsl(210, 45%, 40%)' }}>
              {(user?.name || 'A')[0].toUpperCase()}
            </div>
            <div className="ws-member-info">
              <span className="ws-member-name">{user?.name || 'admin'}</span>
              <span className="ws-member-role">你</span>
            </div>
            <span className="ws-member-dot" />
          </div>
          {onlineAgents.slice(0, 5).map((agent) => {
            const hue = (agent.agentId || 'A').split('').reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
            return (
              <div key={agent.agentId} className="ws-member-item">
                <div className="ws-member-avatar" style={{ background: `hsl(${hue}, 50%, 42%)` }}>
                  {agent.agentId[0].toUpperCase()}
                </div>
                <div className="ws-member-info">
                  <span className="ws-member-name">{agent.displayName || agent.agentId}</span>
                  <span className="ws-member-role">{agent.domain || '智能体'}</span>
                </div>
                <span className="ws-member-dot" />
              </div>
            );
          })}
        </div>
      </div>

      {/* ═══════════════════════════════════════
          Section 5: Divider
          ═══════════════════════════════════════ */}
      <div className="ws-section-divider" />

      {/* ═══════════════════════════════════════
          Section 6: Session Create + Search + Filters
          ═══════════════════════════════════════ */}
      <button className="ws-create-btn" onClick={onCreateSession}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
        <span>新建协同会话</span>
      </button>

      <div className="ws-search">
        <svg className="ws-search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input className="ws-search-input" placeholder="搜索会话..." value={sessionQuery}
          onChange={(e) => onSessionQueryChange(e.target.value)} />
        {sessionQuery && (
          <button className="ws-search-clear" onClick={() => onSessionQueryChange('')}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        )}
      </div>

      <div className="ws-filters">
        {FILTER_OPTIONS.map((opt) => (
          <button key={opt.key} className={`ws-filter-tag ${activeFilter === opt.key ? 'active' : ''}`}
            onClick={() => setActiveFilter(opt.key)}>{opt.label}</button>
        ))}
      </div>

      {/* Notice */}
      {notice && <div className="ws-notice">{notice}</div>}

      {/* ═══════════════════════════════════════
          Section 7: Session List (scrollable)
          ═══════════════════════════════════════ */}
      <div className="ws-session-list">
        {multiSessions.length > 0 && (
          <div className="ws-session-group">
            <div className="ws-session-group-header">
              <svg className="ws-session-group-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" />
              </svg>
              <span>多智能体协同会话</span>
              <span className="ws-session-group-count">{multiSessions.length}</span>
            </div>
            {multiSessions.map(renderSessionItem)}
          </div>
        )}

        {singleSessions.length > 0 && (
          <div className="ws-session-group">
            <div className="ws-session-group-header">
              <svg className="ws-session-group-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" />
              </svg>
              <span>单人会话</span>
              <span className="ws-session-group-count">{singleSessions.length}</span>
            </div>
            {singleSessions.map(renderSessionItem)}
          </div>
        )}

        {archivedSessions.length > 0 && (
          <div className="ws-session-group">
            <button className="ws-session-group-header ws-archive-toggle" onClick={() => setShowArchived(!showArchived)}>
              <svg className="ws-session-group-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="21 8 21 21 3 21 3 8" /><rect x="1" y="3" width="22" height="5" /><line x1="10" y1="12" x2="14" y2="12" />
              </svg>
              <span>归档会话</span>
              <span className="ws-session-group-count">{archivedSessions.length}</span>
              <svg className={`ws-archive-chevron ${showArchived ? 'open' : ''}`} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </button>
            {showArchived && archivedSessions.map(renderSessionItem)}
          </div>
        )}

        {sessionsLength === 0 && (
          <div className="ws-session-empty">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.3 }}>
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            <p>暂无会话</p>
            <span>点击上方按钮创建新会话</span>
          </div>
        )}
      </div>

      {/* ═══════════════════════════════════════
          Section 8: MemberList (if session active)
          ═══════════════════════════════════════ */}
      {sessionId && authHeaders && (
        <MemberList
          sessionId={sessionId}
          userRole={currentRole}
          visibility={currentVisibility}
          authHeaders={authHeaders}
          onOpenShare={onOpenShare}
        />
      )}

      {/* ═══════════════════════════════════════
          Section 9: Logout Footer
          ═══════════════════════════════════════ */}
      <div className="ws-footer">
        <button className="ws-footer-btn" onClick={onLogout} title="退出登录">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
            <polyline points="16 17 21 12 16 7" />
            <line x1="21" y1="12" x2="9" y2="12" />
          </svg>
          <span>退出登录</span>
        </button>
      </div>
    </aside>
  );
});

export default WorkspaceSidebar;
