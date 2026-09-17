import { create } from 'zustand';

export const SETTINGS_MENU = [
  'Mission Control', '服务商', '工作流', '权限', '通用', 'IM 接入', 'MCP', '技能', '记忆', '插件',
  'Computer Use', '决策收件箱', '审计日志', '用户管理', '终端',
  '知识库', '模板市场', '工具市场', '工作空间', '上下文引擎', 'AgentNet',
  'Agent 身份', 'Docker 沙箱', '多模态工作区', '集中日志', '模块连线', 'RAG 检索', '检索评估', 'A2A 互操作', 'A/B 测试',
  '成本分析', 'SLO 仪表板', '离线评估',
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
  { key: 'Mission Control', icon: '🎯', group: '核心配置' },
  { key: '服务商',       icon: '🏭', group: '核心配置' },
  { key: '工作流',       icon: '🔀', group: '核心配置' },
  { key: '权限',         icon: '🔐', group: '核心配置' },
  { key: '通用',         icon: '⚙️', group: '核心配置' },
  { key: 'IM 接入',      icon: '💬', group: '能力扩展' },
  { key: 'MCP',          icon: '🔌', group: '能力扩展' },
  { key: '技能',         icon: '🎯', group: '能力扩展' },
  { key: '插件',         icon: '🧩', group: '能力扩展' },
  { key: 'Computer Use', icon: '🖥️', group: '系统运维' },
  { key: '决策收件箱',   icon: '⚖️', group: '系统运维' },
  { key: '审计日志',     icon: '📋', group: '系统运维' },
  { key: '用户管理',     icon: '👥', group: '系统运维' },
  { key: '记忆',         icon: '🧠', group: '系统运维' },
  { key: '知识库',       icon: '📚', group: '能力扩展' },
  { key: '模板市场',     icon: '🏪', group: '能力扩展' },
  { key: '工具市场',     icon: '🔧', group: '能力扩展' },
  { key: '工作空间',     icon: '🏢', group: '核心配置' },
  { key: '上下文引擎',   icon: '🧿', group: '系统运维' },
  { key: 'AgentNet',     icon: '🕸️', group: '能力扩展' },
  { key: 'Agent 身份',   icon: '🪪', group: '核心配置' },
  { key: 'Docker 沙箱',  icon: '🐳', group: '系统运维' },
  { key: '多模态工作区', icon: '🎯', group: '核心配置' },
  { key: '集中日志',     icon: '📋', group: '系统运维' },
  { key: '模块连线',     icon: '🔗', group: '系统运维' },
  { key: 'RAG 检索',     icon: '🔎', group: '能力扩展' },
  { key: '检索评估',     icon: '📊', group: '能力扩展' },
  { key: 'A2A 互操作',   icon: '🔗', group: '能力扩展' },
  { key: 'A/B 测试',     icon: '🧪', group: '能力扩展' },
  { key: '成本分析',     icon: '💰', group: '系统运维' },
  { key: 'SLO 仪表板',   icon: '🎯', group: '系统运维' },
  { key: '离线评估',     icon: '🧪', group: '系统运维' },
  { key: '终端',         icon: '💻', group: '系统运维' },
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
