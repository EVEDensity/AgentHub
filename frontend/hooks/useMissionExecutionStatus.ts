import { useCallback, useEffect, useRef, useState } from 'react';

export interface ExecutionUnitStatus {
  workUnitId: string;
  status: string;
  assignedAgentId: string | null;
  assignedAdapter: string | null;
  reason: string;
  matchingRunnerAvailable: boolean;
  availabilitySource: 'poll' | 'lease' | 'none';
  lastSeenAt: string | null;
  availabilityExpiresAt: string | null;
}

export interface MissionExecutionStatus {
  schemaVersion: 1;
  missionId: string;
  workspaceId: string;
  missionStatus: string;
  observedAt: string;
  workUnits: ExecutionUnitStatus[];
}

export function useMissionExecutionStatus(missionId: string | null, token: string | undefined, authHeaders: () => Record<string, string>) {
  const [data, setData] = useState<MissionExecutionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const headersRef = useRef(authHeaders);
  headersRef.current = authHeaders;
  const retry = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    setData(null);
    setError(null);
    if (!missionId || !token) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const response = await fetch(`/api/v1/missions/${encodeURIComponent(missionId)}/execution-status`, { headers: headersRef.current(), signal: controller.signal });
        if (!response.ok) throw new Error(`执行状态暂不可用 (HTTP ${response.status})`);
        const value = await response.json() as MissionExecutionStatus;
        if (value.schemaVersion !== 1 || value.missionId !== missionId || !Array.isArray(value.workUnits)) throw new Error('执行状态响应无效');
        if (controller.signal.aborted) return;
        setData(value);
        setError(null);
        if (!['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(value.missionStatus)) timer = setTimeout(poll, 2000);
      } catch (err) {
        if (controller.signal.aborted) return;
        setData(null);
        setError(err instanceof Error ? err.message : '执行状态暂不可用');
      }
    };
    void poll();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [missionId, token, revision]);

  return { data: data?.missionId === missionId ? data : null, error, retry };
}
