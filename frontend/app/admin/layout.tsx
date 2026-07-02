'use client';

import { type JSX, type ReactNode } from 'react';
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

  function handleMenuClick(item: MenuItem): void {
    setActiveMenu(item);
    router.push(`/admin?menu=${encodeURIComponent(item)}`);
  }

  return (
    <div className="flex h-screen overflow-hidden bg-warm-50 text-warm-800">
      {/* ═══════════════════════════════════════════════════════════════
          Sidebar — dark themed, editorial-grade navigation
          ═══════════════════════════════════════════════════════════════ */}
      <aside className="h-screen w-[248px] flex-none flex flex-col overflow-hidden bg-warm-900 border-r border-warm-800/50 select-none">
        {/* ── Brand header ─────────────────────────────────────────── */}
        <div className="h-[60px] shrink-0 flex items-center gap-2.5 px-5 border-b border-warm-800/30">
          <span className="text-xl leading-none" aria-hidden="true">⚡</span>
          <span className="text-[17px] font-bold text-white tracking-tight leading-none">
            Agent<span className="text-primary-400">Hub</span>
          </span>
        </div>

        {/* ── Navigation ────────────────────────────────────────────── */}
        <nav className="flex-1 overflow-y-auto py-5 px-3 space-y-6">
          {MENU_GROUPS.map((group) => {
            const groupItems = MENU_META.filter((m) => m.group === group.key);
            if (groupItems.length === 0) return null;
            return (
              <div key={group.key}>
                {/* Section label */}
                <div className="px-3 mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-warm-500/60 select-none">
                  {group.label}
                </div>

                {/* Menu items */}
                <div className="space-y-0.5">
                  {groupItems.map((item) => {
                    const isActive = activeMenu === item.key;
                    return (
                      <button
                        key={item.key}
                        onClick={() => handleMenuClick(item.key)}
                        className={`
                          group w-full flex items-center gap-3 px-3 py-2.5
                          text-left text-[13px] leading-none
                          rounded-lg
                          border-l-[3px]
                          transition-all duration-200 ease-out
                          outline-none
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
                        <span className="truncate">{item.key}</span>

                        {/* Active indicator glow */}
                        {isActive && (
                          <span
                            className="ml-auto w-1 h-1 rounded-full bg-primary-400 shadow-[0_0_6px_rgba(123,158,251,0.6)] shrink-0"
                            aria-hidden="true"
                          />
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </nav>

        {/* ── User footer ───────────────────────────────────────────── */}
        <div className="shrink-0 border-t border-warm-800/30 px-4 py-3.5">
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
        </div>
      </aside>

      {/* ═══════════════════════════════════════════════════════════════
          Right area — glass header + scrollable content
          ═══════════════════════════════════════════════════════════════ */}
      <div className="min-w-0 flex-1 flex flex-col overflow-hidden">
        {/* Header — glass morphism */}
        <header className="border-b border-warm-150 bg-white/80 backdrop-blur-sm px-8 py-4 shrink-0">
          <div className="mx-auto flex max-w-7xl items-center justify-between">
            <div className="flex items-center gap-3 min-w-0">
              <h1 className="text-h2 text-warm-900 shrink-0">管理控制台</h1>
              <div className="w-px h-5 bg-warm-200" />
              <WorkspaceSelector />
              <span className="hidden sm:inline-flex tag tag-warm">{user?.name} · {user?.role}</span>
              <span className="tag tag-blue shrink-0">{activeMenu}</span>
            </div>
            <a className="btn-secondary shrink-0 transition-all active:scale-[0.98]" href="/">← 返回 IM</a>
          </div>
        </header>

        {/* Content */}
        <main className="flex-1 overflow-hidden min-h-0 px-8 py-8">
          <div className="mx-auto max-w-7xl h-full flex flex-col space-y-6 overflow-y-auto">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
