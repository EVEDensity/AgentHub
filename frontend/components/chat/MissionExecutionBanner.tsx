import { useState } from 'react';
import { useMissionExecutionStatus, type ExecutionUnitStatus } from '../../hooks/useMissionExecutionStatus';

const LABELS: Record<string, string> = {
  waiting_runner: '等待 Runner 连接，任务尚未执行',
  waiting_claim: 'Runner 已连接，等待领取任务',
  waiting_dependencies: '等待前置任务完成',
  waiting_decision: '等待人工决策',
  leased: 'Runner 已领取，等待开始执行',
  executing: '正在执行',
  lease_expired: 'Runner 租约已过期，执行状态待恢复',
  verifying: '执行产物等待独立验收',
  succeeded: '验收通过',
  failed: '任务失败',
  cancelled: '任务已取消',
};

export function executionUnitLabel(unit: ExecutionUnitStatus, now = Date.now()): string {
  const fresh = unit.availabilityExpiresAt !== null && Date.parse(unit.availabilityExpiresAt) > now;
  if (unit.reason === 'executing' && (unit.status !== 'RUNNING' || unit.availabilitySource !== 'lease' || !unit.matchingRunnerAvailable || !fresh)) {
    return LABELS.lease_expired;
  }
  if (unit.reason === 'waiting_claim' && !fresh) return LABELS.waiting_runner;
  return LABELS[unit.reason] ?? '执行状态待确认';
}

export default function MissionExecutionBanner({ missionId, token, authHeaders, onCancel }: {
  missionId: string | null; token?: string; authHeaders: () => Record<string, string>;
  onCancel?: () => Promise<boolean>;
}) {
  const { data, error, retry } = useMissionExecutionStatus(missionId, token, authHeaders);
  const [cancelling, setCancelling] = useState(false);
  if (!missionId) return null;
  return (
    <section aria-label="Mission 执行状态" className="border-b border-warm-150 px-6 py-3 text-sm" aria-live="polite">
      {error ? (
        <div role="alert" className="text-danger-600">
          {error} <button type="button" onClick={retry} className="ml-2 underline">重试读取状态</button>
        </div>
      ) : !data ? '正在读取任务状态…' : data.workUnits.length === 0 ? LABELS[data.missionStatus.toLowerCase()] ?? '等待任务派发' : data.workUnits.map(unit => (
        <div key={unit.workUnitId}>
          <span>{unit.assignedAgentId ?? '未绑定执行器'} · {executionUnitLabel(unit)}</span>
          <span className="ml-2 text-xs text-warm-500">{unit.status}</span>
        </div>
      ))}
      {data && !['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(data.missionStatus) && onCancel && (
        <button type="button" disabled={cancelling} className="mt-2 underline" onClick={() => {
          setCancelling(true);
          void onCancel().finally(() => { setCancelling(false); retry(); });
        }}>取消任务</button>
      )}
    </section>
  );
}
