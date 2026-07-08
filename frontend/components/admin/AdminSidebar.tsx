import React, { useCallback, type JSX } from 'react';
import { MENU_GROUPS, MENU_META, type MenuItem } from '../../stores/adminStore';

interface AdminSidebarProps {
  collapsed: boolean;
  activeMenu: MenuItem;
  user: { name?: string; role?: string } | null;
  onMenuClick: (item: MenuItem) => void;
  onToggle: () => void;
  isMobile?: boolean;
  onUserClick?: () => void;
}

/**
 * Left fixed vertical dark navigation bar — Claude Desktop warm dark aesthetic.
 *
 * RENDERING GUARANTEE: Wrapped in React.memo. Since all callback props come from
 * useCallback hooks with stable dependencies in the parent, and activeMenu only
 * changes on user interaction, this sidebar re-renders ONLY when a menu item is
 * clicked. Page scrolling, form input, and content panel transitions never
 * trigger a sidebar re-render.
 */
function AdminSidebarInner({
  collapsed,
  activeMenu,
  user,
  onMenuClick,
  onToggle,
  isMobile = false,
  onUserClick,
}: AdminSidebarProps): JSX.Element {
  const handleClick = useCallback(
    (item: MenuItem) => {
      onMenuClick(item);
    },
    [onMenuClick],
  );

  const sidebarWidth = collapsed ? 'w-[64px]' : 'w-[240px]';

  return (
    <>
      {/* ── Brand header ── */}
      <div
        className={`admin-sb-brand shrink-0 flex items-center border-b border-warm-200/20 ${
          collapsed ? 'justify-center h-[56px] px-2' : 'gap-2.5 h-[56px] px-5'
        }`}
      >
        {/* Logo mark — AH white on black */}
        <img
          src="/logo.png"
          alt="AgentHub"
          className="w-6 h-6 rounded-[6px] shrink-0 object-contain"
        />
        {!collapsed && (
          <span className="text-[16px] font-bold text-warm-900 tracking-tight leading-none whitespace-nowrap">
            Agent<span className="text-primary-500">Hub</span>
          </span>
        )}
        {isMobile && (
          <button
            className="ml-auto text-warm-400 hover:text-warm-700 transition-colors p-1 rounded"
            onClick={onToggle}
            aria-label="Close menu"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        )}
      </div>

      {/* ── Navigation ── */}
      <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-5 admin-sb-scroll">
        {MENU_GROUPS.map((group) => {
          const groupItems = MENU_META.filter((m) => m.group === group.key && m.key !== '用户管理');
          if (groupItems.length === 0) return null;
          return (
            <div key={group.key}>
              {/* Group label */}
              {!collapsed && (
                <div className="admin-sb-group-label">
                  {group.label}
                </div>
              )}

              <div className="space-y-0.5">
                {groupItems.map((item) => {
                  const isActive = activeMenu === item.key;
                  return (
                    <button
                      key={item.key}
                      type="button"
                      onClick={() => handleClick(item.key)}
                      title={collapsed ? item.key : undefined}
                      className={`admin-sb-item${isActive ? ' active' : ''}${collapsed ? ' collapsed' : ''}`}
                      aria-current={isActive ? 'page' : undefined}
                    >
                      <span className="admin-sb-item-icon" aria-hidden="true">
                        {item.icon}
                      </span>
                      {!collapsed && (
                        <span className="admin-sb-item-label truncate">{item.key}</span>
                      )}
                      {!collapsed && isActive && (
                        <span className="admin-sb-item-dot" aria-hidden="true" />
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </nav>

      {/* ── User footer ── */}
      <div
        className={`shrink-0 border-t border-warm-200/20 px-4 py-3.5 ${onUserClick ? 'cursor-pointer hover:bg-warm-100/50 transition-colors' : ''}`}
        onClick={onUserClick}
        title={onUserClick ? '用户管理' : undefined}
      >
        {collapsed ? (
          <div className="flex justify-center">
            <UserAvatar name={user?.name || ''} />
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <UserAvatar name={user?.name || ''} />
            <div className="min-w-0 flex-1">
              {user?.name ? (
                <>
                  <div className="text-[13px] font-medium text-warm-800 truncate leading-tight">
                    {user.name}
                  </div>
                  <div className="text-[11px] text-warm-500 flex items-center gap-1.5 mt-0.5 leading-tight">
                    <span className="admin-sb-status-dot" />
                    <span className="truncate">{user.role || 'User'}</span>
                  </div>
                </>
              ) : (
                <div className="space-y-1.5">
                  <div className="skeleton skeleton-text !h-3 !mb-0 w-20" />
                  <div className="skeleton skeleton-text !h-2.5 !mb-0 w-14" />
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

/** Tiny avatar circle — deterministic color from name hash */
function UserAvatar({ name }: { name: string }): JSX.Element {
  const initial = (name || '?')[0].toUpperCase();
  const hue = (name || 'A').split('').reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
  return (
    <div
      className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0"
      style={{ background: `hsl(${hue}, 45%, 40%)` }}
    >
      {initial}
    </div>
  );
}

/** Fully memoized — zero re-renders on content panel changes. */
const AdminSidebar = React.memo(AdminSidebarInner);
export default AdminSidebar;
