'use client';

import {
  AssistantRuntimeProvider, MessageNotSentError, MessagePrimitive,
  ThreadPrimitive, useExternalStoreRuntime, type ThreadMessageLike,
} from '@assistant-ui/react';
import type { MissionDialogRow } from '../../lib/missionDialogProjection';

function convertMessage(row: MissionDialogRow): ThreadMessageLike {
  return {
    id: row.id, role: row.role, content: [{ type: 'text', text: row.text }],
    metadata: { custom: { label: row.label, missionId: row.missionId } },
  };
}

/** This adapter supplies display bytes only; it has no model, tools or commands. */
export function MissionObservationThread({ rows }: { rows: MissionDialogRow[] }) {
  const runtime = useExternalStoreRuntime<MissionDialogRow>({
    messages: rows, convertMessage, isRunning: false, isDisabled: true, unstable_enableToolInvocations: false,
    onNew: async () => { throw new MessageNotSentError('请使用 Mission 提交入口'); },
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="min-h-0 flex-1" role="region" aria-label="Mission 对话观察">
        <ThreadPrimitive.Viewport role="log" aria-live="polite" aria-relevant="additions" className="h-full max-h-[36vh] min-h-32 space-y-3 overflow-y-auto rounded border border-warm-200 p-3">
          <ThreadPrimitive.Messages>{({ message }) => (
            <MessagePrimitive.Root className="rounded border border-warm-200 bg-warm-50 p-3">
              <p className="mb-1 break-all text-xs font-medium text-warm-600">{String(message.metadata.custom.label ?? '观察')}</p>
              {typeof message.metadata.custom.missionId === 'string' && <p className="mb-1 break-all font-mono text-xs text-warm-600">{message.metadata.custom.missionId}</p>}
              <div className="whitespace-pre-wrap break-words text-sm text-warm-900">{message.content.map((part, index) => part.type === 'text' ? <span key={index}>{part.text}</span> : null)}</div>
            </MessagePrimitive.Root>
          )}</ThreadPrimitive.Messages>
          {rows.length === 0 && <p className="text-sm text-warm-600">尚无提交或观察。提交后等待真实 Runner，模型文本不会替代独立验收。</p>}
        </ThreadPrimitive.Viewport>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
