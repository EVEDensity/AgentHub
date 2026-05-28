import { memo } from 'react';
import type { ChatSession } from '../../types';

interface SessionSidebarProps {
  user: { name: string; role: string } | null;
  filteredSessions: ChatSession[];
  sessionId: string;
  sessionQuery: string;
  editingId: string;
  editName: string;
  notice: string;
  sessionsLength: number;
  onCreateSession: () => void;
  onSelectSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onRenameSession: (id: string) => void;
  onTogglePin: (id: string, current: number) => void;
  onStartRename: (s: ChatSession) => void;
  onSessionQueryChange: (q: string) => void;
  onEditNameChange: (name: string) => void;
  onEditNameKeyDown: (e: React.KeyboardEvent<HTMLInputElement>, id: string) => void;
  onEditNameBlur: (id: string) => void;
  onLogout: () => void;
}

const SessionSidebar = memo(function SessionSidebar({
  user, filteredSessions, sessionId, sessionQuery, editingId, editName, notice,
  sessionsLength, onCreateSession, onSelectSession, onDeleteSession, onRenameSession,
  onTogglePin, onStartRename, onSessionQueryChange, onEditNameChange,
  onEditNameKeyDown, onEditNameBlur, onLogout,
}: SessionSidebarProps) {
  return (
    <aside className="w-80 border-r border-warm-150 bg-white p-4 flex h-screen flex-col">
      <div className="mb-4">
        <div className="text-h2 text-warm-800">AgentHub</div>
        <div className="mt-1 text-caption text-warm-500">{user?.name} / {user?.role}</div>
      </div>
      <a className="btn-secondary block w-full text-center" href="/admin">管理面板</a>
      <a className="btn-secondary mt-2 block w-full text-center" href="/canvas">智能体画布</a>
      <button className="btn-ghost mt-2 w-full" onClick={onLogout}>退出登录</button>
      {notice && <div className="mt-3 rounded-lg bg-warning-50 p-2 text-xs text-warning-600">{notice}</div>}
      <div className="mb-3 mt-4 flex items-center justify-between border-b border-warm-150 pb-3">
        <button className="btn-ghost flex items-center gap-2" onClick={onCreateSession}><span className="text-lg">+</span><span>New Session</span></button>
      </div>
      <div className="mb-3 flex items-center gap-2 rounded-xl border border-warm-150 bg-warm-50 px-3 py-2">
        <span className="text-warm-400">Search</span>
        <input className="w-full bg-transparent text-sm outline-none" placeholder="Search sessions..." value={sessionQuery} onChange={(e) => onSessionQueryChange(e.target.value)} />
      </div>
      <div className="mb-2 text-xs text-warm-500">Recent 30 days</div>
      <div className="flex-1 overflow-hidden">
        <div className="h-full space-y-1 overflow-auto pr-1">
        {filteredSessions.map((s) => (
          <div key={s.id} className={`group flex items-center gap-1 rounded-lg px-2 py-1 ${s.id === sessionId ? 'bg-warm-100' : 'hover:bg-warm-50'}`}>
            {editingId === s.id ? (
              <input
                className="flex-1 rounded border border-primary-300 px-2 py-1 text-sm outline-none focus:ring-1 focus:ring-primary-500"
                value={editName}
                onChange={(e) => onEditNameChange(e.target.value)}
                onKeyDown={(e) => onEditNameKeyDown(e, s.id)}
                onBlur={() => onEditNameBlur(s.id)}
                autoFocus
              />
            ) : (
              <button className={`flex-1 rounded-lg px-2 py-2 text-left text-sm ${s.id === sessionId ? 'text-warm-800' : 'text-warm-600'}`} onClick={() => onSelectSession(s.id)}>
                <div className="flex items-center gap-1.5 truncate">
                  {s.isPinned ? <span className="shrink-0 text-amber-500" title="Pinned">📌</span> : null}
                  <span className="truncate">{s.name || 'Untitled'}</span>
                </div>
              </button>
            )}
            <button className="invisible rounded p-1 text-warm-400 transition hover:bg-white hover:text-amber-500 group-hover:visible" title="Pin session" onClick={() => onTogglePin(s.id, s.isPinned || 0)}>
              <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill={s.isPinned ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M12 2l3 7h5l-4 6 1 7-5-3-5 3 1-7-4-6h5z" />
              </svg>
            </button>
            <button className="invisible rounded p-1 text-warm-400 transition hover:bg-white hover:text-primary-500 group-hover:visible" title="Rename session" onClick={() => onStartRename(s)}>
              <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />
              </svg>
            </button>
            <button className="invisible rounded p-1 text-warm-400 transition hover:bg-white hover:text-danger-500 group-hover:visible" title="Delete session" onClick={() => onDeleteSession(s.id)}>
              <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M3 6h18" />
                <path d="M8 6V4h8v2" />
                <path d="M19 6l-1 14H6L5 6" />
                <path d="M10 11v6" />
                <path d="M14 11v6" />
              </svg>
            </button>
          </div>
        ))}
        {sessionsLength === 0 && <div className="rounded-lg bg-warm-50 px-3 py-2 text-sm text-warm-500">No sessions, click &quot;New Session&quot;</div>}
        </div>
      </div>
    </aside>
  );
});

export default SessionSidebar;
