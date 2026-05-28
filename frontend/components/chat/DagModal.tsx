import { memo } from 'react';
import type { DagState } from '../../types';

interface DagModalProps {
  dag: DagState;
  onClose: () => void;
}

const DagModal = memo(function DagModal({ dag, onClose }: DagModalProps) {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-warm-900/20">
      <div className="w-[520px] rounded-xl bg-white p-6 shadow-modal">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-h3 text-warm-800">DAG Task Details</h3>
          <button className="btn-ghost p-1 text-warm-500" onClick={onClose}>X</button>
        </div>
        <div className="space-y-3">
          {dag.nodes.map((n, i) => (
            <div key={n.id || i} className="flex items-center gap-3 rounded-lg bg-warm-50 px-4 py-3">
              <span className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-medium ${
                n.status === 'completed' ? 'bg-success-50 text-success-500' :
                n.status === 'running' ? 'bg-primary-50 text-primary-500' :
                'bg-warm-100 text-warm-500'
              }`}>
                {n.status === 'completed' ? 'OK' : n.status === 'running' ? 'R' : i + 1}
              </span>
              <span className="text-body flex-1 text-warm-700">{n.agent || n.name || `Task ${i + 1}`}</span>
              <span className="tag tag-warm">{n.status || 'pending'}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
});

export default DagModal;
