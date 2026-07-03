'use client';

import { useState, useEffect, type JSX, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '../../stores/authStore';
import { useAdminStore, MENU_META, MENU_GROUPS, type MenuItem } from '../../stores/adminStore';
import { WorkspaceSelector } from '../../components/admin/WorkspaceSelector';

function UserAvatar({ name }: { name: string }): JSX.Element {
  const initial = (name || '?')[0].toUpperCase();
  const hue = (name || 'A').split('').reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
  return (
    <div
      className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0 shadow-sm"
      style={{ background: `linear-gradient(135deg, hsl(${hue}, 55%, 45%), hsl(${hue}, 60%, 35%))` }}
    >
      {initial}
    </div>
  );
}

export default function AdminLayout({ children }: { children: ReactNode }): JSX.Element {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const activeMenu = useAdminStore((s) => s.activeMenu);
  const setActiveMenu = useAdminStore((s) => s.setActiveMenu);

  // Responsive sidebar state
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);

  // Detect screen size for auto-collapse
  useEffect(() => {
    const handleResize = () => {
      const w = window.innerWidth;
      if (w < 1024) {
        setSidebarCollapsed(true);
      } else if (w >= 1280) {
        setSidebarCollapsed(false);
      }
      if (w >= 1024) {
        setMobileDrawerOpen(false);
      }
    };
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  function handleMenuClick(item: MenuItem): void {
    setActiveMenu(item);
    router.push(`/admin?menu=${encodeURIComponent(item)}`);
    // Close mobile drawer after selection
    if (window.innerWidth < 1024) {
      setMobileDrawerOpen(false);
    }
  }

  const sidebarWidth = sidebarCollapsed ? 'w-[64px]' : 'w-[248px]';

  return (
    <div className="flex h-screen overflow-hidden bg-warm-50 text-warm-800">
      {/* ══════════════════════════════════════════════════════
          Mobile Overlay (backdrop + drawer)
          ═══════════════════════════════════════════════════ */}
      {mobileDrawerOpen && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm lg:hidden"
            onClick={() => setMobileDrawerOpen(false)}
          />
          {/* Drawer */}
          <aside className="fixed left-0 top-0 z-50 h-screen w-[248px] flex flex-col overflow-hidden bg-warm-900 border-r border-warm-800/50 select-none lg:hidden animate-slide-in-left">
            <SidebarContent
              collapsed={false}
              activeMenu={activeMenu}
              user={user}
              onMenuClick={handleMenuClick}
              onToggle={() => setMobileDrawerOpen(false)}
              isMobile
            />
          </aside>
        </>
      )}

      {/* ══════════════════════════════════════════════════════
          Desktop Sidebar
          ═══════════════════════════════════════════════════ */}
      <aside
        className={`hidden lg:flex h-screen ${sidebarWidth} flex-none flex-col overflow-hidden bg-warm-900 border-r border-warm-800/50 select-none transition-all duration-300 ease-out`}
      >
        <SidebarContent
          collapsed={sidebarCollapsed}
          activeMenu={activeMenu}
          user={user}
          onMenuClick={handleMenuClick}
          onToggle={() => setSidebarCollapsed((v) => !v)}
        />
      </aside>

      {/* ══════════════════════════════════════════════════════
          Right area — glass header + scrollable content
          ═══════════════════════════════════════════════════ */}
      <div className="min-w-0 flex-1 flex flex-col overflow-hidden">
        {/* Header — glass morphism with mobile hamburger */}
        <header className="border-b border-warm-150 bg-white/80 backdrop-blur-sm px-4 md:px-6 lg:px-8 py-3 md:py-4 shrink-0">
          <div className="flex items-center justify-between gap-3 max-w-7xl">
            <div className="flex items-center gap-2 md:gap-3 min-w-0">
              {/* Mobile hamburger */}
              <button
                className="lg:hidden btn-icon shrink-0"
                onClick={() => setMobileDrawerOpen(true)}
                aria-label="Open menu"
              >
                <span className="material-symbols-outlined text-[20px]">menu</span>
              </button>

              {/* Desktop collapse toggle */}
              <button
                className="hidden lg:flex btn-icon shrink-0"
                onClick={() => setSidebarCollapsed((v) => !v)}
                aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
                title={sidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'}
              >
                <span className="material-symbols-outlined text-[18px]">
                  {sidebarCollapsed ? 'chevron_right' : 'chevron_left'}
                </span>
              </button>

              <h1 className="text-base md:text-h2 text-warm-900 shrink-0 hidden sm:block">管理控制台</h1>
              <div className="hidden md:block w-px h-5 bg-warm-200" />
              <div className="hidden md:block">
                <WorkspaceSelector />
              </div>
              <span className="hidden lg:inline-flex tag tag-blue shrink-0">{activeMenu}</span>
            </div>

            <div className="flex items-center gap-2 shrink-0">
              <span className="hidden sm:inline-flex tag tag-warm text-xs">{user?.name}</span>
              <a className="btn-secondary shrink-0 text-xs md:text-sm transition-all active:scale-[0.98]" href="/">← 返回</a>
            </div>
          </div>
        </header>

        {/* Content */}
        <main className="flex-1 overflow-hidden min-h-0 px-4 md:px-6 lg:px-8 py-4 md:py-8">
          <div className="mx-auto max-w-7xl h-full flex flex-col space-y-4 md:space-y-6 overflow-y-auto">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

/**
 * Sidebar content — shared between desktop and mobile
 */
function SidebarContent({
  collapsed,
  activeMenu,
  user,
  onMenuClick,
  onToggle,
  isMobile = false,
}: {
  collapsed: boolean;
  activeMenu: MenuItem;
  user: { name?: string; role?: string } | null;
  onMenuClick: (item: MenuItem) => void;
  onToggle: () => void;
  isMobile?: boolean;
}): JSX.Element {
  return (
    <>
      {/* Brand header */}
      <div className={`shrink-0 flex items-center border-b border-warm-800/30 ${collapsed ? 'justify-center h-[60px] px-2' : 'gap-2.5 h-[60px] px-5'}`}>
        <span className="text-xl leading-none shrink-0" aria-hidden="true">⚡</span>
        {!collapsed && (
          <span className="text-[17px] font-bold text-white tracking-tight leading-none">
            Agent<span className="text-primary-400">Hub</span>
          </span>
        )}
        {isMobile && (
          <button
            className="ml-auto btn-icon text-warm-400 hover:text-white"
            onClick={onToggle}
            aria-label="Close menu"
          >
            <span className="material-symbols-outlined text-[18px]">close</span>
          </button>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-5 px-3 space-y-6">
        {MENU_GROUPS.map((group) => {
          const groupItems = MENU_META.filter((m) => m.group === group.key);
          if (groupItems.length === 0) return null;
          return (
            <div key={group.key}>
              {!collapsed && (
                <div className="px-3 mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-warm-500/60 select-none">
                  {group.label}
                </div>
              )}

              <div className="space-y-0.5">
                {groupItems.map((item) => {
                  const isActive = activeMenu === item.key;
                  return (
                    <button
                      key={item.key}
                      onClick={() => onMenuClick(item.key)}
                      title={collapsed ? item.key : undefined}
                      className={`
                        group flex items-center
                        text-left text-[13px] leading-none
                        rounded-lg
                        border-l-[3px]
                        transition-all duration-200 ease-out
                        outline-none
                        ${collapsed ? 'justify-center px-2 py-3' : 'gap-3 px-3 py-2.5'}
                        ${isActive
                          ? 'bg-primary-500/10 text-primary-200 font-semibold border-l-primary-400 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.03)]'
                          : 'text-warm-400 hover:bg-white/[0.04] hover:text-warm-200 border-l-transparent hover:border-l-warm-600/40'
                        }
                      `}
                    >
                      <span
                        className={`text-[15px] leading-none shrink-0 transition-opacity duration-200 ${
                          isActive ? 'opacity-100' : 'opacity-60 group-hover:opacity-90'
                        }`}
                      >
                        {item.icon}
                      </span>
                      {!collapsed && (
                        <>
                          <span className="truncate">{item.key}</span>
                          {isActive && (
                            <span
                              className="ml-auto w-1 h-1 rounded-full bg-primary-400 shadow-[0_0_6px_rgba(123,158,251,0.6)] shrink-0"
                              aria-hidden="true"
                            />
                          )}
                        </>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </nav>

      {/* User footer */}
      <div className="shrink-0 border-t border-warm-800/30 px-4 py-3.5">
        {collapsed ? (
          <div className="flex justify-center">
            {user?.name ? (
              <UserAvatar name={user.name} />
            ) : (
              <div className="w-8 h-8 rounded-full bg-warm-700 animate-pulse shrink-0" />
            )}
          </div>
        ) : (
          <div className="flex items-center gap-3">
            {user?.name ? (
              <UserAvatar name={user.name} />
            ) : (
              <div className="w-8 h-8 rounded-full bg-warm-700 animate-pulse shrink-0" />
            )}
            <div className="min-w-0 flex-1">
              {user?.name ? (
                <>
                  <div className="text-[13px] font-medium text-warm-200 truncate leading-tight">
                    {user.name}
                  </div>
                  <div className="text-[11px] text-warm-500 flex items-center gap-1.5 mt-0.5 leading-tight">
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-success-500 shadow-[0_0_0_2px_rgba(91,140,90,0.2)] shrink-0" />
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
