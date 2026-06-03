import { memo, useState, useCallback, useRef, useEffect } from 'react';

interface ChatHeaderProps {
  sessionName: string;
  sessionId: string;
  connected: boolean;
  isStreaming: boolean;
  isAutoNaming: boolean;
  percent: number;
  onTaskClick: () => void;
  onRenameSession: (id: string, name: string) => void;
  onRegenerateName: () => void;
  onTogglePreview?: () => void;
  previewOpen?: boolean;
  onResetLayout?: () => void;
}

const ChatHeader = memo(function ChatHeader({
  sessionName, sessionId, connected, isStreaming, isAutoNaming,
  percent, onTaskClick, onRenameSession, onRegenerateName,
  onTogglePreview, previewOpen, onResetLayout,
}: ChatHeaderProps) {
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(sessionName);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Focus and select when entering edit mode
  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  // Sync editValue when sessionName changes externally (e.g. auto-name)
  useEffect(() => {
    if (!editing) {
      setEditValue(sessionName);
    }
  }, [sessionName, editing]);

  const commitRename = useCallback(() => {
    const name = editValue.trim();
    if (!name || name === sessionName) {
      setEditing(false);
      return;
    }
    setEditing(false);
    onRenameSession(sessionId, name);
  }, [editValue, sessionId, sessionName, onRenameSession]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') { commitRename(); }
    if (e.key === 'Escape') { setEditing(false); setEditValue(sessionName); }
  }, [commitRename, sessionName]);

  return (
    <header className="border-b border-warm-150 bg-white px-6 py-4">
      <div className="flex items-center justify-between gap-6">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            {editing ? (
              <input
                ref={inputRef}
                className="text-h3 text-warm-800 rounded border border-primary-300 px-2 py-0.5 outline-none focus:ring-1 focus:ring-primary-500"
                style={{ fontSize: 'inherit', fontWeight: 'inherit' }}
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onKeyDown={handleKeyDown}
                onBlur={commitRename}
              />
            ) : (
              <h1
                className="text-h3 text-warm-800 truncate cursor-pointer hover:text-primary-600 transition-colors select-none"
                onClick={() => { setEditValue(sessionName); setEditing(true); }}
                title="点击编辑会话名称"
              >
                {sessionName || 'New Session'}
              </h1>
            )}

            {/* Auto-naming loading indicator */}
            {isAutoNaming && (
              <span className="inline-flex items-center gap-1 shrink-0 rounded-full bg-primary-50 px-2 py-0.5 text-xs text-primary-600 animate-pulse">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-primary-500" />
                AI 生成名称中...
              </span>
            )}

            {/* Edit button */}
            {!editing && (
              <button
                className="shrink-0 rounded p-1 text-warm-400 hover:text-primary-500 hover:bg-warm-50 transition"
                onClick={() => { setEditValue(sessionName); setEditing(true); }}
                title="编辑名称"
              >
                <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />
                </svg>
              </button>
            )}

            {/* Regenerate name button */}
            {!editing && (
              <button
                className="shrink-0 rounded p-1 text-warm-400 hover:text-accent-500 hover:bg-warm-50 transition disabled:opacity-50 disabled:cursor-not-allowed"
                onClick={onRegenerateName}
                title="AI 重新生成名称"
                disabled={isAutoNaming}
              >
                <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="1 4 1 10 7 10" />
                  <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
                </svg>
              </button>
            )}
          </div>
          <div className="text-caption text-warm-500 mt-0.5">
            WebSocket: {connected ? (isStreaming ? 'AI streaming...' : 'Connected') : 'Reconnecting'}
          </div>
        </div>
        <div className="min-w-[420px]">
          <div className="mb-1.5 flex justify-between text-caption text-warm-500">
            <div className="flex items-center gap-3">
              <button onClick={onTaskClick} className="text-primary-500 hover:text-primary-600">DAG Progress / View Tasks</button>
              {onTogglePreview && (
                <button
                  onClick={onTogglePreview}
                  className={`text-sm font-medium transition-colors ${
                    previewOpen
                      ? 'text-accent-600 hover:text-accent-700'
                      : 'text-warm-500 hover:text-primary-600'
                  }`}
                  title={previewOpen ? '关闭预览面板' : '打开预览面板'}
                >
                  {previewOpen ? '关闭预览' : '文件预览'}
                </button>
              )}
              {onResetLayout && (
                <>
                  <span className="text-warm-200">|</span>
                  <button
                    onClick={onResetLayout}
                    className="text-sm font-medium text-warm-500 transition-colors hover:text-primary-600"
                    title="重置侧栏 / 预览面板 / 文件树宽度到默认值"
                  >
                    重置布局
                  </button>
                </>
              )}
            </div>
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
