import { describe, expect, it } from 'vitest';
import { buildOutgoingMessageDraft } from '../../lib/outgoingMessageDraft';

describe('buildOutgoingMessageDraft', () => {
  it('builds ai content and attachment metadata from files and references', () => {
    const draft = buildOutgoingMessageDraft({
      text: 'Please review',
      files: [
        {
          name: 'hello.py',
          size: 120,
          type: 'text/x-python',
          category: 'code',
          content: 'print("hi")',
        },
      ] as any,
      references: [
        {
          id: 'ref-1',
          path: 'src/app.py',
          quote: 'def main(): pass',
          originalSender: 'user',
          originalTimestamp: '2026-07-19T00:00:00.000Z',
          isFullMessage: false,
          lineStart: 10,
          lineEnd: 12,
        } as any,
      ],
    });

    expect(draft.displayContent).toBe('Please review');
    expect(draft.attachments).toEqual([
      {
        name: 'hello.py',
        size: 120,
        type: 'text/x-python',
        category: 'code',
        fileId: undefined,
      },
    ]);
    expect(draft.aiContent).toContain('[Attached File: hello.py]');
    expect(draft.aiContent).toContain('[Referenced File: src/app.py]');
  });
});
