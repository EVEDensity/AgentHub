/**
 * Single user avatar circle with online-status dot.
 *
 * Used by ``UserRoster`` to render one connected user.
 */
import { memo } from 'react';

interface PresenceBadgeProps {
  name: string;
  role: string;
  status: 'online' | 'idle' | 'typing' | 'offline';
  size?: number;
}

const STATUS_COLORS: Record<string, string> = {
  online: '#5B8C5A',
  idle: '#D98B2B',
  typing: '#4F6CF7',
  offline: '#ADABA3',
};

const ROLE_LABELS: Record<string, string> = {
  owner: 'Owner',
  member: 'Member',
  viewer: 'Viewer',
};

const PresenceBadge = memo(function PresenceBadge({
  name,
  role,
  status,
  size = 32,
}: PresenceBadgeProps) {
  const initial = (name || '?')[0].toUpperCase();
  const hue = Math.abs(
    name.split('').reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 0)
  );
  const bgColor = `hsl(${hue}, 55%, 65%)`;

  return (
    <div
      className="relative flex-shrink-0"
      style={{ width: size, height: size }}
      title={`${name} — ${ROLE_LABELS[role] || role} (${status})`}
    >
      {/* Avatar circle */}
      <div
        className="flex items-center justify-center rounded-full text-white font-semibold select-none"
        style={{
          width: size,
          height: size,
          backgroundColor: bgColor,
          fontSize: Math.max(11, size * 0.38),
        }}
      >
        {initial}
      </div>
      {/* Status dot */}
      <span
        className="absolute -bottom-0.5 -right-0.5 block rounded-full border-2 border-white"
        style={{
          width: Math.max(8, size * 0.32),
          height: Math.max(8, size * 0.32),
          backgroundColor: STATUS_COLORS[status] || STATUS_COLORS.offline,
          transition: 'background-color 0.3s ease',
        }}
      />
    </div>
  );
});

export default PresenceBadge;
