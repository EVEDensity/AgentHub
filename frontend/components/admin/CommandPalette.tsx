'use client';

import React, { useEffect, useState, useCallback, useRef, useMemo, type JSX } from 'react';
import { useRouter } from 'next/navigation';
import { useAdminStore, MENU_META, MENU_GROUPS, type MenuItem } from '../../stores/adminStore';

interface CommandPaletteProps {
  /** Called with the selected menu item to navigate */
  onNavigate: (item: MenuItem) => void;
}

interface SearchResult {
  type: 'nav' | 'action';
  label: string;
  desc: string;
  icon: string;
  group: string;
  /** For nav items: the menu key; for actions: the href or action id */
  target: string;
}

/**
 * Global Command Palette — Ctrl+K to invoke.
 *
 * Features:
 * - Fuzzy search across all admin menus + quick actions
 * - Keyboard navigation (↑ ↓ Enter Escape)
 * - Action set: back to chat, collapse sidebar, settings, logout
 * - Rendered as a fixed overlay modal with dark theme + cyan accent
 */
export default function CommandPalette({ onNavigate }: CommandPaletteProps): JSX.Element | null {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [selectedIdx, setSelectedIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // ── Build search corpus ──────────────────────────────────────────
  const corpus = useMemo<SearchResult[]>(() => {
    const items: SearchResult[] = [];

    // Navigation: all admin menu items
    for (const m of MENU_META) {
      const group = MENU_GROUPS.find((g) => g.key === m.group);
      items.push({
        type: 'nav',
        label: m.key,
        desc: `${group?.label ?? m.group} · 导航至 ${m.key} 管理页面`,
        icon: m.icon,
        group: group?.label ?? m.group,
        target: m.key,
      });
    }

    // Quick actions
    items.push(
      { type: 'action', label: '返回对话', desc: '回到 AgentHub Chat 主界面', icon: '💬', group: '快捷操作', target: '/' },
      { type: 'action', label: '折叠侧边栏', desc: '切换左侧导航栏展开/收起状态', icon: '📐', group: '快捷操作', target: 'toggle-sidebar' },
      { type: 'action', label: '终端', desc: '打开管理终端 — 模拟 Shell 命令行操作', icon: '💻', group: '快捷操作', target: '终端' },
      { type: 'action', label: '通用设置', desc: '打开通用配置页面', icon: '⚙️', group: '快捷操作', target: '通用' },
      { type: 'action', label: '审计日志', desc: '查看系统审计日志', icon: '📋', group: '快捷操作', target: '审计日志' },
      { type: 'action', label: '用户管理', desc: '管理用户与权限', icon: '👥', group: '快捷操作', target: '用户管理' },
    );

    return items;
  }, []);

  // ── Fuzzy filter ──────────────────────────────────────────────────
  const results = useMemo(() => {
    if (!query.trim()) return corpus;
    const q = query.toLowerCase();
    return corpus.filter(
      (r) =>
        r.label.toLowerCase().includes(q) ||
        r.desc.toLowerCase().includes(q) ||
        r.group.toLowerCase().includes(q),
    );
  }, [query, corpus]);

  // ── Keyboard listener ─────────────────────────────────────────────
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      // Ctrl+K or Cmd+K → toggle
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        setOpen((prev) => !prev);
        return;
      }
      // Escape → close
      if (e.key === 'Escape' && open) {
        e.preventDefault();
        setOpen(false);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open]);

  // ── Reset state when opening ─────────────────────────────────────
  useEffect(() => {
    if (open) {
      setQuery('');
      setSelectedIdx(0);
      // Focus input after render
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // ── Keyboard navigation inside palette ────────────────────────────
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault();
          setSelectedIdx((i) => Math.min(i + 1, results.length - 1));
          break;
        case 'ArrowUp':
          e.preventDefault();
          setSelectedIdx((i) => Math.max(i - 1, 0));
          break;
        case 'Enter': {
          e.preventDefault();
          const r = results[selectedIdx];
          if (r) execute(r);
          break;
        }
        case 'Escape':
          e.preventDefault();
          setOpen(false);
          break;
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [results, selectedIdx],
  );

  // ── Scroll selected into view ─────────────────────────────────────
  useEffect(() => {
    if (listRef.current) {
      const el = listRef.current.children[selectedIdx] as HTMLElement | undefined;
      el?.scrollIntoView({ block: 'nearest' });
    }
  }, [selectedIdx]);

  // ── Execute a result ──────────────────────────────────────────────
  const execute = useCallback(
    (r: SearchResult) => {
      setOpen(false);
      if (r.type === 'nav') {
        onNavigate(r.target as MenuItem);
      } else {
        // Action
        switch (r.target) {
          case '/':
            router.push('/');
            break;
          case 'toggle-sidebar':
            // Dispatch a custom event that the layout listens for
            window.dispatchEvent(new CustomEvent('toggle-sidebar'));
            break;
          default:
            // Treat as menu navigation for admin actions
            if ((r.target as string) && r.target !== '/') {
              onNavigate(r.target as MenuItem);
            }
            break;
        }
      }
    },
    [onNavigate, router],
  );

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="cmd-backdrop"
        onClick={() => setOpen(false)}
      />

      {/* Modal */}
      <div className="cmd-modal">
        {/* Search input */}
        <div className="cmd-search">
          <svg
            width="18" height="18" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            style={{ flexShrink: 0, opacity: 0.5 }}
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            ref={inputRef}
            className="cmd-input"
            type="text"
            placeholder="搜索菜单、动作..."
            value={query}
            onChange={(e) => { setQuery(e.target.value); setSelectedIdx(0); }}
            onKeyDown={handleKeyDown}
          />
          <kbd className="cmd-kbd">esc</kbd>
        </div>

        {/* Results */}
        <div className="cmd-results" ref={listRef}>
          {results.length === 0 && (
            <div className="cmd-empty">
              <span style={{ fontSize: 24, opacity: 0.4 }}>🔍</span>
              <span>未找到匹配项</span>
            </div>
          )}

          {results.map((r, i) => {
            const isSelected = i === selectedIdx;
            return (
              <button
                key={`${r.type}-${r.target}-${i}`}
                type="button"
                className={`cmd-item${isSelected ? ' selected' : ''}`}
                onClick={() => execute(r)}
                onMouseEnter={() => setSelectedIdx(i)}
              >
                <span className="cmd-item-icon">{r.icon}</span>
                <span className="cmd-item-body">
                  <span className="cmd-item-label">{r.label}</span>
                  <span className="cmd-item-desc">{r.desc}</span>
                </span>
                <span className="cmd-item-group">
                  <span className={`cmd-item-tag${r.type === 'action' ? ' action' : ''}`}>
                    {r.group}
                  </span>
                </span>
                {isSelected && (
                  <span className="cmd-item-enter" aria-hidden="true">⏎</span>
                )}
              </button>
            );
          })}
        </div>

        {/* Footer */}
        <div className="cmd-footer">
          <span className="cmd-footer-hint">
            <kbd>↑↓</kbd> 导航
          </span>
          <span className="cmd-footer-hint">
            <kbd>↵</kbd> 选择
          </span>
          <span className="cmd-footer-hint">
            <kbd>Esc</kbd> 关闭
          </span>
          <span className="cmd-footer-hint" style={{ marginLeft: 'auto', opacity: 0.5 }}>
            Ctrl+K 再次打开
          </span>
        </div>
      </div>
    </>
  );
}
