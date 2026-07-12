import { useEffect, useState, type JSX } from 'react';
import dynamic from 'next/dynamic';
import type { MCPDashboardData } from '../../../types';

const DashboardOverview = dynamic(() => import('./DashboardOverview'), { ssr: false, loading: () => <div className="p-8 text-sm text-warm-400">加载仪表盘中...</div> });
const AgentMonitor = dynamic(() => import('./AgentMonitor'), { ssr: false, loading: () => <div className="p-8 text-sm text-warm-400">加载中...</div> });
const SessionManager = dynamic(() => import('./SessionManager'), { ssr: false, loading: () => <div className="p-8 text-sm text-warm-400">加载中...</div> });
const TaskMonitor = dynamic(() => import('./TaskMonitor'), { ssr: false, loading: () => <div className="p-8 text-sm text-warm-400">加载中...</div> });

const MCP_SUB_TABS = [
  { key: 'overview', label: '[chart] 仪表盘' },
  { key: 'agents', label: '[bot] Agents' },
  { key: 'sessions', label: '[chat] 会话' },
  { key: 'tasks', label: '[clipboard] 任务' },
  { key: 'tools', label: '[wrench] 工具' },
  { key: 'alerts', label: '[bell] 告警' },
  { key: 'config', label: '[gear] 配置' },
  { key: 'database', label: '[database] 数据库' },
] as const;

type SubTab = (typeof MCP_SUB_TABS)[number]['key'];

function authHeaders(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  const token = localStorage.getItem('agenthub_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

interface MCPLayoutProps {
  /** Optional initial sub-tab to activate */
  initialTab?: SubTab;
}

export default function MCPLayout({ initialTab = 'overview' }: MCPLayoutProps): JSX.Element {
  const [activeTab, setActiveTab] = useState<SubTab>(initialTab);
  const [dashboardData, setDashboardData] = useState<MCPDashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Fetch dashboard data (always needed for overview + shared across tabs)
  async function fetchDashboard(): Promise<void> {
    setLoading(true);
    setError('');
    try {
      const res = await fetch('/api/admin/mcp/dashboard', { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json() as MCPDashboardData;
      setDashboardData(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load dashboard');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void fetchDashboard();
    // Poll every 15s for real-time updates
    const interval = setInterval(() => { void fetchDashboard(); }, 15000);
    return () => clearInterval(interval);
  }, []);

  return (
    <section className="space-y-4">
      {/* Sub-tab navigation */}
      <div className="flex items-center gap-1 border-b border-warm-150 pb-0 overflow-x-auto">
        {MCP_SUB_TABS.map((tab) => (
          <button
            key={tab.key}
            className={`shrink-0 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px rounded-t-lg ${
              activeTab === tab.key
                ? 'border-primary-400 text-primary-700 bg-primary-50/50'
                : 'border-transparent text-warm-500 hover:text-warm-700 hover:bg-warm-50'
            }`}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label}
          </button>
        ))}
        {/* Auto-refresh indicator */}
        <div className="ml-auto shrink-0 flex items-center gap-2 text-xs text-warm-400">
          <span className={`h-2 w-2 rounded-full ${loading ? 'bg-warning-400 animate-pulse' : 'bg-success-500'}`} />
          {loading ? '刷新中...' : dashboardData ? `已更新 ${dashboardData.timestamp?.slice(11, 19) || '--'}` : '—'}
          <button className="text-primary-500 hover:text-primary-700 underline" onClick={() => void fetchDashboard()}>
            刷新
          </button>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="rounded-lg bg-danger-50 border border-danger-200 px-4 py-3 text-sm text-danger-600">
          {error}
          <button className="ml-2 underline" onClick={() => void fetchDashboard()}>重试</button>
        </div>
      )}

      {/* Tab content */}
      {activeTab === 'overview' && <DashboardOverview data={dashboardData} loading={loading} onRefresh={() => void fetchDashboard()} />}
      {activeTab === 'agents' && <AgentMonitor />}
      {activeTab === 'sessions' && <SessionManager />}
      {activeTab === 'tasks' && <TaskMonitor />}
      {activeTab === 'tools' && (
        <div className="flex items-center justify-center py-20 text-sm text-warm-400">
          [wrench] 工具分析 — 开发中，敬请期待
        </div>
      )}
      {activeTab === 'alerts' && (
        <div className="flex items-center justify-center py-20 text-sm text-warm-400">
          [bell] 告警管理 — 开发中，敬请期待
        </div>
      )}
      {activeTab === 'config' && (
        <div className="flex items-center justify-center py-20 text-sm text-warm-400">
          [gear] 配置管理 — 开发中，敬请期待
        </div>
      )}
      {activeTab === 'database' && (
        <div className="flex items-center justify-center py-20 text-sm text-warm-400">
          [database] 数据库管理 — 开发中，敬请期待
        </div>
      )}
    </section>
  );
}
