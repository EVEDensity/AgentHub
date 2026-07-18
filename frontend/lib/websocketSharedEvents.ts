import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import type {
  ChatSession,
  DegradationStatus,
  DiffDecisionEvent,
  DiffUpdateEvent,
  AgentQuestionEvent,
  AgentTodoEvent,
  DeployCardEvent,
  Message,
  PMState,
  PermissionModeChangedEvent,
  PresenceUpdateEvent,
  ProgressUpdateEvent,
  RiskWarningEvent,
  SolutionProposalEvent,
  TaskPreviewEvent,
  TerminalOutputEvent,
  TypingIndicatorEvent,
  UserJoinedEvent,
  UserLeftEvent,
  UserRosterEvent,
  WorkspacePreviewTab,
} from '../types';
import { getCollaborationStore } from './collaborationStore';
import { getPresenceStore } from './presenceStore';
import { updateSessionMessages } from './sessionStore';
import { setDagState, buildDagStateFromTaskPreview, updateDagState } from './dagStore';
import {
  buildAgentTodoMessage,
  buildDeployCardMessage,
  buildSolutionProposalMessage,
  buildTaskPreviewMessage,
} from './chatEventMessages';

type SessionSetState<T> = Dispatch<SetStateAction<T>>;

export interface SharedWebSocketEventDeps {
  wsRef: MutableRefObject<Map<string, WebSocket>>;
  setWorkspaceVersion: SessionSetState<number>;
  setPreviewTabs: SessionSetState<WorkspacePreviewTab[]>;
  setNotice: (msg: string) => void;
  setPmState: (state: PMState) => void;
  setDegradationStatus: (status: DegradationStatus | null) => void;
  setSessions: SessionSetState<ChatSession[]>;
  setIsAutoNaming: (value: boolean) => void;
  setExecPermission: (mode: 1 | 2 | 3) => void;
  sortSessions: (sessions: ChatSession[]) => ChatSession[];
}

function isPreviewChangePath(tab: WorkspacePreviewTab, changePath: string): boolean {
  return tab.path === changePath;
}

function appendMessage(sessionId: string, message: Message): void {
  updateSessionMessages(sessionId, (prev) => [...prev, message]);
}

export function handleSharedWebSocketEvent(
  raw: Record<string, unknown>,
  evt: string | undefined,
  chunkSessionId: string,
  deps: SharedWebSocketEventDeps,
): boolean {
  if (!evt) return false;

  if (evt === 'workspace_change') {
    deps.setWorkspaceVersion((v) => v + 1);
    const changePath = raw.path as string;
    if (changePath) {
      deps.setPreviewTabs((prev) => prev.map((tab) => (
        isPreviewChangePath(tab, changePath)
          ? { ...tab, _version: (tab._version || 0) + 1 }
          : tab
      )));
    }
    return true;
  }

  if (evt === 'file_conflict') {
    const conflictPath = raw.path as string;
    const backupPath = raw.backupPath as string;
    if (conflictPath) {
      deps.setNotice(
        `⚠️ 文件冲突: ${conflictPath} 被其他用户修改过` +
        (backupPath ? ` (原文件已备份为 ${backupPath})` : ''),
      );
    }
    deps.setWorkspaceVersion((v) => v + 1);
    return true;
  }

  if (evt === 'file_lock_change') {
    deps.setWorkspaceVersion((v) => v + 1);
    return true;
  }

  if (evt === 'task_update') {
    const tu = raw as {
      nodeId?: string;
      status?: string;
      detail?: { error?: string; retries?: number };
      progress?: { completed?: number; total?: number; failed?: number; running?: number; percent?: number };
      durationMs?: number;
      retries?: number;
    };
    if (tu.nodeId && tu.status) {
      updateDagState(chunkSessionId, {
        nodeId: tu.nodeId,
        status: tu.status,
        detail: tu.detail,
        progress: tu.progress,
        durationMs: tu.durationMs,
        retries: tu.retries,
      });
      return true;
    }
    return true;
  }

  if (evt === 'pm_state_change') {
    const payload = raw as { state?: PMState };
    if (payload.state) deps.setPmState(payload.state);
    return true;
  }

  if (evt === 'degradation_change') {
    const payload = raw as { status?: DegradationStatus | null };
    deps.setDegradationStatus(payload.status ?? null);
    return true;
  }

  if (evt === 'user_roster') {
    getPresenceStore().setRoster(chunkSessionId, (raw as unknown as UserRosterEvent).users);
    return true;
  }

  if (evt === 'user_joined') {
    const joined = raw as unknown as UserJoinedEvent;
    getPresenceStore().addUser(chunkSessionId, {
      userId: joined.userId,
      name: joined.userName,
      role: joined.role,
      status: 'online',
    });
    return true;
  }

  if (evt === 'user_left') {
    const left = raw as unknown as UserLeftEvent;
    getPresenceStore().removeUser(chunkSessionId, left.userId);
    getCollaborationStore().setTyping(chunkSessionId, left.userId, left.userName, false);
    return true;
  }

  if (evt === 'presence_update') {
    const pu = raw as unknown as PresenceUpdateEvent;
    getPresenceStore().bulkUpdateStatus(chunkSessionId, pu.users);
    return true;
  }

  if (evt === 'typing_indicator') {
    const ti = raw as unknown as TypingIndicatorEvent;
    getCollaborationStore().setTyping(chunkSessionId, ti.userId, ti.userName, ti.isTyping);
    return true;
  }

  if (evt === 'interaction_already_resolved') {
    const iar = raw as { messageId?: string; resolvedBy?: string; userName?: string };
    updateSessionMessages(chunkSessionId, (prev) => {
      const updated = [...prev];
      for (let i = updated.length - 1; i >= 0; i -= 1) {
        const m = updated[i];
        if ((m.messageId || m.id) === iar.messageId) {
          const resolver = { resolvedBy: iar.resolvedBy, resolvedByName: iar.userName };
          if (m.questionData) {
            updated[i] = { ...m, questionData: { ...m.questionData, ...resolver } };
          } else if (m.riskWarningData) {
            updated[i] = { ...m, riskWarningData: { ...m.riskWarningData, ...resolver } };
          } else if (m.todoData) {
            updated[i] = { ...m, todoData: { ...m.todoData, ...resolver } };
          } else if (m.taskPreviewData) {
            updated[i] = { ...m, taskPreviewData: { ...m.taskPreviewData, ...resolver } };
          }
          break;
        }
      }
      return updated;
    });
    return true;
  }

  if (evt === 'permission_mode_changed') {
    const pmc = raw as unknown as PermissionModeChangedEvent;
    if (pmc.mode === 1 || pmc.mode === 2 || pmc.mode === 3) {
      deps.setExecPermission(pmc.mode as 1 | 2 | 3);
    }
    return true;
  }

  if (evt === 'agent_question') {
    const payload = raw as unknown as AgentQuestionEvent;
    appendMessage(chunkSessionId, {
      event: 'message',
      sessionId: chunkSessionId,
      sender: payload.agentId || 'PM',
      content: payload.question,
      type: 'agent_question',
      timestamp: payload.timestamp,
      messageId: payload.messageId,
      questionData: payload,
    });
    return true;
  }

  if (evt === 'progress_update') {
    const payload = raw as unknown as ProgressUpdateEvent;
    updateSessionMessages(chunkSessionId, (prev) => {
      const existingIdx = prev.findIndex(
        (m) => m.messageId === payload.messageId && m.type === 'progress_update',
      );
      if (existingIdx >= 0) {
        const updated = [...prev];
        updated[existingIdx] = {
          ...updated[existingIdx],
          content: payload.currentStep,
          progressData: payload,
        };
        return updated;
      }
      return [
        ...prev,
        {
          event: 'message',
          sessionId: chunkSessionId,
          sender: payload.agentId || 'PM',
          content: payload.currentStep,
          type: 'progress_update',
          timestamp: payload.timestamp,
          messageId: payload.messageId,
          progressData: payload,
        },
      ];
    });
    return true;
  }

  if (evt === 'risk_warning') {
    const payload = raw as unknown as RiskWarningEvent;
    appendMessage(chunkSessionId, {
      event: 'message',
      sessionId: chunkSessionId,
      sender: payload.agentId || 'PM',
      content: `${payload.title}\n${payload.description}`,
      type: 'risk_warning',
      timestamp: payload.timestamp,
      messageId: payload.messageId,
      riskWarningData: payload,
    });
    return true;
  }

  if (evt === 'agent_todo') {
    appendMessage(chunkSessionId, buildAgentTodoMessage(chunkSessionId, raw as unknown as AgentTodoEvent));
    return true;
  }

  if (evt === 'task_preview') {
    const payload = raw as unknown as TaskPreviewEvent;
    setDagState(chunkSessionId, buildDagStateFromTaskPreview(payload));
    appendMessage(chunkSessionId, buildTaskPreviewMessage(chunkSessionId, payload));
    return true;
  }

  if (evt === 'solution_proposal') {
    appendMessage(chunkSessionId, buildSolutionProposalMessage(chunkSessionId, raw as unknown as SolutionProposalEvent));
    return true;
  }

  if (evt === 'deploy_card') {
    appendMessage(chunkSessionId, buildDeployCardMessage(chunkSessionId, raw as unknown as DeployCardEvent));
    return true;
  }

  if (evt === 'terminal_output') {
    const payload = raw as unknown as TerminalOutputEvent;
    updateSessionMessages(chunkSessionId, (prev) => {
      const existingIdx = prev.findIndex((m) => m.messageId === payload.messageId && m.type === 'terminal');
      if (existingIdx >= 0) {
        const updated = [...prev];
        updated[existingIdx] = {
          ...updated[existingIdx],
          content: updated[existingIdx].content + payload.content,
          isStreaming: true,
        };
        return updated;
      }
      return [
        ...prev,
        {
          event: 'message',
          sessionId: chunkSessionId,
          sender: payload.sender || 'system',
          content: payload.content,
          type: 'terminal',
          timestamp: payload.timestamp,
          messageId: payload.messageId,
          isStreaming: true,
        },
      ];
    });
    return true;
  }

  if (evt === 'diff_update') {
    const payload = raw as unknown as DiffUpdateEvent;
    appendMessage(chunkSessionId, {
      event: 'message',
      sessionId: chunkSessionId,
      sender: 'system',
      content: payload.diff,
      type: 'diff',
      timestamp: payload.timestamp,
      messageId: payload.messageId,
      diffFilePath: payload.path,
      diffDecisionState: 'pending',
    });
    return true;
  }

  if (evt === 'diff_decision') {
    const payload = raw as unknown as DiffDecisionEvent;
    updateSessionMessages(chunkSessionId, (prev) => {
      const updated = [...prev];
      for (let i = updated.length - 1; i >= 0; i -= 1) {
        if (updated[i].messageId === payload.messageId && updated[i].type === 'diff') {
          updated[i] = {
            ...updated[i],
            diffDecisionState: payload.decision === 'accept' ? 'accepted' : 'rejected',
          };
          const targetWs = deps.wsRef.current.get(chunkSessionId);
          if (targetWs && targetWs.readyState === WebSocket.OPEN) {
            targetWs.send(JSON.stringify({
              event: 'diff_decision',
              sessionId: payload.sessionId,
              messageId: payload.messageId,
              decision: payload.decision,
              path: payload.path,
            }));
          }
          break;
        }
      }
      return updated;
    });
    return true;
  }

  if (evt === 'session_renamed') {
    const payload = raw as { sessionId: string; name: string };
    deps.setSessions((prev) => deps.sortSessions(prev.map((s) => (s.id === payload.sessionId ? { ...s, name: payload.name } : s))));
    deps.setIsAutoNaming(false);
    return true;
  }

  return false;
}
