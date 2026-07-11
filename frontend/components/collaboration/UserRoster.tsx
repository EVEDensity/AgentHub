/**
 * Collapsible panel showing online users in the current session.
 *
 * Renders a row of avatar circles with status dots.  Click the row
 * to expand a full list with user names and roles.
 */
import { memo, useState, useCallback } from 'react';
import PresenceBadge from './PresenceBadge';
import { getPresenceStore, type PresenceUser } from '../../lib/presenceStore';

interface UserRosterProps {
  sessionId: string;
}

const UserRoster = memo(function UserRoster({ sessionId }: UserRosterProps) {
  const users = getPresenceStore().useUsers(sessionId);
  const [expanded, setExpanded] = useState(false);

  const toggle = useCallback(() => setExpanded((p) => !p), []);

  const online = users.filter((u) => u.status !== 'offline');
  const count = online.length;

  if (count === 0) return null;

  return (
    <div className="relative">
      {/* Compact avatar row */}
      <button
        onClick={toggle}
        className="flex items-center gap-1 px-2 py-1 rounded-lg hover:bg-warm-100 transition-colors"
        title={`${count} user${count > 1 ? 's' : ''} online`}
      >
        <div className="flex -space-x-2">
          {online.slice(0, 4).map((u) => (
            <PresenceBadge
              key={u.userId}
              name={u.name}
              role={u.role}
              status={u.status}
              size={28}
            />
          ))}
          {count > 4 && (
            <div
              className="flex items-center justify-center rounded-full bg-warm-150 text-[10px] font-semibold text-warm-500 border-2 border-white"
              style={{ width: 28, height: 28 }}
            >
              +{count - 4}
            </div>
          )}
        </div>
        <span className="text-[11px] text-warm-400 ml-1">{count}</span>
      </button>

      {/* Expanded list */}
      {expanded && (
        <div className="absolute top-full right-0 mt-2 w-56 rounded-xl bg-warm-100 border border-warm-150 shadow-modal z-30 py-1.5">
          <div className="px-3 py-1.5 text-[11px] font-semibold text-warm-400 uppercase tracking-wide">
            Online — {count}
          </div>
          {online.map((u) => (
            <div
              key={u.userId}
              className="flex items-center gap-3 px-3 py-2 hover:bg-warm-50 transition-colors"
            >
              <PresenceBadge
                name={u.name}
                role={u.role}
                status={u.status}
                size={28}
              />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-warm-800 truncate">
                  {u.name}
                </div>
                <div className="text-[10px] text-warm-400">
                  {u.role === 'owner' ? 'Owner' : u.role === 'member' ? 'Member' : 'Viewer'}
                  {u.status === 'typing' && ' • typing...'}
                </div>
              </div>
              <span
                className="flex-shrink-0 rounded-full w-2 h-2"
                style={{
                  backgroundColor:
                    u.status === 'online'
                      ? '#5B8C5A'
                      : u.status === 'idle'
                        ? '#D98B2B'
                        : '#4F6CF7',
                }}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
});

export default UserRoster;
