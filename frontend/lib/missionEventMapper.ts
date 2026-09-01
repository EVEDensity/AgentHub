/**
 * Mission-event-to-chat-event mapper.
 *
 * The v1 Mission event ledger streams domain events (work-unit
 * lifecycle, evidence, decisions, artifacts). This module maps those
 * events into the simplified chat-event surface that the UI already
 * knows how to render — tool_call, progress_update, solution, etc.
 *
 * Keeping this mapper thin avoids reworking every bubble component;
 * the mapper is the single place where the Mission domain model and
 * the chat presentation model meet.
 */

import type { MissionEvent } from '../../types';

export interface ChatRenderEvent {
  /** Mission/chat turn id — useful for grouping bubbles. */
  missionId: string;
  /** Chat bubble type. */
  type:
    | 'progress_update'
    | 'tool_call'
    | 'tool_result'
    | 'solution'
    | 'risk_warning'
    | 'agent_question'
    | 'agent_todo'
    | 'text'
    | 'system';
  /** Human-readable text for the bubble body. */
  content: string;
  /** When this event occurred (ISO-8601). */
  timestamp: string;
  /** Optional structured payload the bubble can render richer. */
  payload?: Record<string, unknown>;
}

/** Map one Mission ledger event to a chat render event. */
export function mapMissionEvent(
  event: MissionEvent,
): ChatRenderEvent | null {
  const missionId = String(event.missionId ?? event.aggregateId ?? '');
  const timestamp = String(event.generatedAt ?? event.timestamp ?? '');

  switch (event.eventType ?? event.type ?? '') {
    case 'work_unit.started':
      return {
        missionId,
        type: 'progress_update',
        timestamp,
        content: `▶️ ${event.title ?? event.summary ?? 'Work unit started'}`,
        payload: { workUnitId: event.workUnitId ?? event.aggregateId },
      };
    case 'work_unit.completed':
      return {
        missionId,
        type: 'progress_update',
        timestamp,
        content: `✅ ${event.title ?? event.summary ?? 'Work unit completed'}`,
      };
    case 'work_unit.verified':
      return {
        missionId,
        type: 'progress_update',
        timestamp,
        content: `🔍 Verified: ${event.verdict ?? event.summary ?? 'evidence recorded'}`,
        payload: { verdict: event.verdict },
      };
    case 'work_unit.failed':
      return {
        missionId,
        type: 'risk_warning',
        timestamp,
        content: `❌ ${event.title ?? event.summary ?? 'Work unit failed'}`,
        payload: { reason: event.reason ?? event.summary },
      };
    case 'evidence.recorded':
      return {
        missionId,
        type: 'progress_update',
        timestamp,
        content: `📋 Evidence: ${event.summary ?? event.title ?? 'evidence recorded'}`,
        payload: { verdict: event.verdict },
      };
    case 'artifact.registered':
      return {
        missionId,
        type: 'tool_result',
        timestamp,
        content: `📦 Artifact: ${event.title ?? event.contentAddress ?? ''}`,
        payload: { contentAddress: event.contentAddress ?? event.content_address },
      };
    case 'decision.created':
      return {
        missionId,
        type: 'agent_question',
        timestamp,
        content: `❓ ${event.summary ?? event.title ?? 'Decision pending'}`,
        payload: { decisionId: event.decisionId },
      };
    default:
      // Unrecognised event — still emit as system text so the ledger
      // is never silently dropped during a stream replay.
      const text = String(event.summary ?? event.title ?? '').trim();
      if (!text && !event.eventType && !event.type) {
        return null;
      }
      const body = text || String(event.eventType ?? event.type ?? 'unknown');
      return {
        missionId,
        type: 'system',
        timestamp,
        content: body,
      };
  }
}
