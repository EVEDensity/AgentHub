'use client';

import { useEffect, useState, type JSX } from 'react';
import { useWorkspaceStore } from '../../stores/workspaceStore';

export function WorkspaceSelector(): JSX.Element {
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const currentWorkspaceId = useWorkspaceStore((s) => s.currentWorkspaceId);
  const loadWorkspaces = useWorkspaceStore((s) => s.loadWorkspaces);
  const setCurrentWorkspaceId = useWorkspaceStore((s) => s.setCurrentWorkspaceId);

  const [open, setOpen] = useState(false);

  useEffect(() => {
    void loadWorkspaces();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const current = workspaces.find((w) => w.id === currentWorkspaceId);

  return (
    <div className="relative">
      <button
        className="flex items-center gap-1.5 text-xs text-warm-300 hover:text-warm-100 transition-colors"
        onClick={() => setOpen(!open)}
      >
        <span className="material-symbols-outlined text-[14px]">apartment</span>
        <span className="truncate max-w-[120px]">{current?.name || '选择工作空间'}</span>
        <span className="material-symbols-outlined text-[12px]">{open ? 'expand_less' : 'expand_more'}</span>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute top-full left-0 mt-1 z-20 bg-warm-100 rounded-lg shadow-xl border border-warm-200 min-w-[200px] overflow-hidden">
            <div className="py-1">
              {workspaces.map((ws) => (
                <button
                  key={ws.id}
                  className={`w-full text-left px-3 py-2 text-xs flex items-center gap-2 hover:bg-warm-50 transition-colors ${
                    ws.id === currentWorkspaceId ? 'bg-primary-50 text-primary-600' : 'text-warm-700'
                  }`}
                  onClick={() => { setCurrentWorkspaceId(ws.id); setOpen(false); }}
                >
                  <span className="material-symbols-outlined text-[14px]">
                    {ws.id === currentWorkspaceId ? 'check_circle' : 'apartment'}
                  </span>
                  <div className="min-w-0">
                    <div className="truncate font-medium">{ws.name}</div>
                    <div className="text-[10px] text-warm-400">{ws.member_count} 成员</div>
                  </div>
                </button>
              ))}
            </div>
            <div className="border-t border-warm-100 px-2 py-1.5">
              <a
                href="/admin?menu=工作空间"
                className="text-[10px] text-primary-500 hover:underline"
                onClick={() => setOpen(false)}
              >
                管理工作空间...
              </a>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
