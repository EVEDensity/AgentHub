'use client';

import { useState, useEffect, useCallback, useRef, type JSX, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '../../stores/authStore';
import { useAdminStore, type MenuItem } from '../../stores/adminStore';
import { useAgentStore } from '../../stores/agentStore';
import { WorkspaceSelector } from '../../components/admin/WorkspaceSelector';
import AdminSidebar from '../../components/admin/AdminSidebar';
import AdminActivityPanel, { type FrontendActivity } from '../../components/admin/AdminActivityPanel';
import CommandPalette from '../../components/admin/CommandPalette';
import AIChatDialog from '../../components/chat/AIChatDialog';
import ResizableDivider from '../../components/common/ResizableDivider';

/**
 * AdminLayout — AgentHub Warm Dark IDE Layout
 *
 * Structure: [Sidebar | ResizeHandle | Main+Header+Content | ResizeHandle | Console]
 * - Three-column CSS grid with resizable side panels
 * - Right panel for explicitly local browser navigation activity
 * - Cyan accent (#22A3C9) on dark (#121418 / #191C22)
 */
export default function AdminLayout({ children }: { children: ReactNode }): JSX.Element {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const activeMenu = useAdminStore((s) => s.activeMenu);
  const setActiveMenu = useAdminStore((s) => s.setActiveMenu);
  const defaultChatAgent = useAgentStore((s) => s.defaultChatAgent);

  // ── Panel state ────────────────────────────────────────────────────
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const [consoleCollapsed, setConsoleCollapsed] = useState(false);
  const [chatOpen, setChatOpen] = useState(true);

  useEffect(() => {
    try {
      const lastClosed = localStorage.getItem('agenthub_chat_closed_at');
      if (lastClosed) {
        const elapsed = Date.now() - parseInt(lastClosed, 10);
        if (elapsed < 24 * 60 * 60 * 1000) setChatOpen(false);
      }
    } catch { /* localStorage unavailable */ }
  }, []);
  const [activities, setActivities] = useState<FrontendActivity[]>([]);
  const activitySequence = useRef(0);

  const [sidebarWidth, setSidebarWidth] = useState(240);

  useEffect(() => {
    try {
      const stored = localStorage.getItem('agenthub_sidebar_w');
      if (stored) { const n = parseInt(stored, 10); if (n >= 240 && n <= 480) setSidebarWidth(n); }
    } catch { /* ignore */ }
  }, []);
  const [sidebarWidthLive, setSidebarWidthLive] = useState<number | null>(null);
  const sidebarW = sidebarCollapsed ? '64px' : `${sidebarWidthLive ?? sidebarWidth}px`;
  const consoleWidth = consoleCollapsed ? '0px' : '380px';
  const gridTemplateColumns = consoleCollapsed
    ? `${sidebarW} minmax(0, 1fr)`
    : `${sidebarW} minmax(0, 1fr) 4px var(--console-w, ${consoleWidth})`;

  // Auto-collapse on small screens
  useEffect(() => {
    const handleResize = () => {
      const w = window.innerWidth;
      if (w < 1440) {
        setSidebarCollapsed(true);
      } else if (w >= 1600) {
        setSidebarCollapsed(false);
      }
      if (w >= 1024) {
        setMobileDrawerOpen(false);
      }
      if (w < 1600) {
        setConsoleCollapsed(true);
      }
    };
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // Listen for toggle-sidebar event from CommandPalette / ChatDialog
  useEffect(() => {
    const handler = () => setSidebarCollapsed((v) => !v);
    window.addEventListener('toggle-sidebar', handler);
    return () => window.removeEventListener('toggle-sidebar', handler);
  }, []);

  // Ctrl+B keyboard shortcut for sidebar toggle
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'b') {
        e.preventDefault();
        setSidebarCollapsed((v) => !v);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  // ── Local navigation activity; no terminal or server transport ───────
  const pad2 = (n: number) => (n < 10 ? '0' : '') + n;
  const getTime = () => {
    const d = new Date();
    return `${d.getHours()}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  };

  const addNavigationActivity = useCallback((message: string) => {
    const activity = { id: ++activitySequence.current, time: getTime(), message };
    setActivities(previous => [...previous.slice(-199), activity]);
  }, []);

  // ── Stable callbacks ───────────────────────────────────────────────
  const handleMenuClick = useCallback(
    (item: MenuItem) => {
      setActiveMenu(item);
      router.push(`/admin?menu=${encodeURIComponent(item)}`);
      addNavigationActivity(`导航至: ${item}`);
      if (window.innerWidth < 1024) {
        setMobileDrawerOpen(false);
      }
    },
    [setActiveMenu, router, addNavigationActivity],
  );

  const handleToggleSidebar = useCallback(() => {
    setSidebarCollapsed((v) => !v);
  }, []);

  const handleCloseMobile = useCallback(() => {
    setMobileDrawerOpen(false);
  }, []);

  // ── Console resize handle ───────────────────────────────────────────
  const consoleResizeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const consoleHandle = consoleResizeRef.current;
    let isResizingC = false;

    const onMouseDownC = (e: MouseEvent) => {
      isResizingC = true;
      consoleHandle?.classList.add('active');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    };
    const onMouseMove = (e: MouseEvent) => {
      if (isResizingC) {
        const w = Math.max(260, Math.min(600, window.innerWidth - e.clientX));
        document.documentElement.style.setProperty('--console-w', w + 'px');
      }
    };
    const onMouseUp = () => {
      if (isResizingC) {
        isResizingC = false;
        consoleHandle?.classList.remove('active');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
    };

    consoleHandle?.addEventListener('mousedown', onMouseDownC);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);

    return () => {
      consoleHandle?.removeEventListener('mousedown', onMouseDownC);
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };
  }, []);

  return (
    <div
      className="admin-layout-root"
      style={{
        gridTemplateColumns,
      }}
    >
      {/* ═══════════════════════════════════════════════════════
          Mobile Drawer Overlay
          ════════════════════════════════════════════════════ */}
      {mobileDrawerOpen && (
        <>
          <div className="admin-mobile-backdrop" onClick={handleCloseMobile} />
          <aside className="admin-mobile-drawer">
            <AdminSidebar
              collapsed={false}
              activeMenu={activeMenu}
              user={user}
              onMenuClick={handleMenuClick}
              onToggle={handleCloseMobile}
              isMobile
              onUserClick={() => handleMenuClick('用户管理')}
            />
          </aside>
        </>
      )}

      {/* ═══════════════════════════════════════════════════════
          Column 1: Desktop Sidebar
          ════════════════════════════════════════════════════ */}
      <aside
        className="admin-desktop-sidebar"
        style={{
          width: '100%',
          position: 'relative',
        }}
      >
        <AdminSidebar
          collapsed={sidebarCollapsed}
          activeMenu={activeMenu}
          user={user}
          onMenuClick={handleMenuClick}
          onToggle={handleToggleSidebar}
          onUserClick={() => handleMenuClick('用户管理')}
        />
        {/* Resizable divider overlay — bound to sidebar right edge */}
        {!sidebarCollapsed && (
          <div className="admin-sidebar-resize-wrap">
            <ResizableDivider
              orientation="horizontal"
              size={sidebarWidthLive ?? sidebarWidth}
              onPreview={(v) => setSidebarWidthLive(v)}
              onCommit={(v) => {
                setSidebarWidthLive(null);
                setSidebarWidth(v);
                try { localStorage.setItem('agenthub_sidebar_w', String(v)); } catch { /* ignore */ }
              }}
              onReset={() => {
                setSidebarWidthLive(null);
                setSidebarWidth(240);
                try { localStorage.setItem('agenthub_sidebar_w', '240'); } catch { /* ignore */ }
              }}
              min={240}
              max={480}
              defaultValue={240}
              ariaLabel="侧边栏宽度"
              title="拖动调整侧边栏宽度 · 右键输入数值 · 双击重置"
              reversed={false}
              bubbleSide="right"
            />
          </div>
        )}
      </aside>

      {/* ═══════════════════════════════════════════════════════
          Column 2: Main Panel (Status Bar + Header + Content)
          ════════════════════════════════════════════════════ */}
      <div className="admin-right-panel">
        {/* ── Status bar (thin top bar) ── */}
        <div className="admin-status-bar">
          <div className="admin-status-item">
            运行指标尚未接入
          </div>
        </div>

        {/* ── Header bar ── */}
        <header className="admin-header">
          <div className="admin-header-inner">
            {/* Left section */}
            <div className="admin-header-left">
              {/* Mobile hamburger */}
              <button
                className="admin-header-icon-btn lg:hidden"
                onClick={() => setMobileDrawerOpen(true)}
                aria-label="Open menu"
              >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                  <line x1="3" y1="6" x2="21" y2="6" />
                  <line x1="3" y1="12" x2="21" y2="12" />
                  <line x1="3" y1="18" x2="21" y2="18" />
                </svg>
              </button>

              {/* Desktop sidebar toggle */}
              <button
                className="admin-header-icon-btn hidden lg:flex"
                onClick={handleToggleSidebar}
                aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
                title={sidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  {sidebarCollapsed ? (
                    <>
                      <polyline points="9 18 15 12 9 6" />
                      <line x1="20" y1="4" x2="20" y2="20" />
                    </>
                  ) : (
                    <>
                      <polyline points="15 18 9 12 15 6" />
                      <line x1="4" y1="4" x2="4" y2="20" />
                    </>
                  )}
                </svg>
              </button>

              {/* Logo + Title */}
              <img src="/logo.png" alt="AH" className="w-5 h-5 rounded-[4px] shrink-0 object-contain" />
              <h1 className="admin-header-title">AgentHub</h1>
              <span className="admin-header-subtitle">管理控制台</span>

              {/* Divider */}
              <div className="admin-header-divider" />

              {/* Workspace selector */}
              <div className="admin-header-workspace hidden md:block">
                <WorkspaceSelector />
              </div>

              {/* Active menu tag */}
              <span className="admin-header-menu-tag">{activeMenu}</span>

              {/* Context status — token / model / session */}
              <div className="admin-header-status">
                <span className="admin-header-status-item" title="当前模型">
                  <span className="admin-header-status-dot model" />
                  {defaultChatAgent || 'Claude Opus 4.8'}
                </span>
                <span className="admin-header-status-sep" />
                <span className="admin-header-status-item" title="Token 用量 (今日)">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
                  </svg>
                  12.4K / 200K
                </span>
                <span className="admin-header-status-sep" />
                <span className="admin-header-status-item" title="上下文窗口填充率">
                  <span className="admin-header-status-bar">
                    <span className="admin-header-status-fill" style={{ width: '18%' }} />
                  </span>
                  18%
                </span>
              </div>
            </div>

            {/* Right section */}
            <div className="admin-header-right">
              {/* Console toggle */}
              <button
                className="admin-header-action-btn admin-console-toggle"
                title={consoleCollapsed ? '显示控制台' : '隐藏控制台'}
                onClick={() => setConsoleCollapsed((v) => !v)}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="4 17 10 11 4 5" />
                  <line x1="12" y1="19" x2="20" y2="19" />
                </svg>
                <span className="admin-header-lang-label">控制台</span>
              </button>

              {/* Language switch */}
              <button
                className="admin-header-action-btn"
                title="Switch Language"
                onClick={() => {
                  const current = document.documentElement.lang || 'zh-CN';
                  document.documentElement.lang = current === 'zh-CN' ? 'en' : 'zh-CN';
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="10" />
                  <line x1="2" y1="12" x2="22" y2="12" />
                  <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
                </svg>
                <span className="admin-header-lang-label">中/EN</span>
              </button>

              {/* User tag */}
              <span className="admin-header-user-tag">
                <span className="admin-header-user-dot" />
                {user?.name || 'User'}
              </span>

              {/* Back to chat button */}
              <a className="admin-header-back-btn" href="/">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="15 18 9 12 15 6" />
                </svg>
                返回
              </a>
            </div>
          </div>
        </header>

        {/* ── Content area ── */}
        <main className="admin-content-area">
          <div className="admin-content-inner">
            {children}
          </div>
        </main>
      </div>

      {/* Console resize handle */}
      <div
        ref={consoleResizeRef}
        className={`admin-resize-handle console-handle ${consoleCollapsed ? '!hidden' : ''}`}
        title="拖拽调整控制台宽度"
      />

      {/* ═══════════════════════════════════════════════════════
          Column 3: Console Panel (Right)
          ════════════════════════════════════════════════════ */}
      {!consoleCollapsed && (
        <AdminActivityPanel activities={activities} onClear={() => setActivities([])} onCollapse={() => setConsoleCollapsed(true)} />
      )}

      {/* ── Console Expand Trigger — visible only when collapsed ── */}
      {consoleCollapsed && (
        <div
          className="admin-console-expand-trigger"
          onClick={() => setConsoleCollapsed(false)}
          title="展开控制台"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
          <span>前端活动</span>
        </div>
      )}

      {/* ── AI Chat Toggle Button ── */}
      <button
        className="admin-chat-toggle"
        onClick={() => setChatOpen(true)}
        title="AI 对话 (Ctrl+J)"
      >
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      </button>

      {/* ── AI Chat Dialog ── */}
      <AIChatDialog
        open={chatOpen}
        onClose={() => {
          setChatOpen(false);
          try { localStorage.setItem('agenthub_chat_closed_at', String(Date.now())); } catch { /* ignore */ }
        }}
      />

      {/* ── Global Command Palette (Ctrl+K) ── */}
      <CommandPalette onNavigate={handleMenuClick} />
    </div>
  );
}
