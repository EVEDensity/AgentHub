import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DecisionInbox from '../../../components/admin/DecisionInbox';
import { useWorkspaceStore } from '../../../stores/workspaceStore';
import type { MissionDecision } from '../../../types';

const decision: MissionDecision = {
  id: 'decision-1',
  missionId: 'mission-1',
  workUnitId: 'work-unit-1',
  attempt: 2,
  contextDigest: `sha256:${'a'.repeat(64)}`,
  reasonCode: 'artifact_requirements_not_met',
  criterionIds: ['criterion-1'],
  options: ['RETRY_WORK_UNIT', 'FAIL_MISSION'],
  recommendedOption: 'RETRY_WORK_UNIT',
  riskSummary: 'Required execution evidence is incomplete.',
  status: 'PENDING',
  version: 3,
  requestedBy: { type: 'verifier', id: 'verifier-1' },
  requestedAt: '2026-08-16T04:00:00Z',
  expiresAt: '2026-08-16T05:00:00Z',
};

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

describe('DecisionInbox', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useWorkspaceStore.setState({ currentWorkspaceId: 'workspace-alpha' });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('loads real pending decisions for the selected workspace with authorization', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { decisions: [decision] }));
    vi.stubGlobal('fetch', fetchMock);

    render(
      <DecisionInbox
        authHeaders={() => ({ Authorization: 'Bearer test-token' })}
        setNotice={vi.fn()}
      />,
    );

    expect(await screen.findAllByText('Artifact 要求未满足')).toHaveLength(2);
    expect(screen.getByText('Required execution evidence is incomplete.')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/missions/decisions?workspaceId=workspace-alpha&status=PENDING&limit=100&offset=0',
      expect.objectContaining({
        headers: { Authorization: 'Bearer test-token' },
        signal: expect.any(AbortSignal),
      }),
    );
  });

  it('renders an honest empty state without manufacturing decisions', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { decisions: [] })));

    render(<DecisionInbox authHeaders={() => ({})} setNotice={vi.fn()} />);

    expect(await screen.findByText('当前没有待处理 Decision')).toBeInTheDocument();
    expect(screen.getByText('新决策由真实评估和执行链路产生。')).toBeInTheDocument();
    expect(screen.queryByText('mission-1')).not.toBeInTheDocument();
  });

  it('keeps load failures visible and retries through the server', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(503, { detail: 'Mission Control unavailable' }))
      .mockResolvedValueOnce(jsonResponse(200, { decisions: [] }));
    vi.stubGlobal('fetch', fetchMock);

    render(<DecisionInbox authHeaders={() => ({})} setNotice={vi.fn()} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Mission Control unavailable');
    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('当前没有待处理 Decision')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('resolves with the current version and refreshes only after server confirmation', async () => {
    const user = userEvent.setup();
    const setNotice = vi.fn();
    const resolved = { ...decision, status: 'RESOLVED' as const, resolution: 'RETRY_WORK_UNIT' as const };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(200, { decisions: [decision] }))
      .mockResolvedValueOnce(jsonResponse(200, { decision: resolved, workUnit: {}, mission: {} }))
      .mockResolvedValueOnce(jsonResponse(200, { decisions: [] }));
    vi.stubGlobal('fetch', fetchMock);

    render(
      <DecisionInbox
        authHeaders={() => ({ Authorization: 'Bearer operator' })}
        setNotice={setNotice}
      />,
    );

    await screen.findByText('Required execution evidence is incomplete.');
    await user.type(screen.getByLabelText('决策依据'), 'Evidence collection can be retried safely.');
    await user.click(screen.getByRole('button', { name: '重试 WorkUnit' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/missions/mission-1/decisions/decision-1/resolve',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer operator' },
        body: JSON.stringify({
          expectedVersion: 3,
          resolution: 'RETRY_WORK_UNIT',
          rationale: 'Evidence collection can be retried safely.',
        }),
      }),
    ));
    expect(await screen.findByText('当前没有待处理 Decision')).toBeInTheDocument();
    expect(setNotice).toHaveBeenCalledWith('Decision decision-1 已处理：重试 WorkUnit');
  });

  it('refreshes stale state after a version conflict and preserves the warning', async () => {
    const user = userEvent.setup();
    const latest = { ...decision, version: 4, riskSummary: 'A newer verifier result is available.' };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(200, { decisions: [decision] }))
      .mockResolvedValueOnce(jsonResponse(409, { detail: 'expected version 3, found 4' }))
      .mockResolvedValueOnce(jsonResponse(200, { decisions: [latest] }));
    vi.stubGlobal('fetch', fetchMock);

    render(<DecisionInbox authHeaders={() => ({})} setNotice={vi.fn()} />);

    await screen.findByText('Required execution evidence is incomplete.');
    await user.type(screen.getByLabelText('决策依据'), 'Retry with current evidence.');
    await user.click(screen.getByRole('button', { name: '重试 WorkUnit' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Decision 已被其他操作者更新');
    expect(await screen.findByText('A newer verifier result is available.')).toBeInTheDocument();
    expect(screen.getByText('v4')).toBeInTheDocument();
    expect(screen.getByLabelText('决策依据')).toHaveValue('');
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
