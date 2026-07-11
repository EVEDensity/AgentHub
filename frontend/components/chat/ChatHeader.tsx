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
  /** Phase-based progress: thinking | executing | generating | done | idle */
  streamPhase?: 'idle' | 'thinking' | 'executing' | 'generating' | 'done';
  /** Currently executing tool names */
  activeTools?: string[];
  /** Current agent name */
  currentAgentName?: string;
  /** Interrupt the current streaming session */
  onInterruptStream?: () => void;
}

const PM_STATE_COLORS: Record<PMState, string> = {
  IDLE: 'bg-warm-100 text-warm-500 border border-warm-150',
  DECOMPOSING: 'bg-primary-50 text-primary-600 border border-primary-100',
  DISPATCHING: 'bg-primary-50 text-primary-600 border border-primary-100',
  WAITING_USER: 'bg-accent-50 text-accent-600 border border-accent-100',
  EXECUTING: 'bg-success-50 text-success-600 border border-success-100',
  SUMMARIZING: 'bg-primary-50 text-primary-600 border border-primary-100',
};

const PM_STATE_DOTS: Record<PMState, string> = {
  IDLE: 'bg-warm-400',
  DECOMPOSING: 'bg-primary-400 animate-pulse',
  DISPATCHING: 'bg-primary-400 animate-pulse',
  WAITING_USER: 'bg-warning-500 animate-bounce',
  EXECUTING: 'bg-success-400 animate-pulse',
  SUMMARIZING: 'bg-primary-400 animate-pulse',
};

const ChatHeader = memo(function ChatHeader({
  sessionName, sessionId, connected, isStreaming, isAutoNaming,
  percent, onTaskClick, onRenameSession, onRegenerateName,
  onTogglePreview, previewOpen, onResetLayout,
  pmState = 'IDLE', degradationStatus,
  streamPhase = 'idle', activeTools = [], currentAgentName = '',
  onInterruptStream,
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
    <div>
      {/* Degradation banner */}
      {degradationStatus?.active && (
        <div className="px-6 py-2 bg-warning-50 border-b border-warning-100 flex items-center gap-2 text-sm">
          <span className="inline-block h-2 w-2 bg-warning-500 animate-pulse" />
          <span className="font-semibold text-warning-700">降级模式</span>
          <span className="text-warning-600">
            — {degradationStatus.reason}
          </span>
          <span className="text-warning-500 text-xs ml-auto">
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

            {/* Phase + PM state detail */}
            <div className="text-caption text-warm-500 mt-0.5 flex items-center gap-2 flex-wrap">
              {streamPhase !== 'idle' && streamPhase !== 'done' && (
                <span>{connected ? (
                  streamPhase === 'thinking' ? 'AI 思考中...'
                  : streamPhase === 'executing' ? (activeTools.length > 0 ? `执行: ${activeTools.join(', ')}` : '工具执行中...')
                  : streamPhase === 'generating' ? '生成回复中...'
                  : isStreaming ? 'AI 生成中...'
                  : ''
                ) : ''}</span>
              )}
              {pmState === 'WAITING_USER' && (
                <span className="text-warning-600 font-medium">· 等待你的决策</span>
              )}
              {currentAgentName && streamPhase !== 'idle' && streamPhase !== 'done' && (
                <span className="text-primary-500">· {currentAgentName}</span>
              )}
            </div>
          </div>

          {/* ── 右侧：任务控制 / 文件预览 / 重置布局 / 进度 ── */}
          <div className="flex items-center gap-3 shrink-0">
            {/* ★ 方案2: 进度条 — DAG优先，否则用阶段式进度 */}
            <div className="flex items-center gap-2">
              {(() => {
                const phasePercent = streamPhase === 'thinking' ? 25
                  : streamPhase === 'executing' ? 50
                  : streamPhase === 'generating' ? 75
                  : streamPhase === 'done' ? 100
                  : 0;
                const displayPercent = percent > 0 ? percent : phasePercent;
                const barColor = streamPhase === 'thinking' ? 'bg-primary-500'
                  : streamPhase === 'executing' ? 'bg-primary-500 animate-pulse'
                  : streamPhase === 'generating' ? 'bg-primary-500'
                  : streamPhase === 'done' ? 'bg-success-500'
                  : percent > 0 ? 'bg-primary-500'
                  : 'bg-warm-200';
                return (
                  <>
                    <div className="h-1.5 w-16 overflow-hidden rounded-full bg-warm-100 shadow-inner">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${barColor}`}
                        style={{ width: `${displayPercent}%` }}
                      />
                    </div>
                    <span className={`text-xs font-mono font-medium tabular-nums ${isStreaming || streamPhase !== 'idle' ? 'text-primary-600' : 'text-warm-500'}`}>
                      {displayPercent}%
                    </span>
                  </>
                );
              })()}
            </div>

            <span className="h-5 w-px bg-warm-150" />

            <button
              onClick={onTaskClick}
              className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-warm-500 hover:text-primary-500 hover:bg-warm-100 transition-colors"
              title="查看 DAG 任务进度"
            >
              <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="7" height="7" rx="1" />
                <rect x="14" y="3" width="7" height="7" rx="1" />
                <rect x="3" y="14" width="7" height="7" rx="1" />
                <rect x="14" y="14" width="7" height="7" rx="1" />
              </svg>
              <span className="hidden sm:inline">View Tasks</span>
            </button>

            {onTogglePreview && (
              <button
                onClick={onTogglePreview}
                className={`inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-md transition-colors ${
                  previewOpen
                    ? 'text-accent-600 bg-accent-50 hover:bg-accent-100'
                    : 'text-warm-500 hover:text-primary-600 hover:bg-warm-50'
                }`}
                title={previewOpen ? '关闭预览面板' : '打开文件预览面板'}
              >
                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                  <line x1="16" y1="13" x2="8" y2="13" />
                  <line x1="16" y1="17" x2="8" y2="17" />
                </svg>
                <span className="hidden sm:inline">文件预览</span>
              </button>
            )}

            {onResetLayout && (
              <button
                onClick={onResetLayout}
                className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-warm-500 hover:text-primary-600 hover:bg-warm-50 rounded-md transition-colors"
                title="重置侧栏 / 预览面板 / 文件树宽度到默认值"
              >
                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="1 4 1 10 7 10" />
                  <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
                </svg>
                <span className="hidden sm:inline">重置布局</span>
              </button>
            )}

            {/* ★ 方案5: 中断按钮 — 流式/AI处理中显示 */}
            {onInterruptStream && isStreaming && (
              <button
                onClick={onInterruptStream}
                className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-danger-500 hover:text-danger-600 hover:bg-danger-50 transition-colors"
                title="中断当前 AI 处理"
              >
                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor" stroke="none">
                  <rect x="4" y="4" width="16" height="16" rx="2" />
                </svg>
                <span className="hidden sm:inline">中断</span>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
});

export default ChatHeader;
