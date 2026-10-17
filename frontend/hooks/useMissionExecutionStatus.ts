import { useCallback, useEffect, useRef, useState } from 'react';
import { missionIdentifier, readMissionJson } from '../lib/missionStream';

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

const MISSION_STATES = ['DRAFT', 'READY', 'RUNNING', 'VERIFYING', 'WAITING_DECISION', 'SUCCEEDED', 'FAILED', 'CANCELLED'];
const UNIT_STATES = ['PENDING', 'LEASED', 'RUNNING', 'VERIFYING', 'WAITING', 'RETRYING', 'SUCCEEDED', 'FAILED', 'CANCELLED'];
const REASONS = ['waiting_runner', 'waiting_claim', 'waiting_dependencies', 'waiting_decision', 'leased', 'executing', 'lease_expired', 'verifying', 'succeeded', 'failed', 'cancelled'];
const timestamp = (value: unknown) => typeof value === 'string' && /T.+(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
const nullableText = (value: unknown) => value === null || typeof value === 'string' && value.length > 0 && value.length <= 255;

function validatedStatus(value: Record<string, any>, missionId: string, workspaceId?: string): MissionExecutionStatus {
  if (value.schemaVersion !== 1 || value.missionId !== missionId || !missionIdentifier(value.workspaceId)
      || (workspaceId !== undefined && value.workspaceId !== workspaceId) || !MISSION_STATES.includes(value.missionStatus)
      || !timestamp(value.observedAt) || !Array.isArray(value.workUnits) || value.workUnits.length > 1000) throw new Error('执行状态响应无效');
  const ids = new Set<string>();
  for (const unit of value.workUnits) {
    if (!unit || typeof unit !== 'object' || !missionIdentifier(unit.workUnitId) || ids.has(unit.workUnitId)
        || !UNIT_STATES.includes(unit.status) || !REASONS.includes(unit.reason)
        || !nullableText(unit.assignedAgentId) || !nullableText(unit.assignedAdapter)
        || typeof unit.matchingRunnerAvailable !== 'boolean' || !['poll', 'lease', 'none'].includes(unit.availabilitySource)
        || !(unit.lastSeenAt === null || timestamp(unit.lastSeenAt))
        || !(unit.availabilityExpiresAt === null || timestamp(unit.availabilityExpiresAt))) throw new Error('执行状态响应无效');
    ids.add(unit.workUnitId);
  }
  return value as MissionExecutionStatus;
}

export interface MissionExecutionReadOptions { workspaceId?: string; enabled?: boolean; }

export function useMissionExecutionStatus(missionId: string | null, token: string | undefined, authHeaders: () => Record<string, string>, options: MissionExecutionReadOptions = {}) {
  const scope = JSON.stringify([missionId, token, options.workspaceId, options.enabled !== false]);
  const [snapshot, setSnapshot] = useState<{ scope: string; data: MissionExecutionStatus | null; error: string | null } | null>(null);
  const [revision, setRevision] = useState(0);
  const headersRef = useRef(authHeaders);
  headersRef.current = authHeaders;
  const retry = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    setSnapshot(null);
    if (!missionId || !token || options.enabled === false) return;
    let stopped = false;
    let controller: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      controller = new AbortController();
      const readController = controller;
      let deadline: ReturnType<typeof setTimeout> | undefined;
      try {
        const value = await Promise.race([(async () => {
          const response = await fetch(`/api/v1/missions/${encodeURIComponent(missionId)}/execution-status`, {
            headers: headersRef.current(), signal: readController.signal, cache: 'no-store', credentials: 'same-origin', redirect: 'error',
          });
          if (!response.ok) { void response.body?.cancel().catch(() => undefined); throw new Error(`执行状态暂不可用 (HTTP ${response.status})`); }
          return validatedStatus(await readMissionJson(response, readController.signal), missionId, options.workspaceId);
        })(), new Promise<never>((_, reject) => { deadline = setTimeout(() => { readController.abort(); reject(new Error('执行状态读取超时，请重试')); }, 10_000); })]);
        if (stopped || readController.signal.aborted) return;
        setSnapshot({ scope, data: value, error: null });
        if (!['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(value.missionStatus)) timer = setTimeout(poll, 2000);
      } catch (err) {
        if (stopped) return;
        setSnapshot({ scope, data: null, error: err instanceof Error ? err.message : '执行状态暂不可用' });
      } finally { clearTimeout(deadline); }
    };
    void poll();
    return () => { stopped = true; controller?.abort(); if (timer) clearTimeout(timer); };
  }, [missionId, token, scope, options.enabled, options.workspaceId, revision]);

  const visible = snapshot?.scope === scope ? snapshot : null;
  return { data: visible?.data ?? null, error: visible?.error ?? null, retry };
}

export type MissionExecutionReadState = ReturnType<typeof useMissionExecutionStatus>;
