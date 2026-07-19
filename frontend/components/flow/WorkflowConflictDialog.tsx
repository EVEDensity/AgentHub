'use client';

import { AlertTriangle, Download, Upload } from 'lucide-react';
import type { JSX } from 'react';

import type { WorkflowConflict } from '../../hooks/useWorkflowEditorSession';
import { workflowDiffSummary } from '../../lib/workflowContract';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';

export function WorkflowConflictDialog({
  conflict,
  onClose,
  onReload,
  onOverwrite,
}: {
  conflict: WorkflowConflict | null;
  onClose: () => void;
  onReload: () => void | Promise<void>;
  onOverwrite: () => void | Promise<unknown>;
}): JSX.Element {
  const summary = conflict ? workflowDiffSummary(conflict.local, conflict.remote) : [];
  return (
    <Modal
      open={Boolean(conflict)}
      onClose={onClose}
      closeOnBackdrop={false}
      title={(
        <span className="flex items-center gap-2 text-sm font-semibold text-danger-600">
          <AlertTriangle size={17} /> 工作流版本冲突
        </span>
      )}
      size="lg"
      footer={(
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>继续编辑</Button>
          <Button variant="secondary" size="sm" onClick={() => void onReload()}>
            <Download size={14} /> 载入服务器版本
          </Button>
          <Button variant="danger" size="sm" onClick={() => void onOverwrite()}>
            <Upload size={14} /> 以本地版本覆盖
          </Button>
        </>
      )}
    >
      {conflict && (
        <div className="space-y-4 text-sm text-warm-600">
          <p>
            当前编辑基于版本 <strong>{conflict.expectedVersion}</strong>，服务器已更新到版本{' '}
            <strong>{conflict.currentVersion}</strong>。覆盖操作会先采用服务器最新版本号，再提交当前画布。
          </p>
          <div className="grid grid-cols-2 gap-3">
            <div className="border border-warm-200 bg-warm-50 p-3">
              <div className="text-xs font-semibold text-warm-700">本地版本</div>
              <div className="mt-2 text-xs">{conflict.local.nodes.length} 节点 · {conflict.local.edges.length} 连线</div>
            </div>
            <div className="border border-warm-200 bg-warm-50 p-3">
              <div className="text-xs font-semibold text-warm-700">服务器版本</div>
              <div className="mt-2 text-xs">{conflict.remote.nodes.length} 节点 · {conflict.remote.edges.length} 连线</div>
            </div>
          </div>
          <ul className="space-y-1 border-l-2 border-danger-300 pl-3 text-xs">
            {summary.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}
    </Modal>
  );
}
