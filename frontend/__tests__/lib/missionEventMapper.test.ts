import { mapMissionEvent } from '../../lib/missionEventMapper';

interface MissionEvent {
  missionId?: string;
  aggregateId?: string;
  eventType?: string;
  type?: string;
  title?: string;
  summary?: string;
  verdict?: string;
  reason?: string;
  generatedAt?: string;
  timestamp?: string;
  workUnitId?: string;
  contentAddress?: string;
  content_address?: string;
  decisionId?: string;
}

describe('mapMissionEvent', () => {
  test('maps work_unit.started to progress_update', () => {
    const event: MissionEvent = {
      missionId: 'mis-1',
      eventType: 'work_unit.started',
      title: 'Generate route file',
      generatedAt: '2026-09-01T12:00:00Z',
    };
    const result = mapMissionEvent(event);
    expect(result).toMatchObject({
      missionId: 'mis-1',
      type: 'progress_update',
      content: expect.stringContaining('Generate route file'),
    });
  });

  test('maps work_unit.verified with verdict', () => {
    const event: MissionEvent = {
      missionId: 'mis-1',
      eventType: 'work_unit.verified',
      verdict: 'PASS',
      summary: 'health_router.py compiles',
      generatedAt: '2026-09-01T12:00:00Z',
    };
    const result = mapMissionEvent(event);
    expect(result?.type).toBe('progress_update');
    expect(result?.payload).toEqual({ verdict: 'PASS' });
  });

  test('maps work_unit.failed to risk_warning', () => {
    const event: MissionEvent = {
      missionId: 'mis-1',
      eventType: 'work_unit.failed',
      reason: 'compile error',
      generatedAt: '2026-09-01T12:00:00Z',
    };
    const result = mapMissionEvent(event);
    expect(result?.type).toBe('risk_warning');
  });

  test('maps evidence.recorded to progress_update', () => {
    const event: MissionEvent = {
      missionId: 'mis-1',
      eventType: 'evidence.recorded',
      summary: 'pytest 3/3 passed',
      verdict: 'PASS',
      generatedAt: '2026-09-01T12:00:00Z',
    };
    const result = mapMissionEvent(event);
    expect(result?.type).toBe('progress_update');
  });

  test('emits unknown events as system text (never drops)', () => {
    const event: MissionEvent = {
      missionId: 'mis-1',
      eventType: 'some.new.event',
      summary: 'new thing happened',
      generatedAt: '2026-09-01T12:00:00Z',
    };
    const result = mapMissionEvent(event);
    expect(result).not.toBeNull();
    expect(result?.type).toBe('system');
  });

  test('returns null when event has no useful fields', () => {
    const event: MissionEvent = { missionId: 'mis-1' };
    const result = mapMissionEvent(event);
    expect(result).toBeNull();
  });
});
