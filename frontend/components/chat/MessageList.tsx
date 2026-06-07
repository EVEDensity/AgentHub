import { memo, useCallback, useMemo } from 'react';
import dynamic from 'next/dynamic';
import type { Agent, ContentSegment, GeneratedData, Message, User } from '../../types';
import { MessageSquareQuote } from 'lucide-react';
import CodeGenResultPanel, { isCodeGenOutput } from './CodeGenResultPanel';

// Lazy-load heavy diff viewers — Monaco & CodeReview are only used for diff messages
const DiffBubble = dynamic(() => import('./DiffBubble'), {
  ssr: false,
  loading: () => null,
});
const CodeReviewPanel = dynamic(() => import('./CodeReviewPanel'), {
  ssr: false,
  loading: () => null,
});

import MarkdownRenderer from './MarkdownRenderer';
import SafetyBlockAlert from './SafetyBlockAlert';
import ThinkingPanel from './ThinkingPanel';
import ToolCallBubble from './ToolCallBubble';
import GeneratedFilesPanel from '../git/GeneratedFilesPanel';
import AgentQuestionBubble from './AgentQuestionBubble';
import ProgressBubble from './ProgressBubble';
import RiskAlertBubble from './RiskAlertBubble';
import AgentTodoBubble from './AgentTodoBubble';
import TaskPreviewCard from './TaskPreviewCard';
import TerminalBubble from './TerminalBubble';
import { getPresenceStore } from '../../lib/presenceStore';

// ── Module-level pure helpers (no closure dependencies) ────────────────

function normalizeStructuredStreamContent(content: unknown): string {
  if (typeof content === 'string') {
    return content.replace(/<\/?thinking>/g, '');
  }
  if (content === null || content === undefined) {
    return '';
  }
  if (typeof content === 'number' || typeof content === 'boolean' || typeof content === 'bigint') {
    return String(content);
  }
  try {
    return JSON.stringify(content, null, 2);
  } catch {
    return String(content);
  }
}

/** Fast-path: skip regex when no think tags present */
function parseThinkSegments(content: string): ContentSegment[] {
  if (!content.includes('<think>')) {
    return [{ type: 'text', content, isComplete: true }];
  }
  const segments: ContentSegment[] = [];
  const re = /<think>([\s\S]*?)(<\/think>|$)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = re.exec(content)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: 'text', content: content.slice(lastIndex, match.index), isComplete: true });
    }
    segments.push({ type: 'think', content: match[1], isComplete: match[2] === '</think>' });
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < content.length) {
    segments.push({ type: 'text', content: content.slice(lastIndex), isComplete: true });
  }
  if (segments.length === 0) {
    segments.push({ type: 'text', content, isComplete: true });
  }
  return segments;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

function FileIcon({ category, size }: { category: string; size: number }) {
  const cls = `h-${size} w-${size} shrink-0 text-warm-400`;
  switch (category) {
    case 'code':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>;
    case 'document':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>;
    case 'image':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>;
    case 'archive':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 8v13H3V8"/><path d="M1 3h22v5H1z"/><path d="M10 12h4"/></svg>;
    case 'spreadsheet':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/></svg>;
    case 'config':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>;
    default:
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>;
  }
}

// ── Avatar helpers ──────────────────────────────────────────────────

const AVATAR_COLORS = [
  { bg: 'bg-blue-500',   ring: 'ring-blue-200' },
  { bg: 'bg-emerald-500', ring: 'ring-emerald-200' },
  { bg: 'bg-violet-500',  ring: 'ring-violet-200' },
  { bg: 'bg-amber-500',   ring: 'ring-amber-200' },
  { bg: 'bg-rose-500',    ring: 'ring-rose-200' },
  { bg: 'bg-cyan-500',    ring: 'ring-cyan-200' },
  { bg: 'bg-fuchsia-500', ring: 'ring-fuchsia-200' },
  { bg: 'bg-orange-500',  ring: 'ring-orange-200' },
  { bg: 'bg-teal-500',    ring: 'ring-teal-200' },
  { bg: 'bg-indigo-500',  ring: 'ring-indigo-200' },
  { bg: 'bg-pink-500',    ring: 'ring-pink-200' },
  { bg: 'bg-lime-500',    ring: 'ring-lime-200' },
];

const KNOWN_AGENTS = new Set(['Orchestrator', 'Architect', 'CodeGen', 'Review', 'Test', 'Deploy', 'Implement', 'PM', '__direct__', 'system']);

function getAvatarColor(name: string): { bg: string; ring: string } {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) | 0;
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}

function isAiSender(sender: string): boolean {
  return KNOWN_AGENTS.has(sender) || sender.toLowerCase().includes('agent');
}

function getOwnerLabel(ownerUserId: string | undefined, currentUser: { id?: string; name: string } | null): string | null {
  if (!ownerUserId) return null;
  if (currentUser?.id && ownerUserId === currentUser.id) {
    return currentUser.name || '我';
  }
  return ownerUserId.length > 8 ? ownerUserId.slice(0, 8) + '…' : ownerUserId;
}

function OwnerChip({
  ownerUserId, currentUser, resolveName,
}: {
  ownerUserId: string | undefined;
  currentUser: { id?: string; name: string } | null;
  resolveName: (uid: string | undefined) => string | null;
}) {
  const name = resolveName(ownerUserId) ?? getOwnerLabel(ownerUserId, currentUser);
  if (!name) return null;
  const isMe = !!(currentUser?.id && ownerUserId === currentUser.id);
  return (
    <span
      className={
        'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium border ' +
        (isMe
          ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
          : 'bg-teal-50 text-teal-700 border-teal-200')
      }
      title={isMe ? '这是你发起的 Agent' : `Agent 归属：${name}`}
    >
      <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
        <circle cx="12" cy="7" r="4" />
      </svg>
      <span>{isMe ? `${name} 的` : `@${name}`}</span>
    </span>
  );
}

function formatTimestamp(iso: string): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return '刚刚';
    if (diffMin < 60) return `${diffMin}分钟前`;
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const msgDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const diffDays = Math.floor((today.getTime() - msgDay.getTime()) / 86400000);
    if (diffDays === 0) {
      return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    }
    if (diffDays === 1) return `昨天 ${d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`;
    if (diffDays < 7) return `${diffDays}天前`;
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

function SenderAvatar({ name, avatarUrl, size }: { name: string; avatarUrl?: string; size?: 'sm' | 'md' }) {
  const s = size === 'sm' ? 'h-6 w-6 text-[10px]' : 'h-8 w-8 text-xs';
  const { bg, ring } = getAvatarColor(name);
  const initial = (name || '?')[0].toUpperCase();

  if (avatarUrl) {
    return (
      <img
        src={avatarUrl}
        className={`inline-flex items-center justify-center rounded-full object-cover shrink-0 ring-2 ${ring} ${s}`}
        title={name}
        alt={name}
        loading="lazy"
        decoding="async"
      />
    );
  }

  return (
    <span
      className={`inline-flex items-center justify-center rounded-full ${bg} text-white font-bold ${s} shrink-0 ring-2 ${ring}`}
      title={name}
    >
      {initial}
    </span>
  );
}

// ── Per-message row (memo'd — only re-renders when its msg changes) ──

interface MessageRowProps {
  msg: Message;
  msgKey: string;
  user: User | null;
  agentAvatarMap: Map<string, string>;
  resolveUserName: (uid: string | undefined) => string | null;
  onQuoteMessage?: (msg: Message, selectedText?: string) => void;
  onSendPMEvent?: (event: Record<string, unknown>) => void;
}

const MessageRow = memo(function MessageRow({
  msg, msgKey, user, agentAvatarMap, resolveUserName,
  onQuoteMessage, onSendPMEvent,
}: MessageRowProps) {
  const getAvatarUrl = useCallback((sender: string): string | undefined => {
    if (!sender || agentAvatarMap.size === 0) return undefined;
    return agentAvatarMap.get(sender) ?? agentAvatarMap.get(sender.toLowerCase());
  }, [agentAvatarMap]);

  // User-message detection
  const userId = user?.id || '';
  const userName = (user?.name || '').trim();
  const sender = (msg.sender || '').trim();
  const isUser = (userId !== '' && msg.userId === userId)
    || sender === 'user'
    || (userName !== '' && sender === userName)
    || (userName !== '' && sender.toLowerCase() === userName.toLowerCase());
  const isToolCall = msg.type === 'tool_call' || msg.type === 'tool_result';
  const isCode = msg.type === 'code' || msg.type === 'diff';
  const badge = msg.type || 'text';
  const showCursor = msg.isStreaming;
  const safeContent = useMemo(
    () => normalizeStructuredStreamContent(msg.content),
    [msg.content],
  );
  const segments = useMemo(
    () => parseThinkSegments(safeContent),
    [safeContent],
  );

  // ── Streaming thinking placeholder ──
  const isThinking = showCursor && !isUser && !isCode && !isToolCall;
  if (isThinking && (!safeContent || safeContent.startsWith('正在'))) {
    const statusText = safeContent || '模型正在思考中...';
    return (
      <div key={msgKey} className="mb-4 flex justify-start">
        <SenderAvatar name={msg.sender || 'AI'} avatarUrl={getAvatarUrl(msg.sender || '')} size="sm" />
        <div className="ml-2 max-w-[85%] rounded-2xl px-4 py-3 bg-white border border-blue-200 shadow-sm">
          <div className="mb-1 flex items-center gap-2 text-xs opacity-80">
            <span className="font-semibold text-warm-700">{msg.sender || 'agent'}</span>
            <span className="rounded px-2 py-0.5 bg-blue-50 text-blue-600 text-xs font-medium">AI</span>
            <OwnerChip ownerUserId={msg.userId} currentUser={user} resolveName={resolveUserName} />
            <span className="rounded px-2 py-0.5 bg-blue-50 text-blue-600 text-xs">思考中</span>
            {msg.timestamp && (
              <span className="ml-auto text-warm-400 whitespace-nowrap">{formatTimestamp(msg.timestamp)}</span>
            )}
          </div>
          <div className="flex items-center gap-2 py-1">
            <span className="flex items-center gap-1">
              <span className="inline-block h-2 w-2 rounded-full bg-blue-400 animate-pulse" style={{ animationDelay: '0ms' }} />
              <span className="inline-block h-2 w-2 rounded-full bg-blue-400 animate-pulse" style={{ animationDelay: '200ms' }} />
              <span className="inline-block h-2 w-2 rounded-full bg-blue-400 animate-pulse" style={{ animationDelay: '400ms' }} />
            </span>
            <span className="text-sm text-warm-500">{statusText}</span>
          </div>
        </div>
      </div>
    );
  }

  // ── Tool call bubble ──
  if (isToolCall) {
    return (
      <div key={msgKey} className="mb-3">
        <div className="mb-1 flex items-center gap-2 text-xs text-warm-500">
          <SenderAvatar name={msg.sender || 'agent'} avatarUrl={getAvatarUrl(msg.sender || '')} size="sm" />
          <span className="font-semibold text-warm-700">{msg.sender || 'agent'}</span>
          <span className="rounded px-1.5 py-0.5 bg-blue-100 text-blue-700 text-xs font-medium">AI</span>
          <OwnerChip ownerUserId={msg.userId} currentUser={user} resolveName={resolveUserName} />
          <span className="tag tag-warm">{badge}</span>
          {msg.timestamp && (
            <span className="ml-auto text-warm-400 whitespace-nowrap">{formatTimestamp(msg.timestamp)}</span>
          )}
        </div>
        <ToolCallBubble
          calls={msg.toolCallData?.calls}
          results={msg.toolResultData?.results}
          isStreaming={showCursor}
        />
      </div>
    );
  }

  // ── Terminal output ──
  if (msg.type === 'terminal') {
    return (
      <div key={msgKey} className="mb-3">
        <div className="mb-1 flex items-center gap-2 text-xs text-warm-500">
          <SenderAvatar name={msg.sender || 'agent'} avatarUrl={getAvatarUrl(msg.sender || '')} size="sm" />
          <span className="font-semibold text-warm-700">{msg.sender || 'agent'}</span>
          <span className="rounded px-1.5 py-0.5 bg-blue-100 text-blue-700 text-xs font-medium">AI</span>
          <OwnerChip ownerUserId={msg.userId} currentUser={user} resolveName={resolveUserName} />
          <span className="tag tag-warm">terminal</span>
          {msg.timestamp && (
            <span className="ml-auto text-warm-400 whitespace-nowrap">{formatTimestamp(msg.timestamp)}</span>
          )}
          {showCursor && <span className="inline-block h-3 w-0.5 animate-pulse bg-primary-500" />}
        </div>
        <TerminalBubble content={safeContent} isStreaming={!!showCursor} />
      </div>
    );
  }

  // ── Code / diff ──
  if (isCode) {
    const isGitDiff = safeContent.includes('diff --git ');
    const diffProps = (onSendPMEvent && msg.diffDecisionState !== undefined)
      ? {
          onAccept: () => onSendPMEvent({
            event: 'diff_decision',
            sessionId: msg.sessionId,
            messageId: msg.messageId || msg.id,
            decision: 'accept' as const,
            path: msg.diffFilePath || '',
          }),
          onReject: () => onSendPMEvent({
            event: 'diff_decision',
            sessionId: msg.sessionId,
            messageId: msg.messageId || msg.id,
            decision: 'reject' as const,
            path: msg.diffFilePath || '',
          }),
          decisionState: msg.diffDecisionState,
        }
      : {};
    return (
      <div key={msgKey} className="-mx-6 mb-4 px-6">
        <div className="mb-2 flex items-center gap-2 text-xs text-warm-500">
          <SenderAvatar name={msg.sender || 'agent'} avatarUrl={getAvatarUrl(msg.sender || '')} size="sm" />
          <span className="font-semibold text-warm-700">{msg.sender || 'agent'}</span>
          <span className="rounded px-1.5 py-0.5 bg-blue-100 text-blue-700 text-xs font-medium">AI</span>
          <OwnerChip ownerUserId={msg.userId} currentUser={user} resolveName={resolveUserName} />
          <span className="tag tag-warm">{badge}</span>
          {msg.timestamp && (
            <span className="ml-auto text-warm-400 whitespace-nowrap">{formatTimestamp(msg.timestamp)}</span>
          )}
          {showCursor && <span className="inline-block h-4 w-0.5 animate-pulse bg-primary-500" />}
        </div>
        {isGitDiff ? (
          <CodeReviewPanel content={safeContent} />
        ) : (
          <DiffBubble value={safeContent} {...diffProps} />
        )}
      </div>
    );
  }

  // ── PM/PMO interaction bubbles ──
  if (msg.type === 'agent_question' && msg.questionData && onSendPMEvent) {
    return (
      <AgentQuestionBubble
        key={msgKey}
        data={msg.questionData}
        isStreaming={!!showCursor}
        onSendEvent={onSendPMEvent}
      />
    );
  }

  if (msg.type === 'progress_update' && msg.progressData) {
    return (
      <ProgressBubble
        key={msgKey}
        data={msg.progressData}
        isStreaming={!!showCursor}
      />
    );
  }

  if (msg.type === 'risk_warning' && msg.riskWarningData && onSendPMEvent) {
    return (
      <RiskAlertBubble
        key={msgKey}
        data={msg.riskWarningData}
        isStreaming={!!showCursor}
        onSendEvent={onSendPMEvent}
      />
    );
  }

  if (msg.type === 'agent_todo' && msg.todoData && onSendPMEvent) {
    return (
      <AgentTodoBubble
        key={msgKey}
        data={msg.todoData}
        isStreaming={!!showCursor}
        onSendEvent={onSendPMEvent}
      />
    );
  }

  if (msg.type === 'task_preview' && msg.taskPreviewData && onSendPMEvent) {
    return (
      <TaskPreviewCard
        key={msgKey}
        data={msg.taskPreviewData}
        isStreaming={!!showCursor}
        onSendEvent={onSendPMEvent}
      />
    );
  }

  // ── Default text bubble ──
  return (
    <div key={msgKey} className={`mb-4 flex ${isUser ? 'justify-end' : 'justify-start'} group relative`}>
      <div className={`max-w-[85%] rounded-2xl px-4 py-3 shadow-sm ${isUser ? 'bg-primary-500 text-white' : 'bg-white text-warm-800 border border-warm-150'} ${!isUser ? 'relative' : ''}`}>
        {onQuoteMessage && !isUser && (
          <button
            type="button"
            onClick={() => {
              const sel = window.getSelection();
              const selectedText = (sel && !sel.isCollapsed) ? sel.toString().trim() : '';
              onQuoteMessage(msg, selectedText || undefined);
            }}
            className="absolute left-full ml-1 top-1 z-10 opacity-0 group-hover:opacity-100 transition-opacity inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[10px] font-medium bg-white/90 border border-warm-200 text-warm-500 hover:text-primary-600 hover:border-primary-300 shadow-sm whitespace-nowrap"
            title="引用Agent的回复（选中文字可引用片段）"
          >
            <MessageSquareQuote className="h-3 w-3" />
            引用
          </button>
        )}

        <div className="mb-1 flex items-center gap-2 text-xs opacity-80">
          {!isUser && <SenderAvatar name={msg.sender || 'AI'} avatarUrl={getAvatarUrl(msg.sender || '')} size="sm" />}
          <span className="font-semibold">{msg.sender || 'agent'}</span>
          {isUser ? (
            <span className="rounded px-2 py-0.5 bg-white/20 text-white font-medium">You</span>
          ) : isAiSender(msg.sender || '') ? (
            <>
              <span className="rounded px-2 py-0.5 bg-blue-100 text-blue-700 font-medium">AI</span>
              <OwnerChip ownerUserId={msg.userId} currentUser={user} resolveName={resolveUserName} />
            </>
          ) : (
            <span className="rounded px-2 py-0.5 bg-purple-100 text-purple-700 font-medium">User</span>
          )}
          {msg.timestamp && (
            <span className="ml-auto text-warm-400 whitespace-nowrap">{formatTimestamp(msg.timestamp)}</span>
          )}
          {showCursor && <span className="inline-block h-4 w-0.5 animate-pulse bg-primary-500" />}
        </div>
        {isUser ? (
          <div className="whitespace-pre-wrap leading-7">
            {safeContent}
            {msg.attachments && msg.attachments.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5 border-t border-white/20 pt-2">
                {msg.attachments.map((f, i) => (
                  <span key={i} className="inline-flex items-center gap-1 rounded bg-white/20 px-2 py-0.5 text-xs">
                    <FileIcon category={f.category || 'unknown'} size={3} />
                    <span className="max-w-[140px] truncate">{f.name}</span>
                    <span className="opacity-70">{formatSize(f.size)}</span>
                  </span>
                ))}
              </div>
            )}
            {showCursor && <span className="ml-0.5 inline-block h-5 w-0.5 animate-pulse bg-primary-500 align-text-bottom" />}
          </div>
        ) : (
          <div className="leading-7">
            {segments.map((seg, si) => {
              if (seg.type === 'think') {
                return <ThinkingPanel key={si} content={seg.content} isStreaming={!!showCursor} isComplete={seg.isComplete} />;
              }
              const cleanText = seg.content.replace('【正式回复】\n', '');
              const diffIdx = cleanText.indexOf('diff --git ');
              if (diffIdx >= 0) {
                const before = cleanText.slice(0, diffIdx).trim();
                const diffContent = cleanText.slice(diffIdx);
                return (
                  <div key={si}>
                    {seg.content.includes('【正式回复】') ? null : <div className="mb-2 text-xs font-semibold text-warm-500">【正式回复】</div>}
                    {before && <MarkdownRenderer content={before} />}
                    <CodeReviewPanel content={diffContent} />
                  </div>
                );
              }
              if (isCodeGenOutput(cleanText)) {
                return (
                  <div key={si}>
                    {seg.content.includes('【正式回复】') ? null : <div className="mb-2 text-xs font-semibold text-warm-500">【正式回复】</div>}
                    <CodeGenResultPanel content={cleanText} />
                  </div>
                );
              }
              return (
                <div key={si}>
                  {seg.content.includes('【正式回复】') ? null : <div className="mb-2 text-xs font-semibold text-warm-500">【正式回复】</div>}
                  <MarkdownRenderer content={cleanText} />
                </div>
              );
            })}
            {showCursor && <span className="ml-0.5 inline-block h-5 w-0.5 animate-pulse bg-primary-500 align-text-bottom" />}
          </div>
        )}
        {msg.guardrailResult?.flags?.length ? <SafetyBlockAlert result={msg.guardrailResult} /> : null}
      </div>
    </div>
  );
});

// ── Master MessageList ─────────────────────────────────────────────────

interface MessageListProps {
  messages: Message[];
  user: User | null;
  generated: GeneratedData | null;
  onCommit: () => void;
  messagesContainerRef?: React.RefObject<HTMLElement | null>;
  bottomRef?: React.RefObject<HTMLDivElement | null>;
  onQuoteMessage?: (msg: Message, selectedText?: string) => void;
  onSendPMEvent?: (event: Record<string, unknown>) => void;
  agents?: Agent[];
  sessionId?: string;
}

const EMPTY_USER_MAP = new Map<string, string>();

const MessageList = memo(function MessageList({
  messages, user, generated, onCommit, messagesContainerRef, bottomRef,
  onQuoteMessage, onSendPMEvent, agents, sessionId,
}: MessageListProps) {
  const presenceUsers = useMemo(() => {
    if (!sessionId) return EMPTY_USER_MAP;
    try {
      return new Map(
        getPresenceStore().getUsers(sessionId).map((u) => [u.userId, u.name] as const),
      );
    } catch {
      return EMPTY_USER_MAP;
    }
  }, [sessionId, messages.length]);

  const resolveUserName = useCallback((uid: string | undefined): string | null => {
    if (!uid) return null;
    if (user?.id && uid === user.id) return user.name || '我';
    const fromPresence = presenceUsers.get(uid);
    if (fromPresence) return fromPresence;
    return uid.length > 8 ? uid.slice(0, 8) + '…' : uid;
  }, [user, presenceUsers]);

  const agentAvatarMap = useMemo(() => {
    if (!agents || agents.length === 0) return new Map<string, string>();
    const map = new Map<string, string>();
    for (const a of agents) {
      const url = (a.avatarUrl || '').trim();
      if (!url) continue;
      map.set(a.agentId, url);
      if (a.displayName && a.displayName !== a.agentId) {
        map.set(a.displayName, url);
        map.set(a.displayName.toLowerCase(), url);
      }
      map.set(a.agentId.toLowerCase(), url);
    }
    return map;
  }, [agents]);

  // Stable message keys using messageId > id > timestamp+index
  const getMsgKey = useCallback((msg: Message, index: number): string => {
    return msg.messageId || msg.id || `msg-${index}-${msg.timestamp}`;
  }, []);

  return (
    <section ref={messagesContainerRef as React.LegacyRef<HTMLElement>} className="flex-1 overflow-auto p-6 chat-scroll-container">
      {messages.map((msg, index) => (
        <div className="chat-message-row" key={getMsgKey(msg, index)}>
          <MessageRow
            msg={msg}
            msgKey={getMsgKey(msg, index)}
            user={user}
            agentAvatarMap={agentAvatarMap}
            resolveUserName={resolveUserName}
            onQuoteMessage={onQuoteMessage}
            onSendPMEvent={onSendPMEvent}
          />
        </div>
      ))}
      {generated && <GeneratedFilesPanel generated={generated} onCommit={onCommit} />}
      <div ref={bottomRef as React.LegacyRef<HTMLDivElement>} />
    </section>
  );
});

export default MessageList;
