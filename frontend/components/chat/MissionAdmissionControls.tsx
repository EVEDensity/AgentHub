import type { PendingMissionConfirmation } from '../../hooks/useMissionChat';

export default function MissionAdmissionControls({ pending, error, sending, missionId, onConfirm, onCancel, onReconnect }: {
  pending: PendingMissionConfirmation | null; error: string | null; sending: boolean;
  missionId: string | null; onConfirm: () => Promise<unknown>; onCancel: () => Promise<boolean>; onReconnect: () => void;
}) {
  if (!pending && !error && !sending) return null;
  return (
    <section className="border-b border-warm-150 px-6 py-3 text-sm" aria-live="polite">
      {sending && <span>正在提交请求…</span>}
      {pending && (
        <div>
          <span>等待确认：{pending.description}</span>
          <button type="button" disabled={sending} onClick={() => void onConfirm()} className="ml-3 underline">确认执行</button>
          <button type="button" disabled={sending} onClick={() => void onCancel()} className="ml-3 underline">取消请求</button>
        </div>
      )}
      {error && (
        <div role="alert" className="text-danger-600">
          {error}{missionId && <button type="button" onClick={onReconnect} className="ml-2 underline">重新连接事件</button>}
        </div>
      )}
    </section>
  );
}
