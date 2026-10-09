import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import MissionExecutionBanner, { executionUnitLabel } from '../../../components/chat/MissionExecutionBanner';
import type { ExecutionUnitStatus } from '../../../hooks/useMissionExecutionStatus';

const unit: ExecutionUnitStatus = {
  workUnitId: 'unit-1', status: 'PENDING', assignedAgentId: 'executor', assignedAdapter: 'function-calling',
  reason: 'waiting_runner', matchingRunnerAvailable: false, availabilitySource: 'none', lastSeenAt: null, availabilityExpiresAt: null,
};
const body = { schemaVersion: 1, missionId: 'mission-1', workspaceId: 'alice', missionStatus: 'RUNNING', observedAt: new Date().toISOString(), workUnits: [unit] };
const response = (status = 200) => new Response(JSON.stringify(body), { status });
const headers = () => ({ Authorization: 'Bearer test-token' });

describe('MissionExecutionBanner', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('shows waiting when the selected Agent has no matching Runner', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response()));
    render(<MissionExecutionBanner missionId="mission-1" token="test-token" authHeaders={headers} />);
    await screen.findByText('executor · 等待 Runner 连接，任务尚未执行');
    expect(screen.getByText('PENDING')).toBeVisible();
    expect(screen.queryByText('正在执行')).not.toBeInTheDocument();
  });

  it('shows status read failures and retries only the same read endpoint', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(503)).mockResolvedValueOnce(response());
    vi.stubGlobal('fetch', fetch);
    render(<MissionExecutionBanner missionId="mission-1" token="test-token" authHeaders={headers} />);
    await screen.findByRole('alert');
    await userEvent.click(screen.getByRole('button', { name: '重试读取状态' }));
    await screen.findByText('executor · 等待 Runner 连接，任务尚未执行');
    expect(fetch.mock.calls.every(call => call[0] === '/api/v1/missions/mission-1/execution-status')).toBe(true);
  });

  it('requires an unexpired server lease to label a WorkUnit as executing', () => {
    const running: ExecutionUnitStatus = { ...unit, status: 'RUNNING', reason: 'executing', matchingRunnerAvailable: true, availabilitySource: 'lease', availabilityExpiresAt: new Date(2000).toISOString() };
    expect(executionUnitLabel(running, 1000)).toBe('正在执行');
    expect(executionUnitLabel(running, 3000)).toContain('租约已过期');
  });

  it('does not carry another Mission snapshot across a selection change', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response()));
    const { rerender } = render(<MissionExecutionBanner missionId="mission-1" token="test-token" authHeaders={headers} />);
    await screen.findByText('PENDING');
    rerender(<MissionExecutionBanner missionId="other-mission" token="test-token" authHeaders={headers} />);
    await waitFor(() => expect(screen.queryByText('PENDING')).not.toBeInTheDocument());
  });
});
