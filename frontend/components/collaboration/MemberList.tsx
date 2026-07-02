/**
 * Inline member list — shows session members with online status.
 *
 * Used in the session sidebar or as a standalone panel.
 * Clicking the "share" button opens ``ShareDialog``.
 */

import { memo, useState, useCallback, useEffect } from 'react';
import { Users, Share2 } from 'lucide-react';
import type { SessionMember } from '../../types';
import PresenceBadge from './PresenceBadge';
import { getPresenceStore, type PresenceUser } from '../../lib/presenceStore';

interface MemberListProps {
  sessionId: string;
  sessionName?: string;
  userRole?: string;
  visibility?: string;
  authHeaders: Record<string, string>;
  onOpenShare?: () => void;
}

const MemberList = memo(function MemberList({
  sessionId,
  sessionName,
  userRole,
  visibility,
  authHeaders,
  onOpenShare,
}: MemberListProps) {
  const [members, setMembers] = useState<SessionMember[]>([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const onlineUsers = getPresenceStore().useUsers(sessionId);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    setLoading(true);
    fetch(`/api/chat/sessions/${sessionId}/members`, { headers: authHeaders })
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled) setMembers(data.members || []);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [sessionId, authHeaders]);

  // Merge online status from presence store
  const membersWithStatus: (SessionMember & { onlineStatus: string })[] = members.map((m) => {
    const online = onlineUsers.find((u: PresenceUser) => u.userId === m.userId);
    return { ...m, onlineStatus: online?.status || 'offline' };
  });

  const onlineCount = membersWithStatus.filter((m) => m.onlineStatus !== 'offline').length;
  const isOwner = userRole === 'owner';

  if (loading && members.length === 0) {
    return (
      <div className="px-3 py-2 text-[11px] text-warm-400">Loading...</div>
    );
  }

  return (
    <div className="border-t border-warm-100">
      {/* Header */}
      <button
        onClick={() => setExpanded((p) => !p)}
        className="flex w-full items-center justify-between px-4 py-2.5 hover:bg-warm-50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Users className="h-3.5 w-3.5 text-warm-400" />
          <span className="text-xs font-medium text-warm-600">
            成员
          </span>
          <span className="text-[11px] text-warm-400">
            {members.length}
            {onlineCount > 0 && (
              <span className="ml-1 text-emerald-500">({onlineCount} 在线)</span>
            )}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {isOwner && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onOpenShare?.();
              }}
              className="rounded p-1 text-warm-400 hover:text-primary-500 hover:bg-primary-50 transition-colors"
              title="管理成员"
            >
              <Share2 className="h-3.5 w-3.5" />
            </button>
          )}
          <svg
            className={`h-3.5 w-3.5 text-warm-400 transition-transform ${expanded ? 'rotate-180' : ''}`}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </div>
      </button>

      {/* Expanded list */}
      {expanded && (
        <div className="px-3 pb-3">
          {membersWithStatus.length === 0 ? (
            <div className="text-[11px] text-warm-400 py-2 text-center">暂无成员</div>
          ) : (
            <ul className="space-y-0.5">
              {membersWithStatus.map((m) => (
                <li
                  key={m.userId}
                  className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 hover:bg-warm-50 transition-colors"
                >
                  <PresenceBadge
                    name={m.userName}
                    role={m.role}
                    status={m.onlineStatus as 'online' | 'idle' | 'typing' | 'offline'}
                    size={24}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-medium text-warm-700 truncate">
                      {m.userName}
                    </div>
                    <div className="text-[10px] text-warm-400">
                      {m.role === 'owner' ? '所有者' : m.role === 'member' ? '成员' : '观察者'}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {/* Quick invite hint for owner */}
          {isOwner && members.length <= 1 && (
            <button
              onClick={onOpenShare}
              className="mt-2 w-full rounded-lg border border-dashed border-warm-200 px-3 py-2 text-[11px] text-warm-500 hover:border-primary-300 hover:text-primary-600 hover:bg-primary-50/50 transition-colors"
            >
              + 邀请用户加入协作
            </button>
          )}
        </div>
      )}
    </div>
  );
});

export default MemberList;
