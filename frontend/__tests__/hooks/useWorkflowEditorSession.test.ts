import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useWorkflowEditorSession } from '../../hooks/useWorkflowEditorSession';

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const serverWorkflow = {
  id: 7,
  name: 'Server workflow',
  description: '',
  triggerKeywords: [],
  nodes: [{ id: 'agent', type: 'agent', name: 'Agent', description: '', x: 0, y: 0, agent: 'CodeGen', dependencies: [] }],
  edges: [],
  isDefault: false,
  active: true,
  version: 2,
  schemaVersion: 1,
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('useWorkflowEditorSession', () => {
  it('recovers a persisted draft on refresh without mixing it with the server graph', async () => {
    const draft = {
      draftKey: 'workflow-7', workflowId: 7, baseVersion: 2, draftVersion: 3,
      createdAt: '', updatedAt: '',
      payload: { ...serverWorkflow, name: 'Recovered draft', nodes: [...serverWorkflow.nodes, { id: 'review', type: 'human', name: 'Review', description: '', x: 300, y: 0, dependencies: ['agent'], humanConfig: { prompt: 'Review' } }], edges: [{ id: 'agent->review', from: 'agent', to: 'review' }] },
      validation: { valid: true, normalized: {}, issues: [] },
    };
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/drafts/workflow-7')) return Promise.resolve(response(draft));
      if (url.endsWith('/workflows/7')) return Promise.resolve(response(serverWorkflow));
      throw new Error(`Unexpected request: ${url}`);
    }));

    const { result } = renderHook(() => useWorkflowEditorSession(7));

    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.document.name).toBe('Recovered draft');
    expect(result.current.document.nodes.map((node) => node.id)).toEqual(['agent', 'review']);
    expect(result.current.message).toBe('已恢复上次草稿');
  });

  it('debounces graph changes and persists a versioned draft', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/drafts/new-workflow') && !init?.method) return Promise.resolve(response({}, 404));
      if (url.endsWith('/drafts/new-workflow') && init?.method === 'PUT') {
        const payload = JSON.parse(String(init.body));
        return Promise.resolve(response({
          draftKey: 'new-workflow', baseVersion: 0, draftVersion: 1,
          createdAt: '', updatedAt: '', payload,
          validation: { valid: true, normalized: {}, issues: [] },
        }));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useWorkflowEditorSession());
    await waitFor(() => expect(result.current.ready).toBe(true));

    act(() => result.current.setDocument({ ...result.current.document, name: 'Autosaved' }));

    await waitFor(() => expect(result.current.status).toBe('saved'), { timeout: 2000 });
    const saveCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT');
    expect(saveCall).toBeTruthy();
    expect(JSON.parse(String(saveCall?.[1]?.body)).draftVersion).toBe(0);
  });

  it('opens a compare conflict when publish sees a stale workflow version', async () => {
    let workflowReads = 0;
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/drafts/workflow-7')) return Promise.resolve(response({}, 404));
      if (url.endsWith('/validate')) return Promise.resolve(response({ valid: true, normalized: {}, issues: [] }));
      if (url.endsWith('/workflows/7') && init?.method === 'PUT') {
        return Promise.resolve(response({ detail: { code: 'workflow_version_conflict', message: 'conflict', expectedVersion: 2, currentVersion: 3 } }, 409));
      }
      if (url.endsWith('/workflows/7')) {
        workflowReads += 1;
        return Promise.resolve(response({ ...serverWorkflow, name: workflowReads > 1 ? 'Remote update' : serverWorkflow.name, version: workflowReads > 1 ? 3 : 2 }));
      }
      throw new Error(`Unexpected request: ${url}`);
    }));
    const { result } = renderHook(() => useWorkflowEditorSession(7));
    await waitFor(() => expect(result.current.ready).toBe(true));

    await act(async () => { await result.current.publish(); });

    expect(result.current.conflict?.kind).toBe('workflow');
    expect(result.current.conflict?.currentVersion).toBe(3);
    expect(result.current.conflict?.remote.name).toBe('Remote update');
  });
});
