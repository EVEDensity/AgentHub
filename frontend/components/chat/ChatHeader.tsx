import { memo } from 'react';
import type { ChatSession } from '../../types';

interface ChatHeaderProps {
  sessionName: string;
  connected: boolean;
  isStreaming: boolean;
  percent: number;
  onTaskClick: () => void;
}

const ChatHeader = memo(function ChatHeader({ sessionName, connected, isStreaming, percent, onTaskClick }: ChatHeaderProps) {
  return (
    <header className="border-b border-warm-150 bg-white px-6 py-4">
      <div className="flex items-center justify-between gap-6">
        <div>
          <div className="text-h3 text-warm-800">{sessionName || 'New Session'}</div>
          <div className="text-caption text-warm-500 mt-0.5">
            WebSocket: {connected ? (isStreaming ? 'AI streaming...' : 'Connected') : 'Reconnecting'}
          </div>
        </div>
        <div className="min-w-[420px]">
          <div className="mb-1.5 flex justify-between text-caption text-warm-500">
            <button onClick={onTaskClick} className="text-primary-500 hover:text-primary-600">DAG Progress / View Tasks</button>
            <span>{percent}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-warm-100">
            <div className="h-full bg-primary-500 transition-all duration-300" style={{ width: `${percent}%` }} />
          </div>
        </div>
      </div>
    </header>
  );
});

export default ChatHeader;
