/**
 * Session sharing dialog — invite users, manage roles, toggle visibility.
 *
 * Opens as a modal overlay. The session owner can:
 *   - Invite users by name
 *   - Change member roles (member ↔ viewer)
 *   - Remove members
 *   - Toggle public/private visibility
 */

import { memo, useState, useCallback, useEffect } from 'react';
import { X, UserPlus, Globe, Lock, Shield } from 'lucide-react';
import type { SessionMember } from '../../types';

interface ShareDialogProps {
  open: boolean;
  sessionId: string;
  sessionName: string;
  userRole: string;          // current user's role in this session
  visibility?: string;
  authHeaders: Record<string, string>;
  onClose: () => void;
  onVisibilityChange?: (visibility: string) => void;
}

const ShareDialog = memo(function ShareDialog({
  open,
  sessionId,
  sessionName,
  userRole,
  visibility,
  authHeaders,
  onClose,
  onVisibilityChange,
}: ShareDialogProps) {
  const [members, setMembers] = useState<SessionMember[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [inviteName, setInviteName] = useState('');
  const [inviteRole, setInviteRole] = useState('member');
  const [inviting, setInviting] = useState(false);
  const [changingRole, setChangingRole] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);

  const isOwner = userRole === 'owner';
  const canManage = isOwner;
  const currentVisibility = visibility || 'private';

  // Load members
  const loadMembers = useCallback(async () => {
    if (!sessionId || !open) return;
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`/api/chat/sessions/${sessionId}/members`, {
        headers: authHeaders,
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Failed to load members');
      const data = await res.json();
      setMembers(data.members || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load members');
    } finally {
      setLoading(false);
    }
  }, [sessionId, open, authHeaders]);

  useEffect(() => {
    if (open) loadMembers();
  }, [open, loadMembers]);

  // Invite a user
  const handleInvite = useCallback(async () => {
    const name = inviteName.trim();
    if (!name) return;
    setInviting(true);
    setError('');
    try {
      const res = await fetch(`/api/chat/sessions/${sessionId}/members`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify({ user_name: name, role: inviteRole }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Invite failed');
      }
      setInviteName('');
      await loadMembers();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Invite failed');
    } finally {
      setInviting(false);
    }
  }, [inviteName, inviteRole, sessionId, authHeaders, loadMembers]);

  // Change member role
  const handleRoleChange = useCallback(async (userId: string, newRole: string) => {
    setChangingRole(userId);
    setError('');
    try {
      const res = await fetch(`/api/chat/sessions/${sessionId}/members/${userId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify({ role: newRole }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Role change failed');
      }
      await loadMembers();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Role change failed');
    } finally {
      setChangingRole(null);
    }
  }, [sessionId, authHeaders, loadMembers]);

  // Remove member
  const handleRemove = useCallback(async (userId: string) => {
    setRemoving(userId);
    setError('');
    try {
      const res = await fetch(`/api/chat/sessions/${sessionId}/members/${userId}`, {
        method: 'DELETE',
        headers: authHeaders,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Remove failed');
      }
      await loadMembers();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Remove failed');
    } finally {
      setRemoving(null);
    }
  }, [sessionId, authHeaders, loadMembers]);

  // Toggle visibility
  const handleVisibilityToggle = useCallback(async () => {
    const newVis = currentVisibility === 'public' ? 'private' : 'public';
    setError('');
    try {
      const res = await fetch(`/api/chat/sessions/${sessionId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify({ visibility: newVis }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Visibility change failed');
      }
      onVisibilityChange?.(newVis);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Visibility change failed');
    }
  }, [currentVisibility, sessionId, authHeaders, onVisibilityChange]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-warm-900/40 backdrop-blur-sm" onClick={onClose} />

      {/* Dialog */}
      <div className="relative w-full max-w-md rounded-2xl bg-white shadow-modal border border-warm-150 overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-warm-100">
          <div>
            <h2 className="text-base font-semibold text-warm-800">分享会话</h2>
            <p className="text-xs text-warm-400 mt-0.5 truncate max-w-[260px]">{sessionName}</p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-warm-400 hover:text-warm-600 hover:bg-warm-50 transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4 max-h-[420px] overflow-y-auto">
          {/* Error banner */}
          {error && (
            <div className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">
              {error}
            </div>
          )}

          {/* Visibility toggle (owner only) */}
          {isOwner && (
            <div className="flex items-center justify-between rounded-xl bg-warm-50 px-4 py-3">
              <div className="flex items-center gap-2.5">
                {currentVisibility === 'public' ? (
                  <Globe className="h-4 w-4 text-emerald-500" />
                ) : (
                  <Lock className="h-4 w-4 text-warm-400" />
                )}
                <div>
                  <div className="text-sm font-medium text-warm-700">
                    {currentVisibility === 'public' ? '公开访问' : '私密会话'}
                  </div>
                  <div className="text-[11px] text-warm-400">
                    {currentVisibility === 'public'
                      ? '所有认证用户都可以查看'
                      : '仅被邀请的成员可以访问'}
                  </div>
                </div>
              </div>
              <button
                onClick={handleVisibilityToggle}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  currentVisibility === 'public' ? 'bg-emerald-500' : 'bg-warm-250'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform ${
                    currentVisibility === 'public' ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
          )}

          {/* Invite section (owner only) */}
          {isOwner && (
            <div>
              <label className="block text-xs font-semibold text-warm-500 uppercase tracking-wide mb-2">
                邀请成员
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={inviteName}
                  onChange={(e) => setInviteName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleInvite(); }}
                  placeholder="输入用户名..."
                  disabled={inviting}
                  className="flex-1 rounded-lg border border-warm-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-300 disabled:opacity-50"
                />
                <select
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value)}
                  disabled={inviting}
                  className="rounded-lg border border-warm-200 px-2 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-primary-300 disabled:opacity-50"
                >
                  <option value="member">成员</option>
                  <option value="viewer">观察者</option>
                </select>
                <button
                  onClick={handleInvite}
                  disabled={inviting || !inviteName.trim()}
                  className="inline-flex items-center gap-1 rounded-lg bg-primary-500 px-3 py-2 text-sm font-medium text-white hover:bg-primary-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <UserPlus className="h-4 w-4" />
                  {inviting ? '邀请中...' : '邀请'}
                </button>
              </div>
            </div>
          )}

          {/* Members list */}
          <div>
            <label className="block text-xs font-semibold text-warm-500 uppercase tracking-wide mb-2">
              成员 ({members.length})
            </label>
            {loading ? (
              <div className="text-sm text-warm-400 py-3 text-center">加载中...</div>
            ) : members.length === 0 ? (
              <div className="text-sm text-warm-400 py-3 text-center">暂无成员</div>
            ) : (
              <ul className="space-y-1">
                {members.map((m) => (
                  <li
                    key={m.userId}
                    className="flex items-center justify-between rounded-lg px-3 py-2 hover:bg-warm-50 transition-colors"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <div className="flex items-center justify-center w-8 h-8 rounded-full bg-primary-100 text-primary-700 text-xs font-bold shrink-0">
                        {(m.userName || '?')[0].toUpperCase()}
                      </div>
                      <div className="min-w-0">
                        <div className="text-sm font-medium text-warm-800 truncate">
                          {m.userName}
                          {m.role === 'owner' && (
                            <span className="ml-1.5 inline-flex items-center gap-0.5 text-[10px] text-amber-600">
                              <Shield className="h-3 w-3" />
                            </span>
                          )}
                        </div>
                        <div className="text-[11px] text-warm-400">
                          {m.role === 'owner' ? '所有者' : m.role === 'member' ? '成员' : '观察者'}
                          {m.onlineStatus === 'online' && (
                            <span className="ml-1.5 inline-block w-1.5 h-1.5 rounded-full bg-emerald-500" />
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Management controls (owner only, not for self) */}
                    {canManage && m.role !== 'owner' && (
                      <div className="flex items-center gap-1 ml-2">
                        <select
                          value={m.role}
                          onChange={(e) => handleRoleChange(m.userId, e.target.value)}
                          disabled={changingRole === m.userId}
                          className="rounded border border-warm-200 px-1.5 py-1 text-[11px] bg-white focus:outline-none focus:ring-1 focus:ring-primary-300 disabled:opacity-50"
                        >
                          <option value="member">成员</option>
                          <option value="viewer">观察者</option>
                        </select>
                        <button
                          onClick={() => handleRemove(m.userId)}
                          disabled={removing === m.userId}
                          className="rounded p-1 text-warm-400 hover:text-red-500 hover:bg-red-50 transition-colors disabled:opacity-50"
                          title="移除成员"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="border-t border-warm-100 px-5 py-3 flex justify-end">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm font-medium text-warm-600 hover:bg-warm-50 transition-colors"
          >
            完成
          </button>
        </div>
      </div>
    </div>
  );
});

export default ShareDialog;
