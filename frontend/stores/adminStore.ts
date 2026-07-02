import { create } from 'zustand';

export const SETTINGS_MENU = [
  '服务商', '工作流', '权限', '通用', 'IM 接入', 'MCP', '技能', '记忆', '插件',
  'Computer Use', '审计日志', '用户管理',
] as const;

export type MenuItem = (typeof SETTINGS_MENU)[number];

/** Menu metadata for rich sidebar rendering — icons, groups, optional badges */
export interface MenuItemMeta {
  key: MenuItem;
  icon: string;
  group: string;
}

export const MENU_GROUPS = [
  { key: '核心配置', label: '核心配置' },
  { key: '能力扩展', label: '能力扩展' },
  { key: '系统运维', label: '系统运维' },
] as const;

export const MENU_META: readonly MenuItemMeta[] = [
  { key: '服务商',       icon: '🏭', group: '核心配置' },
  { key: '工作流',       icon: '🔀', group: '核心配置' },
  { key: '权限',         icon: '🔐', group: '核心配置' },
  { key: '通用',         icon: '⚙️', group: '核心配置' },
  { key: 'IM 接入',      icon: '💬', group: '能力扩展' },
  { key: 'MCP',          icon: '🔌', group: '能力扩展' },
  { key: '技能',         icon: '🎯', group: '能力扩展' },
  { key: '插件',         icon: '🧩', group: '能力扩展' },
  { key: 'Computer Use', icon: '🖥️', group: '系统运维' },
  { key: '审计日志',     icon: '📋', group: '系统运维' },
  { key: '用户管理',     icon: '👥', group: '系统运维' },
  { key: '记忆',         icon: '🧠', group: '系统运维' },
] as const;

interface AdminState {
  activeMenu: MenuItem;
  setActiveMenu: (m: MenuItem) => void;
  notice: string;
  setNotice: (msg: string) => void;
}

export const useAdminStore = create<AdminState>()((set) => ({
  activeMenu: '服务商',
  setActiveMenu: (m) => set({ activeMenu: m }),

  notice: '',
  setNotice: (msg) => set({ notice: msg }),
}));
