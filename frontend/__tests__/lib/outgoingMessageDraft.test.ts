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

  it('never splices image base64 into the body (MM-2 / ADR-0105)', () => {
    const dataUrl = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==';
    const draft = buildOutgoingMessageDraft({
      text: '看看这张图',
      files: [
        {
          name: 'logo.png',
          size: 1024,
          type: 'image/png',
          category: 'image',
          content: dataUrl,
        },
      ] as any,
      references: [],
    });

    // body keeps a descriptive marker only — payload must ride attachments
    expect(draft.aiContent).toBe('看看这张图\n\n---\n[Attached Image: logo.png]');
    expect(draft.aiContent).not.toContain('base64');
    expect(draft.aiContent).not.toContain(dataUrl);
    // structured attachment still carries the inline payload for the backend
    expect(draft.attachments[0]).toMatchObject({
      name: 'logo.png',
      type: 'image/png',
      category: 'image',
    });
  });
});
