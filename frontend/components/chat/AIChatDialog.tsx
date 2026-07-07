'use client';

import { useState, useRef, useEffect, useCallback, type JSX, type KeyboardEvent } from 'react';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
}

interface AIChatDialogProps {
  open: boolean;
  onClose: () => void;
  addConsoleLog?: (tag: string, msg: string, tagClass?: string) => void;
}

const MODELS = ['Claude Opus 4.8', 'Claude Sonnet 4', 'GPT-4o', 'DeepSeek-V3', 'Gemini 2.5'] as const;

const QUICK_ACTIONS = [
  { label: '分析代码', prompt: '请帮我分析这段代码的逻辑和潜在问题' },
  { label: '生成报告', prompt: '请根据当前数据生成一份分析报告' },
  { label: '调试错误', prompt: '我遇到了一个错误，请帮我分析和修复' },
  { label: '优化性能', prompt: '请帮我分析当前系统的性能瓶颈并提出优化建议' },
  { label: '编写测试', prompt: '请帮我为这个模块编写单元测试' },
  { label: '代码审查', prompt: '请对当前代码进行审查并提出改进建议' },
];

const WELCOME_MESSAGE: ChatMessage = {
  id: 'welcome',
  role: 'assistant',
  content:
    '你好！我是 **AgentHub AI 助手**，基于 Claude 多智能体协作架构。\n\n我可以帮你：\n- 🔍 分析代码逻辑与架构设计\n- 📊 生成数据报告与可视化建议\n- 🐛 调试错误并给出修复方案\n- ⚡ 优化性能瓶颈\n- 🧪 编写和审查测试用例\n\n请随时提问，我会调动适合的智能体来协助你。',
  timestamp: '',
};

export default function AIChatDialog({ open, onClose, addConsoleLog }: AIChatDialogProps): JSX.Element {
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME_MESSAGE]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [selectedModel, setSelectedModel] = useState<string>(MODELS[0]);
  const [showModelMenu, setShowModelMenu] = useState(false);
  const [minimized, setMinimized] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Focus input on open
  useEffect(() => {
    if (open && !minimized) {
      setTimeout(() => inputRef.current?.focus(), 150);
    }
  }, [open, minimized]);

  // Keyboard shortcut
  useEffect(() => {
    const handler = (e: globalThis.KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'j') {
        e.preventDefault();
        if (open) {
          onClose();
        } else {
          // open handled by parent
        }
      }
      if (e.key === 'Escape' && open) {
        if (minimized) {
          setMinimized(false);
        } else {
          onClose();
        }
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onClose, minimized]);

  const pad2 = (n: number) => (n < 10 ? '0' : '') + n;
  const getTime = () => {
    const d = new Date();
    return `${d.getHours()}:${pad2(d.getMinutes())}`;
  };

  const addMessage = useCallback((role: 'user' | 'assistant' | 'system', content: string) => {
    const msg: ChatMessage = {
      id: `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      role,
      content,
      timestamp: getTime(),
    };
    setMessages((prev) => [...prev, msg]);
    return msg;
  }, []);

  const simulateStreaming = useCallback(
    (fullText: string, msgId: string) => {
      setIsStreaming(true);
      let idx = 0;
      const charsPerTick = 3;
      const tickMs = 15;

      // Initialize message with empty content
      setMessages((prev) =>
        prev.map((m) => (m.id === msgId ? { ...m, content: '' } : m)),
      );

      const interval = setInterval(() => {
        idx += charsPerTick;
        if (idx >= fullText.length) {
          setMessages((prev) =>
            prev.map((m) => (m.id === msgId ? { ...m, content: fullText } : m)),
          );
          clearInterval(interval);
          setIsStreaming(false);
        } else {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === msgId ? { ...m, content: fullText.slice(0, idx) + '▊' } : m,
            ),
          );
        }
      }, tickMs);

      return () => clearInterval(interval);
    },
    [],
  );

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;

    setInput('');
    addMessage('user', trimmed);

    // Simulate AI thinking + response
    setTimeout(() => {
      const response = generateResponse(trimmed);
      const msg = addMessage('assistant', '');
      simulateStreaming(response, msg.id);

      addConsoleLog?.('AI', `请求: ${trimmed.slice(0, 40)}${trimmed.length > 40 ? '…' : ''}`, 'gold');
    }, 400 + Math.random() * 600);
  }, [input, isStreaming, addMessage, simulateStreaming, addConsoleLog]);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleQuickAction = (prompt: string) => {
    setInput(prompt);
    inputRef.current?.focus();
  };

  const handleClearChat = () => {
    setMessages([WELCOME_MESSAGE]);
    addConsoleLog?.('SYS', '对话已清空', 'info');
  };

  const handleToggleSidebar = () => {
    window.dispatchEvent(new CustomEvent('toggle-sidebar'));
  };

  if (!open) return <></>;

  return (
    <>
      {/* Backdrop */}
      <div
        className="cmd-backdrop"
        style={{ zIndex: 80 }}
        onClick={minimized ? undefined : onClose}
      />

      {/* Dialog */}
      <div
        ref={dialogRef}
        className="cmd-modal"
        style={{
          zIndex: 81,
          width: '720px',
          maxHeight: minimized ? 'auto' : '720px',
          height: minimized ? 'auto' : '720px',
          top: minimized ? 'auto' : '50%',
          bottom: minimized ? '100px' : 'auto',
          transform: minimized ? 'translateX(-50%)' : 'translate(-50%, -50%)',
          transition: 'all 0.25s cubic-bezier(0.16, 1, 0.3, 1)',
        }}
      >
        {/* Header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '0 16px',
            height: 48,
            borderBottom: '1px solid rgb(var(--warm-200))',
            flexShrink: 0,
            cursor: minimized ? 'pointer' : 'default',
          }}
          onClick={minimized ? () => setMinimized(false) : undefined}
        >
          {/* Logo */}
          <div
            style={{
              width: 28,
              height: 28,
              borderRadius: 6,
              background: 'rgb(var(--primary-500))',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#121418',
              fontWeight: 700,
              fontSize: 12,
              flexShrink: 0,
            }}
          >
            AH
          </div>

          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'rgb(var(--warm-900))', lineHeight: 1.3 }}>
              AgentHub AI 对话
            </div>
            <div style={{ fontSize: 10, color: 'rgb(var(--warm-500))', fontFamily: 'monospace' }}>
              {selectedModel} · {messages.length} 条消息
            </div>
          </div>

          {/* Model selector */}
          <div style={{ position: 'relative' }}>
            <button
              className="admin-header-action-btn"
              onClick={(e) => {
                e.stopPropagation();
                setShowModelMenu((v) => !v);
              }}
              style={{ fontSize: 11, gap: 3 }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M12 2L2 7l10 5 10-5-10-5z" />
                <path d="M2 17l10 5 10-5" />
                <path d="M2 12l10 5 10-5" />
              </svg>
              {selectedModel.split(' ')[0]}
            </button>

            {showModelMenu && (
              <>
                <div
                  style={{
                    position: 'fixed',
                    inset: 0,
                    zIndex: 82,
                  }}
                  onClick={() => setShowModelMenu(false)}
                />
                <div
                  style={{
                    position: 'absolute',
                    top: '100%',
                    right: 0,
                    marginTop: 4,
                    zIndex: 83,
                    background: 'rgb(var(--surface-overlay))',
                    border: '1px solid rgb(var(--warm-200))',
                    borderRadius: 6,
                    padding: 4,
                    minWidth: 180,
                    boxShadow: '0 8px 24px rgba(0,0,0,0.45)',
                  }}
                >
                  {MODELS.map((m) => (
                    <button
                      key={m}
                      style={{
                        display: 'block',
                        width: '100%',
                        textAlign: 'left',
                        padding: '8px 14px',
                        borderRadius: 6,
                        border: 'none',
                        background: m === selectedModel ? 'rgba(34,163,201,0.1)' : 'transparent',
                        color: m === selectedModel ? 'rgb(var(--primary-500))' : 'rgb(var(--warm-700))',
                        fontSize: 12,
                        cursor: 'pointer',
                        fontFamily: 'inherit',
                      }}
                      onClick={() => {
                        setSelectedModel(m);
                        setShowModelMenu(false);
                      }}
                    >
                      {m}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>

          {/* Sidebar toggle */}
          <button
            className="admin-console-act"
            title="折叠/展开侧边栏"
            onClick={handleToggleSidebar}
            style={{ flexShrink: 0 }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <line x1="9" y1="3" x2="9" y2="21" />
            </svg>
          </button>

          {/* Actions */}
          <button
            className="admin-console-act"
            title="清空对话"
            onClick={handleClearChat}
            style={{ flexShrink: 0 }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            </svg>
          </button>

          <button
            className="admin-console-act"
            title={minimized ? '展开' : '最小化'}
            onClick={() => setMinimized((v) => !v)}
            style={{ flexShrink: 0 }}
          >
            {minimized ? '□' : '−'}
          </button>

          <button
            className="admin-console-act"
            title="关闭 (Esc)"
            onClick={onClose}
            style={{ flexShrink: 0 }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* Body (hidden when minimized) */}
        {!minimized && (
          <>
            {/* Messages area */}
            <div
              style={{
                flex: 1,
                overflowY: 'auto',
                padding: '16px 20px',
                display: 'flex',
                flexDirection: 'column',
                gap: 12,
                minHeight: 0,
              }}
            >
              {messages.map((msg) => (
                <div
                  key={msg.id}
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
                    animation: 'logEntryIn 0.2s cubic-bezier(0.19, 1, 0.22, 1)',
                  }}
                >
                  {/* Sender label */}
                  <div
                    style={{
                      fontSize: 10,
                      color: 'rgb(var(--warm-500))',
                      marginBottom: 3,
                      fontFamily: 'monospace',
                      letterSpacing: '0.04em',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                    }}
                  >
                    {msg.role === 'assistant' ? (
                      <>
                        <span
                          style={{
                            width: 6,
                            height: 6,
                            borderRadius: '50%',
                            background: 'rgb(var(--primary-500))',
                            display: 'inline-block',
                          }}
                        />
                        AgentHub AI
                      </>
                    ) : msg.role === 'user' ? (
                      '你'
                    ) : (
                      '系统'
                    )}
                    {msg.timestamp && (
                      <span style={{ color: 'rgb(var(--warm-500))', opacity: 0.6 }}>{msg.timestamp}</span>
                    )}
                  </div>

                  {/* Bubble */}
                  <div
                    style={{
                      maxWidth: '85%',
                      padding: '10px 16px',
                      borderRadius: msg.role === 'user' ? '8px 8px 2px 8px' : '8px 8px 8px 2px',
                      background:
                        msg.role === 'user'
                          ? 'rgb(var(--primary-500))'
                          : msg.role === 'system'
                          ? 'rgb(var(--warm-150))'
                          : 'rgb(var(--warm-100))',
                      color:
                        msg.role === 'user'
                          ? '#121418'
                          : msg.role === 'system'
                          ? 'rgb(var(--warm-600))'
                          : 'rgb(var(--warm-900))',
                      border:
                        msg.role === 'assistant'
                          ? '1px solid rgb(var(--warm-200))'
                          : 'none',
                      fontSize: 13,
                      lineHeight: 1.65,
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                      fontFamily:
                        msg.role === 'assistant' && msg.content.includes('`')
                          ? "'JetBrains Mono', monospace"
                          : 'inherit',
                    }}
                  >
                    {msg.content || (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        <span
                          style={{
                            width: 6,
                            height: 6,
                            borderRadius: '50%',
                            background: 'rgb(var(--primary-500))',
                            display: 'inline-block',
                            animation: 'breathe 1s ease-in-out infinite',
                          }}
                        />
                        <span
                          style={{
                            width: 6,
                            height: 6,
                            borderRadius: '50%',
                            background: 'rgb(var(--primary-500))',
                            display: 'inline-block',
                            animation: 'breathe 1s ease-in-out 0.2s infinite',
                          }}
                        />
                        <span
                          style={{
                            width: 6,
                            height: 6,
                            borderRadius: '50%',
                            background: 'rgb(var(--primary-500))',
                            display: 'inline-block',
                            animation: 'breathe 1s ease-in-out 0.4s infinite',
                          }}
                        />
                      </span>
                    )}
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </div>

            {/* Quick actions */}
            {messages.length <= 1 && (
              <div
                style={{
                  display: 'flex',
                  gap: 6,
                  flexWrap: 'wrap',
                  padding: '0 20px 8px',
                  flexShrink: 0,
                }}
              >
                {QUICK_ACTIONS.map((action) => (
                  <button
                    key={action.label}
                    onClick={() => handleQuickAction(action.prompt)}
                    style={{
                      padding: '6px 14px',
                      borderRadius: 16,
                      border: '1px solid rgb(var(--warm-200))',
                      background: 'transparent',
                      color: 'rgb(var(--warm-600))',
                      fontSize: 12,
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                      transition: 'all 0.12s',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.borderColor = 'rgb(var(--primary-500))';
                      e.currentTarget.style.color = 'rgb(var(--primary-500))';
                      e.currentTarget.style.background = 'rgba(34,163,201,0.06)';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = 'rgb(var(--warm-200))';
                      e.currentTarget.style.color = 'rgb(var(--warm-600))';
                      e.currentTarget.style.background = 'transparent';
                    }}
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            )}

            {/* Input area */}
            <div
              style={{
                borderTop: '1px solid rgb(var(--warm-200))',
                padding: '12px 16px',
                flexShrink: 0,
                display: 'flex',
                gap: 10,
                alignItems: 'flex-end',
              }}
            >
              {/* Attachment button */}
              <button
                className="admin-console-act"
                title="附加文件"
                style={{ marginBottom: 0 }}
                onClick={() => addConsoleLog?.('SYS', '文件上传功能 (待接入)', 'info')}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                </svg>
              </button>

              {/* Text area */}
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="输入消息… (Enter 发送，Shift+Enter 换行)"
                rows={1}
                style={{
                  flex: 1,
                  background: 'rgb(var(--warm-100))',
                  border: '1px solid rgb(var(--warm-200))',
                  borderRadius: 6,
                  padding: '10px 14px',
                  color: 'rgb(var(--warm-900))',
                  fontSize: 13,
                  fontFamily: 'inherit',
                  outline: 'none',
                  resize: 'none',
                  maxHeight: 120,
                  lineHeight: 1.5,
                }}
                onFocus={(e) => {
                  e.currentTarget.style.borderColor = 'rgb(var(--primary-500))';
                }}
                onBlur={(e) => {
                  e.currentTarget.style.borderColor = 'rgb(var(--warm-200))';
                }}
              />

              {/* Send button */}
              <button
                onClick={handleSend}
                disabled={!input.trim() || isStreaming}
                style={{
                  padding: '10px 22px',
                  borderRadius: 6,
                  border: 'none',
                  background: input.trim() && !isStreaming ? 'rgb(var(--primary-500))' : 'rgb(var(--warm-200))',
                  color: input.trim() && !isStreaming ? '#121418' : 'rgb(var(--warm-500))',
                  fontSize: 14,
                  fontWeight: 510,
                  cursor: input.trim() && !isStreaming ? 'pointer' : 'not-allowed',
                  fontFamily: 'inherit',
                  transition: 'background 0.12s, color 0.12s',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                {isStreaming ? (
                  <>
                    <span
                      style={{
                        width: 12,
                        height: 12,
                        borderRadius: '50%',
                        border: '2px solid rgb(var(--warm-400))',
                        borderTopColor: 'rgb(var(--primary-500))',
                        animation: 'spin 0.6s linear infinite',
                        display: 'inline-block',
                      }}
                    />
                    响应中
                  </>
                ) : (
                  <>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="22" y1="2" x2="11" y2="13" />
                      <polygon points="22 2 15 22 11 13 2 9 22 2" />
                    </svg>
                    发送
                  </>
                )}
              </button>
            </div>
          </>
        )}

        {/* Minimized footer hint */}
        {minimized && (
          <div
            style={{
              padding: '8px 16px',
              borderTop: '1px solid rgb(var(--warm-200))',
              fontSize: 11,
              color: 'rgb(var(--warm-500))',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: 'rgb(var(--success-500))',
                display: 'inline-block',
              }}
            />
            {isStreaming ? '正在响应…' : '点击展开对话 · Ctrl+J 切换'}
          </div>
        )}
      </div>
    </>
  );
}

// ── Simulated AI response generator ──────────────────────────────────
function generateResponse(prompt: string): string {
  const lower = prompt.toLowerCase();

  if (lower.includes('分析') || lower.includes('代码')) {
    return `好的，我来分析这段代码：

**代码结构分析：**
1. **架构层面**：当前代码采用了模块化设计，职责分离清晰
2. **潜在问题**：
   - 缺少错误边界处理，异常情况可能导致崩溃
   - 部分函数没有类型注解，类型安全性不足
   - 存在潜在的 N+1 查询问题

**改进建议：**
\`\`\`typescript
// 1. 添加错误边界
try {
  const result = await processData(input);
  return { success: true, data: result };
} catch (error) {
  logger.error('数据处理失败', { error, input });
  return { success: false, error: '处理异常，请稍后重试' };
}
\`\`\`

需要我进一步深入分析某个具体方面吗？`;
  }

  if (lower.includes('报告') || lower.includes('数据')) {
    return `根据当前数据，我生成了以下分析报告：

---

## 📊 数据分析报告

| 指标 | 当前值 | 环比变化 | 趋势 |
|------|--------|----------|------|
| 系统吞吐量 | 1,247 RPM | +12.4% | 📈 上升 |
| P95 延迟 | 1.8s | -0.3s | 📉 改善 |
| 成功率 | 98.7% | +0.4pp | 📈 上升 |

**关键发现：**
- 自动扩容策略在高峰时段有效降低了延迟
- 模型响应质量评分稳定在 4.2/5.0
- 建议关注检索模块的 P50 延迟上升趋势

需要我将报告导出为 PDF 或 Markdown 格式吗？`;
  }

  if (lower.includes('错误') || lower.includes('调试') || lower.includes('debug')) {
    return `我来帮你分析这个错误：

**错误定位：**
根据描述，错误可能出现在以下几个方面：

1. **类型不匹配**：检查传入参数是否与函数签名一致
2. **异步处理**：确认 \`await\` 是否正确使用了 Promise
3. **状态管理**：检查 store 更新是否触发了无限循环

**修复方案：**
\`\`\`typescript
// 问题代码
const data = store.getState().items; // 可能为 undefined

// 修复后
const data = store.getState().items ?? [];
if (!data.length) {
  console.warn('数据为空，使用默认值');
  return DEFAULT_ITEMS;
}
\`\`\`

建议你检查控制台中的完整堆栈跟踪，确认错误的具体来源。需要我帮你设置断点调试吗？`;
  }

  if (lower.includes('测试') || lower.includes('test')) {
    return `我为你编写了以下测试用例：

\`\`\`typescript
import { describe, it, expect, vi } from 'vitest';
import { processData, validateInput } from './module';

describe('processData', () => {
  it('应该正确处理有效输入', async () => {
    const result = await processData({ id: 'test-1', value: 42 });
    expect(result.success).toBe(true);
    expect(result.data).toBeDefined();
  });

  it('应该拒绝无效输入并返回错误', async () => {
    const result = await processData({ id: '', value: -1 });
    expect(result.success).toBe(false);
    expect(result.error).toContain('无效');
  });

  it('应该在超时时优雅降级', async () => {
    vi.useFakeTimers();
    const promise = processData({ id: 'slow', value: 0 });
    vi.advanceTimersByTime(5000);
    const result = await promise;
    expect(result.success).toBe(false);
    vi.useRealTimers();
  });
});
\`\`\`

测试覆盖率预计可达 85%+。需要我补充边界情况测试吗？`;
  }

  if (lower.includes('优化') || lower.includes('性能')) {
    return `我分析了系统性能，以下是优化建议：

**🔍 性能瓶颈分析：**

1. **渲染性能**：部分组件缺少 \`React.memo\`，导致不必要的重渲染
2. **数据加载**：建议实现虚拟滚动 (\`@tanstack/react-virtual\`)
3. **缓存策略**：API 响应缺少客户端缓存

**优化方案（按优先级排序）：**

| 优先级 | 优化项 | 预期收益 | 工作量 |
|--------|--------|----------|--------|
| P0 | 添加 React.memo + useMemo | -40% 渲染时间 | 2h |
| P1 | 虚拟滚动列表 | -60% DOM 节点 | 4h |
| P2 | SWR/React Query 缓存 | -30% API 调用 | 3h |

需要我具体实现某个优化吗？`;
  }

  // Default response
  return `感谢你的提问！关于 **"${prompt.slice(0, 30)}${prompt.length > 30 ? '…' : ''}"**：

我理解你的需求。作为 AgentHub AI 助手，我可以调动多个专业智能体来协作完成任务：

- **Router** 会分析你的请求并分派给最合适的智能体
- **Planner** 会制定详细的执行计划
- **Executor** 会调用相关工具执行具体步骤
- **Critic** 会审查结果质量

请告诉我更多细节，我会提供更精准的帮助。你也可以尝试以下快速操作：分析代码、生成报告、调试错误、优化性能、编写测试。`;
}
