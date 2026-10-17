'use client';

export interface FrontendActivity { id: number; time: string; message: string; }

/** Browser navigation records are not server logs, execution or health signals. */
export default function AdminActivityPanel({ activities, onClear, onCollapse }: {
  activities: FrontendActivity[]; onClear: () => void; onCollapse: () => void;
}) {
  return <aside className="admin-console" aria-label="前端活动">
    <div className="admin-console-header">
      <h2 className="admin-console-tab active">前端活动</h2>
      <div className="admin-console-actions">
        <button type="button" className="admin-console-act" title="清空前端活动" aria-label="清空前端活动" onClick={onClear}>◻</button>
        <button type="button" className="admin-console-act" title="折叠面板" aria-label="折叠面板" onClick={onCollapse}>⟩</button>
      </div>
    </div>
    <p className="border-b border-warm-200 px-3 py-2 text-xs text-warm-600">仅记录此页面的导航点击，时间来自浏览器；不是执行日志、工具调用或服务端状态。</p>
    <div className="admin-console-body" role="log" aria-label="本地导航活动" aria-live="polite">
      {activities.length === 0 ? <p className="p-4 text-center text-xs text-warm-600">暂无前端活动</p> : activities.map(activity => <div key={activity.id} className="admin-console-log">
        <span className="admin-console-log-time">{activity.time}</span>
        <span className="admin-console-log-tag info">NAV</span>
        <span className="admin-console-log-msg">{activity.message}</span>
      </div>)}
    </div>
  </aside>;
}
