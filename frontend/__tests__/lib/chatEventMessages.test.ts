import { describe, expect, it } from 'vitest';
import {
  buildAgentTodoMessage,
  buildDeployCardMessage,
  buildSolutionProposalMessage,
  buildTaskPreviewMessage,
} from '../../lib/chatEventMessages';

describe('chatEventMessages helpers', () => {
  it('builds a task preview message', () => {
    const msg = buildTaskPreviewMessage('s1', {
      event: 'task_preview',
      sessionId: 's1',
      messageId: 'm1',
      timestamp: '2026-07-19T00:00:00.000Z',
      tasks: [],
    });

    expect(msg.type).toBe('task_preview');
    expect(msg.taskPreviewData?.messageId).toBe('m1');
    expect(msg.content).toBe('任务预览');
  });

  it('builds a solution proposal message', () => {
    const msg = buildSolutionProposalMessage('s1', {
      event: 'solution_proposal',
      sessionId: 's1',
      messageId: 'm2',
      timestamp: '2026-07-19T00:00:00.000Z',
      intentType: 'technical_development',
      requirements: [],
      nonFunctionalRequirements: [],
      constraints: [],
      solutions: [{ id: 'a', name: 'A', techStack: [], architecture: '', pros: [], cons: [], estimatedEffort: 'low', riskLevel: 'low', score: 1 }],
      recommendedSolutionId: 'a',
      recommendationReason: '',
      autoConfirmSeconds: 15,
    });

    expect(msg.type).toBe('solution_proposal');
    expect(msg.solutionProposalData?.messageId).toBe('m2');
  });

  it('builds agent todo and deploy messages', () => {
    const todo = buildAgentTodoMessage('s1', {
      event: 'agent_todo',
      sessionId: 's1',
      messageId: 'm3',
      timestamp: '2026-07-19T00:00:00.000Z',
      agentId: 'PM',
      title: 'Check',
      description: 'Do it',
      actions: [],
      priority: 'medium',
    });
    const deploy = buildDeployCardMessage('s1', {
      event: 'deploy_card',
      sessionId: 's1',
      messageId: 'm4',
      timestamp: '2026-07-19T00:00:00.000Z',
      version: '1.0.0',
      completedAt: '2026-07-19T00:00:00.000Z',
      description: 'Deployed',
      affectedFiles: [],
      agentId: 'Deploy',
    });

    expect(todo.type).toBe('agent_todo');
    expect(todo.content).toContain('Check');
    expect(deploy.type).toBe('deploy_card');
    expect(deploy.deployCardData?.version).toBe('1.0.0');
  });
});
