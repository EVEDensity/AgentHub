import { create } from 'zustand';

export const SETTINGS_MENU = [
  '服务商', '工作流', '权限', '通用', 'IM 接入', 'MCP', '技能', '记忆', '插件',
  'Computer Use', '审计日志', '用户管理',
] as const;

export type MenuItem = (typeof SETTINGS_MENU)[number];

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
