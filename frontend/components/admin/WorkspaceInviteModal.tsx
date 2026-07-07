'use client';

import { type JSX, type FormEvent } from 'react';
import { useWorkspaceStore } from '../../stores/workspaceStore';

interface Props {
  workspaceId: string;
  onClose: () => void;
}

export function WorkspaceInviteModal({ workspaceId, onClose }: Props): JSX.Element {
  const inviteEmail = useWorkspaceStore((s) => s.inviteEmail);
  const inviteRole = useWorkspaceStore((s) => s.inviteRole);
  const inviteMember = useWorkspaceStore((s) => s.inviteMember);
  const setInviteEmail = useWorkspaceStore((s) => s.setInviteEmail);
  const setInviteRole = useWorkspaceStore((s) => s.setInviteRole);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!inviteEmail.trim()) return;
    await inviteMember(workspaceId, inviteEmail.trim(), inviteRole);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="card w-full max-w-sm shadow-modal" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-warm-700">邀请成员</h3>
          <button className="text-warm-400 hover:text-warm-600" onClick={onClose}>
            <span className="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="text-xs text-warm-500 block mb-1">邮箱地址</label>
            <input
              type="email"
              className="input-field w-full text-sm"
              placeholder="colleague@company.com"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              required
            />
          </div>
          <div>
            <label className="text-xs text-warm-500 block mb-1">角色</label>
            <select
              className="input-field w-full text-sm"
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value as 'admin' | 'editor' | 'viewer')}
            >
              <option value="admin">管理员 — 完全控制</option>
              <option value="editor">编辑者 — 创建和管理 Agent/知识库</option>
              <option value="viewer">观察者 — 只读访问</option>
            </select>
          </div>

          <div className="flex justify-end gap-2 pt-3 border-t border-warm-100">
            <button type="button" className="btn-ghost text-xs" onClick={onClose}>
              取消
            </button>
            <button type="submit" className="btn-primary text-xs" disabled={!inviteEmail.trim()}>
              <span className="material-symbols-outlined text-[14px]">send</span>
              发送邀请
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
