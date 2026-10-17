import { describe, expect, it } from 'vitest';
import { appendMissionDialogRows, projectMissionDialogEvents } from '../../lib/missionDialogProjection';
import type { ChatRenderEvent } from '../../lib/missionEventMapper';

function event(index: number, updates: Partial<ChatRenderEvent> = {}): ChatRenderEvent {
  return { missionId: 'mission-1', eventId: `event-${index}`, eventType: 'harness.assistant.delta', type: 'text', content: `观察 ${index}`, timestamp: '', ...updates };
}

describe('Mission dialog projection', () => {
  it('uses ledger IDs and labels text, tool and completed records as observations', () => {
    const rows = projectMissionDialogEvents([
      event(1, { content: '<script>window.PWNED=true</script>' }),
      event(2, { eventType: 'harness.tool.completed', type: 'tool_result', content: 'command said PASS' }),
      event(3, { eventType: 'work_unit.completed' }),
    ], 'mission-1');
    expect(rows.map(row => row.id)).toEqual(['observation:mission-1:event-1', 'observation:mission-1:event-2', 'observation:mission-1:event-3']);
    expect(rows.every(row => row.label.startsWith('观察 · '))).toBe(true);
    expect(rows[0].text).toBe('<script>window.PWNED=true</script>');
    expect(rows.some(row => Object.hasOwn(row, 'verdict'))).toBe(false);
  });

  it('excludes anonymous and foreign Mission records without inventing identities', () => {
    expect(projectMissionDialogEvents([event(1, { eventId: undefined }), event(2, { missionId: 'foreign' }), event(3, { eventType: undefined })], 'mission-1')).toEqual([]);
    expect(projectMissionDialogEvents([event(1)], null)).toEqual([]);
  });

  it('deduplicates replayed IDs, bounds rows and reports omitted history', () => {
    const rows = projectMissionDialogEvents(Array.from({ length: 205 }, (_, index) => event(index)), 'mission-1');
    const projection = appendMissionDialogRows({ rows: [], omitted: false }, rows);
    expect(projection.rows).toHaveLength(200);
    expect(projection.rows[0].id).toBe('observation:mission-1:event-5');
    expect(projection.omitted).toBe(true);
    expect(appendMissionDialogRows(projection, rows.slice(-3))).toBe(projection);
  });

  it('bounds visible Unicode text without splitting surrogate pairs', () => {
    const [row] = projectMissionDialogEvents([event(1, { content: '😀'.repeat(4001) })], 'mission-1');
    expect(row.text).toBe('😀'.repeat(4000) + '\n[观察文本已截断]');
  });
});
