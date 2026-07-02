/**
 * Animated "User is typing..." indicator shown below ChatInput.
 *
 * Subscribes to ``CollaborationStore`` for the active session.
 */
import { memo } from 'react';
import { getCollaborationStore } from '../../lib/collaborationStore';

interface TypingIndicatorProps {
  sessionId: string;
}

const TypingIndicator = memo(function TypingIndicator({ sessionId }: TypingIndicatorProps) {
  const typingUsers = getCollaborationStore().useTypingUsers(sessionId);

  if (typingUsers.length === 0) return null;

  const names = typingUsers.map((u) => u.userName).filter(Boolean);
  const label =
    names.length === 1
      ? `${names[0]} is typing...`
      : names.length === 2
        ? `${names[0]} and ${names[1]} are typing...`
        : `${names.slice(0, 2).join(', ')} and ${names.length - 2} others are typing...`;

  return (
    <div className="flex items-center gap-1.5 px-1 py-0.5 text-[11px] text-warm-400 select-none">
      <span className="inline-flex gap-0.5">
        <span
          className="inline-block w-0.5 h-0.5 rounded-full bg-warm-400 animate-bounce"
          style={{ animationDuration: '0.6s', animationDelay: '0s' }}
        />
        <span
          className="inline-block w-0.5 h-0.5 rounded-full bg-warm-400 animate-bounce"
          style={{ animationDuration: '0.6s', animationDelay: '0.15s' }}
        />
        <span
          className="inline-block w-0.5 h-0.5 rounded-full bg-warm-400 animate-bounce"
          style={{ animationDuration: '0.6s', animationDelay: '0.3s' }}
        />
      </span>
      <span>{label}</span>
    </div>
  );
});

export default TypingIndicator;
