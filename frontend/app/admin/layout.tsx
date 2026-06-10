'use client';

import { type JSX, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '../../stores/authStore';
import { useAdminStore, SETTINGS_MENU, type MenuItem } from '../../stores/adminStore';

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
      {/* ── Sidebar — dark themed for visual separation ────────── */}
      <aside className="h-screen w-[240px] flex-none overflow-y-auto bg-warm-900 border-r border-warm-800">
        <div className="sticky top-0 z-10 h-[72px] border-b border-warm-800/60 bg-warm-900/95 backdrop-blur-sm px-5 flex items-center gap-2">
          <span className="text-lg">⚡</span>
          <span className="text-lg font-bold text-white tracking-tight">Agent<span className="text-primary-400">Hub</span></span>
        </div>
        <nav className="py-4 px-3 space-y-0.5">
          {SETTINGS_MENU.map((item) => (
            <button
              key={item}
              className={`block w-full px-4 py-2.5 text-left text-sm rounded-lg transition-all ${
                activeMenu === item
                  ? 'bg-primary-500/15 text-primary-300 font-medium shadow-sm'
                  : 'text-warm-400 hover:bg-white/5 hover:text-warm-200'
              }`}
              onClick={() => handleMenuClick(item)}
            >
              {item}
            </button>
          ))}
        </nav>
      </aside>

      {/* ── Right area ───────────────────────────────────────────── */}
      <div className="min-w-0 flex-1 flex flex-col overflow-hidden">
        {/* Header — glass morphism */}
        <header className="border-b border-warm-150 bg-white/80 backdrop-blur-sm px-8 py-4 shrink-0">
          <div className="mx-auto flex max-w-7xl items-center justify-between">
            <div className="flex items-center gap-3">
              <h1 className="text-h2 text-warm-900">管理控制台</h1>
              <span className="tag tag-warm">{user?.name} · {user?.role}</span>
              <span className="tag tag-blue">{activeMenu}</span>
            </div>
            <a className="btn-secondary transition-all active:scale-[0.98]" href="/">← 返回 IM</a>
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
