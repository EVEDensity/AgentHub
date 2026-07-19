'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  cloneWorkflow,
  EMPTY_WORKFLOW,
  normalizeWorkflowDocument,
  type WorkflowDocument,
  type WorkflowValidationIssue,
} from '../lib/workflowContract';
import {
  readConflictDetail,
  WorkflowApiError,
  workflowClient,
} from '../lib/workflowClient';

export type DraftStatus = 'loading' | 'clean' | 'dirty' | 'saving' | 'saved' | 'error';

export interface WorkflowConflict {
  kind: 'recovery' | 'workflow' | 'draft';
  local: WorkflowDocument;
  remote: WorkflowDocument;
  expectedVersion: number;
  currentVersion: number;
}

export function workflowDraftKey(workflowId?: number): string {
  return workflowId ? `workflow-${workflowId}` : 'new-workflow';
}

export function useWorkflowEditorSession(workflowId?: number) {
  const [currentWorkflowId, setCurrentWorkflowId] = useState(workflowId);
  const [document, setDocument] = useState<WorkflowDocument>(() => cloneWorkflow(EMPTY_WORKFLOW));
  const [issues, setIssues] = useState<WorkflowValidationIssue[]>([]);
  const [status, setStatus] = useState<DraftStatus>('loading');
  const [message, setMessage] = useState('');
  const [conflict, setConflict] = useState<WorkflowConflict | null>(null);
  const [ready, setReady] = useState(false);

  const draftKey = useMemo(() => workflowDraftKey(currentWorkflowId), [currentWorkflowId]);
  const draftVersionRef = useRef(0);
  const baseVersionRef = useRef(0);
  const lastPersistedRef = useRef('');
  const pendingSaveRef = useRef<WorkflowDocument | null>(null);
  const savingRef = useRef(false);

  const applyDocument = useCallback((next: WorkflowDocument, markPersisted = false) => {
    const normalized = normalizeWorkflowDocument(next);
    setDocument(normalized);
    if (markPersisted) lastPersistedRef.current = JSON.stringify(normalized);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setReady(false);
    setStatus('loading');
    setCurrentWorkflowId(workflowId);
    const key = workflowDraftKey(workflowId);

    void (async () => {
      try {
        const [server, draft] = await Promise.all([
          workflowId ? workflowClient.getWorkflow(workflowId) : Promise.resolve(null),
          workflowClient.getDraft(key),
        ]);
        if (cancelled) return;
        const base = server || cloneWorkflow(EMPTY_WORKFLOW);
        baseVersionRef.current = server?.version || 0;
        if (draft) {
          draftVersionRef.current = draft.draftVersion;
          setIssues(draft.validation.issues || []);
          const recovered = normalizeWorkflowDocument({ ...draft.payload, id: workflowId || draft.payload.id });
          applyDocument(recovered, true);
          if (server && draft.baseVersion !== server.version) {
            setConflict({
              kind: 'recovery',
              local: recovered,
              remote: server,
              expectedVersion: draft.baseVersion,
              currentVersion: server.version,
            });
          } else {
            setMessage('已恢复上次草稿');
          }
        } else {
          draftVersionRef.current = 0;
          applyDocument(base, true);
          setIssues([]);
        }
        setStatus('clean');
        setReady(true);
      } catch (error) {
        if (cancelled) return;
        setStatus('error');
        setMessage(error instanceof Error ? error.message : '工作流加载失败');
        setReady(true);
      }
    })();
    return () => { cancelled = true; };
  }, [applyDocument, workflowId]);

  const flushDraftQueue = useCallback(async () => {
    if (savingRef.current) return;
    savingRef.current = true;
    try {
      while (pendingSaveRef.current) {
        const snapshot = pendingSaveRef.current;
        pendingSaveRef.current = null;
        setStatus('saving');
        try {
          const saved = await workflowClient.saveDraft(
            workflowDraftKey(snapshot.id),
            snapshot,
            baseVersionRef.current,
            draftVersionRef.current,
          );
          draftVersionRef.current = saved.draftVersion;
          lastPersistedRef.current = JSON.stringify(snapshot);
          setIssues(saved.validation.issues || []);
          setStatus('saved');
          setMessage('草稿已保存');
        } catch (error) {
          if (error instanceof WorkflowApiError && error.status === 409) {
            const detail = readConflictDetail(error.detail);
            const remoteDraft = await workflowClient.getDraft(workflowDraftKey(snapshot.id));
            setConflict({
              kind: 'draft',
              local: snapshot,
              remote: remoteDraft?.payload || snapshot,
              expectedVersion: detail?.expectedVersion ?? draftVersionRef.current,
              currentVersion: detail?.currentVersion ?? remoteDraft?.draftVersion ?? 0,
            });
          }
          setStatus('error');
          setMessage(error instanceof Error ? error.message : '草稿保存失败');
          pendingSaveRef.current = null;
        }
      }
    } finally {
      savingRef.current = false;
    }
  }, []);

  useEffect(() => {
    if (!ready) return;
    const fingerprint = JSON.stringify(document);
    if (fingerprint === lastPersistedRef.current) return;
    setStatus('dirty');
    const timer = window.setTimeout(() => {
      pendingSaveRef.current = cloneWorkflow(document);
      void flushDraftQueue();
    }, 800);
    return () => window.clearTimeout(timer);
  }, [document, flushDraftQueue, ready]);

  const publishWithVersion = useCallback(async (expectedVersion: number) => {
    let validation;
    try {
      validation = await workflowClient.validate(document);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '工作流校验失败');
      return false;
    }
    setIssues(validation.issues || []);
    if (!validation.valid) {
      setMessage('请先修复画布中的校验问题');
      return false;
    }
    try {
      const saved = await workflowClient.publish(document, expectedVersion);
      const oldDraftKey = workflowDraftKey(document.id);
      baseVersionRef.current = saved.version;
      draftVersionRef.current = 0;
      setCurrentWorkflowId(saved.id);
      applyDocument(saved, true);
      setConflict(null);
      setStatus('clean');
      setMessage('工作流已发布');
      await workflowClient.deleteDraft(oldDraftKey).catch(() => undefined);
      if (!document.id && saved.id && typeof window !== 'undefined') {
        window.history.replaceState(null, '', `/canvas?id=${saved.id}`);
      }
      return true;
    } catch (error) {
      if (error instanceof WorkflowApiError && error.status === 409 && document.id) {
        const detail = readConflictDetail(error.detail);
        const remote = await workflowClient.getWorkflow(document.id);
        setConflict({
          kind: 'workflow',
          local: cloneWorkflow(document),
          remote,
          expectedVersion: detail?.expectedVersion ?? expectedVersion,
          currentVersion: detail?.currentVersion ?? remote.version,
        });
      }
      setMessage(error instanceof Error ? error.message : '发布失败');
      return false;
    }
  }, [applyDocument, document]);

  const publish = useCallback(() => publishWithVersion(document.version), [document.version, publishWithVersion]);

  const discardDraft = useCallback(async () => {
    try {
      await workflowClient.deleteDraft(draftKey).catch(() => undefined);
      const base = currentWorkflowId
        ? await workflowClient.getWorkflow(currentWorkflowId)
        : cloneWorkflow(EMPTY_WORKFLOW);
      draftVersionRef.current = 0;
      baseVersionRef.current = base.version;
      setIssues([]);
      setConflict(null);
      applyDocument(base, true);
      setStatus('clean');
      setMessage('草稿已丢弃');
    } catch (error) {
      setStatus('error');
      setMessage(error instanceof Error ? error.message : '草稿丢弃失败');
    }
  }, [applyDocument, currentWorkflowId, draftKey]);

  const reloadConflictRemote = useCallback(async () => {
    if (!conflict) return;
    if (conflict.kind !== 'draft') {
      await workflowClient.deleteDraft(draftKey).catch(() => undefined);
      draftVersionRef.current = 0;
      baseVersionRef.current = conflict.remote.version;
    } else {
      draftVersionRef.current = conflict.currentVersion;
    }
    applyDocument(conflict.remote, true);
    setConflict(null);
    setStatus('clean');
    setMessage('已载入服务器版本');
  }, [applyDocument, conflict, draftKey]);

  const overwriteConflict = useCallback(async () => {
    if (!conflict) return false;
    if (conflict.kind === 'draft') {
      draftVersionRef.current = conflict.currentVersion;
      pendingSaveRef.current = cloneWorkflow(conflict.local);
      setConflict(null);
      await flushDraftQueue();
      return true;
    }
    setConflict(null);
    return publishWithVersion(conflict.currentVersion);
  }, [conflict, flushDraftQueue, publishWithVersion]);

  return {
    document,
    setDocument,
    issues,
    status,
    message,
    conflict,
    ready,
    draftKey,
    publish,
    discardDraft,
    reloadConflictRemote,
    overwriteConflict,
    dismissConflict: () => setConflict(null),
  };
}
