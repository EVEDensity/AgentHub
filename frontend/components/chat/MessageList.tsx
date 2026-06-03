import { memo, type JSX } from 'react';
import type { ContentSegment, GeneratedData, Message, User } from '../../types';
import DiffBubble from './DiffBubble';
import CodeReviewPanel from './CodeReviewPanel';
import CodeGenResultPanel, { isCodeGenOutput } from './CodeGenResultPanel';
import FidelityScore from './FidelityScore';
import MarkdownRenderer from './MarkdownRenderer';
import ThinkingPanel from './ThinkingPanel';
import ToolCallBubble from './ToolCallBubble';
import GeneratedFilesPanel from '../git/GeneratedFilesPanel';

function normalizeStructuredStreamContent(content: string): string {
  return content ? content.replace(/<\/?thinking>/g, '') : content;
}

function parseThinkSegments(content: string): ContentSegment[] {
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

interface MessageListProps {
  messages: Message[];
  user: User | null;
  generated: GeneratedData | null;
  onCommit: () => void;
  messagesContainerRef: React.RefObject<HTMLElement | null>;
  bottomRef: React.RefObject<HTMLDivElement | null>;
}

const MessageList = memo(function MessageList({ messages, user, generated, onCommit, messagesContainerRef, bottomRef }: MessageListProps) {
  function renderMessage(msg: Message, index: number): JSX.Element {
    // 用户消息判定：
    // 1. sender === 'user' (本地立即创建的消息)
    // 2. sender === 当前登录用户的 name
    // 3. sender 与 current user.name 一致（忽略大小写）
    // 4. fallback：如果 user 为空但消息没有 agent_id 前缀（兼容旧会话）
    const userName = (user?.name || '').trim();
    const sender = (msg.sender || '').trim();
    const isUser = sender === 'user'
      || (userName !== '' && sender === userName)
      || (userName !== '' && sender.toLowerCase() === userName.toLowerCase());
    const isToolCall = msg.type === 'tool_call' || msg.type === 'tool_result';
    const isCode = msg.type === 'code' || msg.type === 'diff';
    const badge = msg.type || 'text';
    const showCursor = msg.isStreaming;

    // ── Streaming thinking placeholder → show animated thinking indicator ──
    // This covers both the initial "empty" placeholder and subsequent
    // agent_thinking updates that carry phase details (e.g. "正在调用工具: ...")
    const isThinking = showCursor && !isUser && !isCode && !isToolCall;
    if (isThinking && (!msg.content || msg.content.startsWith('正在'))) {
      const statusText = msg.content || '模型正在思考中...';
      return (
        <div key={`${msg.timestamp}-${index}`} className="mb-4 flex justify-start">
          <div className="max-w-[85%] rounded-2xl px-4 py-3 bg-white border border-blue-200 shadow-sm">
            <div className="mb-1 flex items-center gap-2 text-xs opacity-80">
              <span className="font-semibold text-warm-700">{msg.sender || 'agent'}</span>
              <span className="rounded px-2 py-0.5 bg-blue-50 text-blue-600 text-xs">思考中</span>
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

    if (isToolCall) {
      return (
        <div key={`${msg.timestamp}-${index}`} className="mb-3">
          <ToolCallBubble
            calls={msg.toolCallData?.calls}
            results={msg.toolResultData?.results}
            isStreaming={showCursor}
          />
        </div>
      );
    }

    if (isCode) {
      // Auto-detect: multi-file git diff → CodeReviewPanel, else Monaco DiffBubble
      const isGitDiff = msg.content.includes('diff --git ');
      return (
        <div key={`${msg.timestamp}-${index}`} className="-mx-6 mb-4 px-6">
          <div className="mb-2 flex items-center gap-2 text-xs text-warm-500">
            <span className="font-semibold text-warm-700">{msg.sender || 'agent'}</span>
            <span className="tag tag-warm">{badge}</span>
            {showCursor && <span className="inline-block h-4 w-0.5 animate-pulse bg-primary-500" />}
          </div>
          {isGitDiff ? (
            <CodeReviewPanel content={msg.content} />
          ) : (
            <DiffBubble value={msg.content} />
          )}
          {msg.fidelityScore ? <FidelityScore score={msg.fidelityScore} /> : null}
        </div>
      );
    }
    return (
      <div key={`${msg.timestamp}-${index}`} className={`mb-4 flex ${isUser ? 'justify-end' : 'justify-start'}`}>
        <div className={`max-w-[85%] rounded-2xl px-4 py-3 shadow-sm ${isUser ? 'bg-primary-500 text-white' : 'bg-white text-warm-800 border border-warm-150'}`}>
          <div className="mb-1 flex items-center gap-2 text-xs opacity-80">
            <span className="font-semibold">{msg.sender || 'agent'}</span>
            <span className={`rounded px-2 py-0.5 ${isUser ? 'bg-white/20 text-white' : 'bg-warm-100 text-warm-600'}`}>{badge}</span>
            {showCursor && <span className="inline-block h-4 w-0.5 animate-pulse bg-primary-500" />}
          </div>
          {isUser ? (
            <div className="whitespace-pre-wrap leading-7">
              {msg.content}
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
              {parseThinkSegments(normalizeStructuredStreamContent(msg.content)).map((seg, si) => {
                if (seg.type === 'think') {
                  return <ThinkingPanel key={si} content={seg.content} isStreaming={!!showCursor} isComplete={seg.isComplete} />;
                }
                // Detect git diff blocks within text and render CodeReviewPanel
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
                // Detect CodeGen JSON output ({"files":[...]}) and render structured panel
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
          {!isUser && msg.fidelityScore ? <FidelityScore score={msg.fidelityScore} /> : null}
        </div>
      </div>
    );
  }

  return (
    <section ref={messagesContainerRef} className="flex-1 overflow-auto p-6">
      {messages.map(renderMessage)}
      {generated && <GeneratedFilesPanel generated={generated} onCommit={onCommit} />}
      <div ref={bottomRef} />
    </section>
  );
});

export default MessageList;
