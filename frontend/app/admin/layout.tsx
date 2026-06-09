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
      {/* ── Sidebar ─────────────────────────────────────────────── */}
      <aside className="h-screen w-[254px] flex-none overflow-y-auto border-r border-warm-150 bg-[#F3F2F0]">
        <div className="sticky top-0 z-10 h-20 border-b border-warm-150 bg-[#ECEBE8] px-5 flex items-center text-xl font-semibold text-warm-800">
          设置
        </div>
        <nav className="py-3">
          {SETTINGS_MENU.map((item) => (
            <button
              key={item}
              className={`block w-full px-5 py-3 text-left text-[34px] leading-none ${
                activeMenu === item
                  ? 'bg-[#ECEBE8] text-warm-900 font-medium'
                  : 'text-warm-700 hover:bg-[#ECEBE8]'
              }`}
              onClick={() => handleMenuClick(item)}
            >
              <span className="text-[32px] align-middle">{item}</span>
            </button>
          ))}
        </nav>
      </aside>

      {/* ── Right area ───────────────────────────────────────────── */}
      <div className="min-w-0 flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <header className="border-b border-warm-150 bg-white px-8 py-4 shrink-0">
          <div className="mx-auto flex max-w-7xl items-center justify-between">
            <div className="flex items-center gap-3">
              <h1 className="text-h2">管理控制台</h1>
              <span className="tag tag-warm">{user?.name}/{user?.role}</span>
              <span className="tag tag-blue">当前模块：{activeMenu}</span>
            </div>
            <a className="btn-secondary" href="/">返回 IM</a>
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
