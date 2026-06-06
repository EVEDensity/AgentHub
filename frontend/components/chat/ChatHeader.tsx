import { memo, useState, useCallback, useRef, useEffect } from 'react';
import type { PMState, DegradationStatus } from '../../types';
import { PM_STATE_LABELS } from '../../types';

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
  pmState?: PMState;
  degradationStatus?: DegradationStatus | null;
}

const PM_STATE_COLORS: Record<PMState, string> = {
  IDLE: 'bg-warm-100 text-warm-500',
  DECOMPOSING: 'bg-purple-100 text-purple-600',
  DISPATCHING: 'bg-blue-100 text-blue-600',
  WAITING_USER: 'bg-amber-100 text-amber-700',
  EXECUTING: 'bg-emerald-100 text-emerald-600',
  SUMMARIZING: 'bg-indigo-100 text-indigo-600',
};

const PM_STATE_DOTS: Record<PMState, string> = {
  IDLE: 'bg-warm-400',
  DECOMPOSING: 'bg-purple-400 animate-pulse',
  DISPATCHING: 'bg-blue-400 animate-pulse',
  WAITING_USER: 'bg-amber-500 animate-bounce',
  EXECUTING: 'bg-emerald-400 animate-pulse',
  SUMMARIZING: 'bg-indigo-400 animate-pulse',
};

const ChatHeader = memo(function ChatHeader({
  sessionName, sessionId, connected, isStreaming, isAutoNaming,
  percent, onTaskClick, onRenameSession, onRegenerateName,
  onTogglePreview, previewOpen, onResetLayout,
  pmState = 'IDLE', degradationStatus,
}: ChatHeaderProps) {
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(sessionName);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  useEffect(() => {
    if (!editing) setEditValue(sessionName);
  }, [sessionName, editing]);

  const commitRename = useCallback(() => {
    const name = editValue.trim();
    if (!name || name === sessionName) { setEditing(false); return; }
    setEditing(false);
    onRenameSession(sessionId, name);
  }, [editValue, sessionId, sessionName, onRenameSession]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') { commitRename(); }
    if (e.key === 'Escape') { setEditing(false); setEditValue(sessionName); }
  }, [commitRename, sessionName]);

  return (
    <header className="border-b border-warm-150 bg-white">
      {/* Degradation banner */}
      {degradationStatus?.active && (
        <div className="px-6 py-2 bg-amber-50 border-b border-amber-200 flex items-center gap-2 text-sm">
          <span className="inline-block h-2 w-2 rounded-full bg-amber-500 animate-pulse" />
          <span className="font-semibold text-amber-800">⚠️ 降级模式</span>
          <span className="text-amber-700">
            — {degradationStatus.reason}
          </span>
          <span className="text-amber-500 text-xs ml-auto">
            已运行 {(() => {
              const elapsed = Math.floor((Date.now() - new Date(degradationStatus.startedAt).getTime()) / 60000);
              return elapsed < 1 ? '不到1分钟' : `${elapsed} 分钟`;
            })()}
            {degradationStatus.recoveryAttempts > 0 && ` · 已尝试恢复 ${degradationStatus.recoveryAttempts} 次`}
          </span>
        </div>
      )}

      <div className="px-6 py-4">
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

              {isAutoNaming && (
                <span className="inline-flex items-center gap-1 shrink-0 rounded-full bg-primary-50 px-2 py-0.5 text-xs text-primary-600 animate-pulse">
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-primary-500" />
                  AI 生成名称中...
                </span>
              )}

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

              {/* ── PM state indicator ────────────────────────── */}
              {pmState !== 'IDLE' && (
                <span className={`inline-flex items-center gap-1.5 shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium ${PM_STATE_COLORS[pmState]}`}>
                  <span className={`inline-block h-2 w-2 rounded-full ${PM_STATE_DOTS[pmState]}`} />
                  PM: {PM_STATE_LABELS[pmState]}
                </span>
              )}
            </div>

            {/* WS status + PM state detail */}
            <div className="text-caption text-warm-500 mt-0.5 flex items-center gap-2">
              <span>WebSocket: {connected ? (isStreaming ? 'AI streaming...' : 'Connected') : 'Reconnecting'}</span>
              {pmState === 'WAITING_USER' && (
                <span className="text-amber-600 font-medium">· 等待你的决策</span>
              )}
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
      </div>
    </header>
  );
});

export default ChatHeader;
