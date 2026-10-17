import type { ChatRenderEvent } from './missionEventMapper';

export const MISSION_DIALOG_ROW_LIMIT = 200;
const OBSERVATION_TEXT_LIMIT = 4000;

/** Ephemeral presentation of a submitted request or a server ledger observation. */
export interface MissionDialogRow {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  label: string;
  missionId?: string;
}

export interface MissionDialogProjection {
  rows: MissionDialogRow[];
  omitted: boolean;
}

export function appendMissionDialogRows(previous: MissionDialogProjection, incoming: MissionDialogRow[]): MissionDialogProjection {
  const seen = new Set(previous.rows.map(row => row.id));
  const additions = incoming.filter(row => {
    if (seen.has(row.id)) return false;
    seen.add(row.id);
    return true;
  });
  if (additions.length === 0) return previous;
  const rows = [...previous.rows, ...additions];
  return { rows: rows.slice(-MISSION_DIALOG_ROW_LIMIT), omitted: previous.omitted || rows.length > MISSION_DIALOG_ROW_LIMIT };
}

export function projectMissionDialogEvents(events: ChatRenderEvent[], missionId: string | null): MissionDialogRow[] {
  return events.flatMap(event => {
    if (!missionId || event.missionId !== missionId || !event.eventId || !event.eventType) return [];
    const text = Array.from(event.content);
    return [{
      id: `observation:${missionId}:${event.eventId}`,
      role: 'assistant' as const,
      text: text.length > OBSERVATION_TEXT_LIMIT ? text.slice(0, OBSERVATION_TEXT_LIMIT).join('') + '\n[观察文本已截断]' : event.content,
      label: `观察 · ${event.eventType}`,
      missionId,
    }];
  });
}
