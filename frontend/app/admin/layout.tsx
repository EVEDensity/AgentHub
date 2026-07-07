'use client';

import { useState, useEffect, useCallback, useRef, type JSX, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '../../stores/authStore';
import { useAdminStore, MENU_META, MENU_GROUPS, type MenuItem } from '../../stores/adminStore';
import { useAgentStore } from '../../stores/agentStore';
import { WorkspaceSelector } from '../../components/admin/WorkspaceSelector';
import AdminSidebar from '../../components/admin/AdminSidebar';
import CommandPalette from '../../components/admin/CommandPalette';
import AIChatDialog from '../../components/chat/AIChatDialog';

/**
 * AdminLayout — AgentHub Warm Dark IDE Layout
 *
 * Structure: [Sidebar | ResizeHandle | Main+Header+Content | ResizeHandle | Console]
 * - Three-column CSS grid with resizable side panels
 * - Right console for logs, events, and command input
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
  const [consoleTab, setConsoleTab] = useState<'logs' | 'state' | 'tools'>('logs');
  const [consoleLogs, setConsoleLogs] = useState<Array<{ time: string; tag: string; msg: string; tagClass?: string }>>([
    { time: '--:--:--', tag: 'SYS', msg: 'AgentHub 控制台已就绪 · 等待管理操作', tagClass: 'info' },
  ]);

  const sidebarWidth = sidebarCollapsed ? '64px' : '240px';
  const consoleWidth = consoleCollapsed ? '0px' : '380px';

  // Auto-collapse on small screens
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
      if (w < 1280) {
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

  // ── Console log helpers ────────────────────────────────────────────
  const pad2 = (n: number) => (n < 10 ? '0' : '') + n;
  const getTime = () => {
    const d = new Date();
    return `${d.getHours()}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  };

  const addConsoleLog = useCallback((tag: string, msg: string, tagClass?: string) => {
    setConsoleLogs((prev) => [...prev.slice(-200), { time: getTime(), tag, msg, tagClass }]);
  }, []);

  // ── Stable callbacks ───────────────────────────────────────────────
  const handleMenuClick = useCallback(
    (item: MenuItem) => {
      setActiveMenu(item);
      router.push(`/admin?menu=${encodeURIComponent(item)}`);
      addConsoleLog('NAV', `导航至: ${item}`, 'gold');
      if (window.innerWidth < 1024) {
        setMobileDrawerOpen(false);
      }
    },
    [setActiveMenu, router, addConsoleLog],
  );

  const handleToggleSidebar = useCallback(() => {
    setSidebarCollapsed((v) => !v);
  }, []);

  const handleCloseMobile = useCallback(() => {
    setMobileDrawerOpen(false);
  }, []);

  // ── Resize handles ─────────────────────────────────────────────────
  const sidebarResizeRef = useRef<HTMLDivElement>(null);
  const consoleResizeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const sidebarHandle = sidebarResizeRef.current;
    const consoleHandle = consoleResizeRef.current;
    let isResizingS = false;
    let isResizingC = false;

    const onMouseDownS = (e: MouseEvent) => {
      isResizingS = true;
      sidebarHandle?.classList.add('active');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    };
    const onMouseDownC = (e: MouseEvent) => {
      isResizingC = true;
      consoleHandle?.classList.add('active');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    };
    const onMouseMove = (e: MouseEvent) => {
      if (isResizingS) {
        const w = Math.max(160, Math.min(320, e.clientX));
        document.documentElement.style.setProperty('--sidebar-w', w + 'px');
      }
      if (isResizingC) {
        const w = Math.max(260, Math.min(600, window.innerWidth - e.clientX));
        document.documentElement.style.setProperty('--console-w', w + 'px');
      }
    };
    const onMouseUp = () => {
      if (isResizingS || isResizingC) {
        isResizingS = false;
        isResizingC = false;
        sidebarHandle?.classList.remove('active');
        consoleHandle?.classList.remove('active');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
    };

    sidebarHandle?.addEventListener('mousedown', onMouseDownS);
    consoleHandle?.addEventListener('mousedown', onMouseDownC);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);

    return () => {
      sidebarHandle?.removeEventListener('mousedown', onMouseDownS);
      consoleHandle?.removeEventListener('mousedown', onMouseDownC);
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };
  }, []);

  // ── Console input handler ──────────────────────────────────────────
  const handleConsoleCommand = useCallback(
    (cmd: string) => {
      addConsoleLog('CMD', `> ${cmd}`, 'gold');
      const lower = cmd.toLowerCase().trim();
      if (lower === 'help') {
        addConsoleLog('HELP', '可用命令: help, clear, status, menu, routes, version', 'info');
      } else if (lower === 'clear') {
        setConsoleLogs([]);
      } else if (lower === 'status') {
        addConsoleLog('SYS', `菜单: ${activeMenu} · 侧栏: ${sidebarCollapsed ? '折叠' : '展开'} · 控制台: ${consoleCollapsed ? '隐藏' : '显示'}`, 'info');
      } else if (lower === 'version') {
        addConsoleLog('SYS', 'AgentHub v5.1.0 · Warm Dark Theme · Gold Accent', 'info');
      } else if (lower === 'menu') {
        addConsoleLog('SYS', `当前模块: ${activeMenu} · 共 ${MENU_META.length} 个菜单项`, 'info');
      } else {
        addConsoleLog('SYS', `未知指令: ${cmd} · 输入 help 查看可用命令`, 'warn');
      }
    },
    [addConsoleLog, activeMenu, sidebarCollapsed, consoleCollapsed],
  );

  return (
    <div
      className="admin-layout-root"
      style={{
        gridTemplateColumns: `${sidebarCollapsed ? '64px' : 'var(--sidebar-w, 240px)'} 4px 1fr 4px ${consoleCollapsed ? '0px' : 'var(--console-w, 380px)'}`,
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
            />
          </aside>
        </>
      )}

      {/* ═══════════════════════════════════════════════════════
          Column 1: Desktop Sidebar
          ════════════════════════════════════════════════════ */}
      <aside className={`admin-desktop-sidebar ${sidebarCollapsed ? 'w-[64px]' : 'w-[240px]'}`}>
        <AdminSidebar
          collapsed={sidebarCollapsed}
          activeMenu={activeMenu}
          user={user}
          onMenuClick={handleMenuClick}
          onToggle={handleToggleSidebar}
        />
      </aside>

      {/* Sidebar resize handle */}
      <div ref={sidebarResizeRef} className="admin-resize-handle" title="拖拽调整侧边栏宽度" />

      {/* ═══════════════════════════════════════════════════════
          Column 2: Main Panel (Status Bar + Header + Content)
          ════════════════════════════════════════════════════ */}
      <div className="admin-right-panel">
        {/* ── Status bar (thin top bar) ── */}
        <div className="admin-status-bar">
          <div className="admin-status-item">
            <span className="admin-status-dot-sm pulse" />
            系统正常
          </div>
          <div className="admin-status-item">
            RPM <span className="admin-status-value">1,247</span>
          </div>
          <div className="admin-status-item">
            P95 <span className="admin-status-value">1.8s</span>
          </div>
          <div className="admin-status-item">
            成功率 <span className="admin-status-value">98.7%</span>
          </div>
          <div className="admin-status-item">
            智能体 <span className="admin-status-value">6 在线</span>
          </div>
          <div className="admin-status-right">
            AGENTHUB v5.1.0 · CLAUDE WARM DARK
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

              {/* Title */}
              <h1 className="admin-header-title">AgentHub</h1>
              <span className="admin-header-subtitle">管理控制台</span>

              {/* Divider */}
              <div className="admin-header-divider" />

              {/* Workspace selector */}
              <div className="hidden md:block">
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
                className="admin-header-action-btn"
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
        <aside className="admin-console">
          <div className="admin-console-header">
            <div className="admin-console-tabs">
              <button
                className={`admin-console-tab ${consoleTab === 'logs' ? 'active' : ''}`}
                onClick={() => setConsoleTab('logs')}
              >
                执行日志
              </button>
              <button
                className={`admin-console-tab ${consoleTab === 'state' ? 'active' : ''}`}
                onClick={() => setConsoleTab('state')}
              >
                状态机
              </button>
              <button
                className={`admin-console-tab ${consoleTab === 'tools' ? 'active' : ''}`}
                onClick={() => setConsoleTab('tools')}
              >
                工具调用
              </button>
            </div>
            <div className="admin-console-actions">
              <button
                className="admin-console-act"
                title="清空日志"
                onClick={() => setConsoleLogs([])}
              >
                ◻
              </button>
              <button
                className="admin-console-act"
                title="折叠面板"
                onClick={() => setConsoleCollapsed(true)}
              >
                ⟩
              </button>
            </div>
          </div>

          <div className="admin-console-body">
            {consoleLogs.length === 0 ? (
              <div style={{ color: 'rgb(var(--warm-500))', padding: '20px 0', textAlign: 'center' }}>
                控制台已清空 · 等待事件…
              </div>
            ) : (
              consoleLogs.map((log, i) => (
                <div key={i} className="admin-console-log">
                  <span className="admin-console-log-time">{log.time}</span>
                  <span className={`admin-console-log-tag ${log.tagClass || 'info'}`}>{log.tag}</span>
                  <span className="admin-console-log-msg">{log.msg}</span>
                </div>
              ))
            )}
          </div>

          <div className="admin-console-input-row">
            <span className="admin-console-prompt">$</span>
            <input
              className="admin-console-input"
              placeholder="输入指令… help / clear / status / version"
              autoComplete="off"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.target as HTMLInputElement).value.trim()) {
                  handleConsoleCommand((e.target as HTMLInputElement).value);
                  (e.target as HTMLInputElement).value = '';
                }
              }}
            />
          </div>
        </aside>
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
        onClose={() => setChatOpen(false)}
        addConsoleLog={addConsoleLog}
      />

      {/* ── Global Command Palette (Ctrl+K) ── */}
      <CommandPalette onNavigate={handleMenuClick} />
    </div>
  );
}
