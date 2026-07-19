import type { AgentTodoEvent, DeployCardEvent, Message, SolutionProposalEvent, TaskPreviewEvent } from '../types';

function baseMessage(
  sessionId: string,
  payload: { timestamp: string; messageId: string; sender: string; content: string; type: Message['type'] },
): Message {
  return {
    event: 'message',
    sessionId,
    sender: payload.sender,
    content: payload.content,
    type: payload.type,
    timestamp: payload.timestamp,
    messageId: payload.messageId,
  };
}

export function buildAgentTodoMessage(sessionId: string, payload: AgentTodoEvent): Message {
  return {
    ...baseMessage(sessionId, {
      timestamp: payload.timestamp,
      messageId: payload.messageId,
      sender: payload.agentId || 'PM',
      content: `${payload.title}\n${payload.description}`,
      type: 'agent_todo',
    }),
    todoData: payload,
  };
}

export function buildTaskPreviewMessage(sessionId: string, payload: TaskPreviewEvent): Message {
  return {
    ...baseMessage(sessionId, {
      timestamp: payload.timestamp,
      messageId: payload.messageId,
      sender: 'system',
      content: '任务预览',
      type: 'task_preview',
    }),
    taskPreviewData: payload,
  };
}

export function buildSolutionProposalMessage(sessionId: string, payload: SolutionProposalEvent): Message {
  return {
    ...baseMessage(sessionId, {
      timestamp: payload.timestamp,
      messageId: payload.messageId,
      sender: 'Orchestrator',
      content: `方案分析 — ${payload.solutions.length} 个方案`,
      type: 'solution_proposal',
    }),
    solutionProposalData: payload,
  };
}

export function buildDeployCardMessage(sessionId: string, payload: DeployCardEvent): Message {
  return {
    ...baseMessage(sessionId, {
      timestamp: payload.timestamp,
      messageId: payload.messageId,
      sender: payload.agentId || 'Deploy',
      content: payload.description || '部署完成',
      type: 'deploy_card',
    }),
    deployCardData: payload,
  };
}
