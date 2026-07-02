'use client';

import { useEffect, useState, type JSX } from 'react';
import { useWorkspaceStore } from '../../stores/workspaceStore';
import { WorkspaceInviteModal } from './WorkspaceInviteModal';

interface Props {
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
}

export default function WorkspaceManager({ authHeaders: _authHeaders, setNotice: _setNotice }: Props): JSX.Element {
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const currentWorkspaceId = useWorkspaceStore((s) => s.currentWorkspaceId);
  const members = useWorkspaceStore((s) => s.members);
  const isLoading = useWorkspaceStore((s) => s.isLoading);

  const loadWorkspaces = useWorkspaceStore((s) => s.loadWorkspaces);
  const createWorkspace = useWorkspaceStore((s) => s.createWorkspace);
  const deleteWorkspace = useWorkspaceStore((s) => s.deleteWorkspace);
  const loadMembers = useWorkspaceStore((s) => s.loadMembers);
  const removeMember = useWorkspaceStore((s) => s.removeMember);
  const setCurrentWorkspaceId = useWorkspaceStore((s) => s.setCurrentWorkspaceId);

  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [showInvite, setShowInvite] = useState(false);

  useEffect(() => {
    void loadWorkspaces();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (currentWorkspaceId) void loadMembers(currentWorkspaceId);
  }, [currentWorkspaceId]); // eslint-disable-line react-hooks/exhaustive-deps

  const current = workspaces.find((w) => w.id === currentWorkspaceId);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    await createWorkspace(newName.trim(), newDesc.trim());
    setNewName('');
    setNewDesc('');
    setShowCreate(false);
  };

  return (
    <div className="flex flex-col h-full gap-3">
      <div className="flex items-center gap-3">
        <h3 className="text-sm font-semibold text-warm-700">工作空间管理</h3>
        <div className="flex-1" />
        <button className="btn-primary text-xs" onClick={() => setShowCreate(!showCreate)}>
          <span className="material-symbols-outlined text-[14px]">add</span>
          新建工作空间
        </button>
      </div>

      {/* Create form */}
      {showCreate && (
        <div className="card p-4 space-y-3">
          <input
            className="input-field w-full text-sm"
            placeholder="工作空间名称"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleCreate(); }}
          />
          <input
            className="input-field w-full text-sm"
            placeholder="描述（可选）"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
          />
          <div className="flex justify-end gap-2">
            <button className="btn-ghost text-xs" onClick={() => setShowCreate(false)}>取消</button>
            <button className="btn-primary text-xs" onClick={handleCreate} disabled={!newName.trim()}>
              创建
            </button>
          </div>
        </div>
      )}

      {/* Two-column layout */}
      <div className="grid grid-cols-[280px_1fr] gap-4 flex-1 min-h-0">
        {/* Left: workspace list */}
        <div className="card overflow-y-auto">
          <div className="text-xs text-warm-400 mb-2">{workspaces.length} 个工作空间</div>
          {workspaces.map((ws) => (
            <div
              key={ws.id}
              className={`flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ${
                currentWorkspaceId === ws.id
                  ? 'bg-primary-50 border border-primary-200'
                  : 'hover:bg-warm-50 border border-transparent'
              }`}
              onClick={() => setCurrentWorkspaceId(ws.id)}
            >
              <span className="material-symbols-outlined text-[16px] text-primary-500 shrink-0">apartment</span>
              <div className="min-w-0 flex-1">
                <div className="text-xs font-medium text-warm-700 truncate">{ws.name}</div>
                <div className="text-[10px] text-warm-400">{ws.member_count} 成员</div>
              </div>
              {ws.id !== 'ws-default' && (
                <button
                  className="shrink-0 text-warm-300 hover:text-danger-500"
                  onClick={(e) => { e.stopPropagation(); void deleteWorkspace(ws.id); }}
                >
                  <span className="material-symbols-outlined text-[14px]">delete</span>
                </button>
              )}
            </div>
          ))}
        </div>

        {/* Right: workspace detail */}
        <div className="card overflow-y-auto">
          {!current ? (
            <div className="text-center py-12 text-xs text-warm-400">选择一个工作空间</div>
          ) : (
            <div className="space-y-4">
              <div>
                <h4 className="text-sm font-semibold text-warm-700">{current.name}</h4>
                {current.description && (
                  <p className="text-xs text-warm-400 mt-1">{current.description}</p>
                )}
                <div className="flex items-center gap-3 mt-2 text-[10px] text-warm-400">
                  <span>ID: {current.id}</span>
                  <span>创建时间: {current.created_at ? new Date(current.created_at).toLocaleDateString() : '-'}</span>
                </div>
              </div>

              {/* Members section */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <h5 className="text-xs font-semibold text-warm-600">成员 ({members.length})</h5>
                  <button className="btn-ghost text-[10px]" onClick={() => setShowInvite(true)}>
                    <span className="material-symbols-outlined text-[12px]">person_add</span>
                    邀请
                  </button>
                </div>
                {members.length === 0 ? (
                  <div className="text-center py-6 text-xs text-warm-400">暂无成员</div>
                ) : (
                  <div className="space-y-1">
                    {members.map((m) => (
                      <div key={m.user_id} className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-warm-50">
                        <div className="w-7 h-7 rounded-full bg-primary-100 flex items-center justify-center text-xs font-medium text-primary-600">
                          {(m.display_name || m.email)[0]?.toUpperCase()}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="text-xs font-medium text-warm-700">{m.display_name || m.email}</div>
                          <div className="text-[10px] text-warm-400">{m.email}</div>
                        </div>
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                          m.role === 'admin' ? 'bg-primary-50 text-primary-600' :
                          m.role === 'editor' ? 'bg-success-50 text-success-600' :
                          'bg-warm-100 text-warm-500'
                        }`}>
                          {m.role === 'admin' ? '管理员' : m.role === 'editor' ? '编辑者' : '观察者'}
                        </span>
                        {m.user_id !== 'system' && (
                          <button
                            className="text-warm-300 hover:text-danger-500 shrink-0"
                            onClick={() => void removeMember(current.id, m.user_id)}
                          >
                            <span className="material-symbols-outlined text-[14px]">remove</span>
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Danger zone */}
              {current.id !== 'ws-default' && (
                <div className="border border-danger-200 rounded-lg p-3 bg-danger-50/30">
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-xs font-medium text-danger-600">删除工作空间</div>
                      <div className="text-[10px] text-danger-400">此操作不可撤销</div>
                    </div>
                    <button
                      className="btn-danger text-xs"
                      onClick={() => void deleteWorkspace(current.id)}
                    >
                      删除
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {showInvite && (
        <WorkspaceInviteModal
          workspaceId={currentWorkspaceId}
          onClose={() => setShowInvite(false)}
        />
      )}
    </div>
  );
}
