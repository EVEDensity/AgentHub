import type { GetServerSideProps } from 'next';
import { useState, useRef, useEffect, useCallback } from 'react';
import Head from 'next/head';
import { Send, Bot, User, Sparkles } from 'lucide-react';

// ── Types ──────────────────────────────────────────────────────────────

interface BotConfig {
  botId: string;
  name: string;
  welcomeMessage: string;
  placeholder: string;
  themeColor: string;
  logoUrl: string;
  suggestedQuestions: string[];
  poweredBy: string;
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
}

interface PageProps {
  botId: string;
  embed: boolean;
  config: BotConfig | null;
  error: string | null;
}

// ── SSR: Fetch bot config server-side ──────────────────────────────────

export const getServerSideProps: GetServerSideProps<PageProps> = async (ctx) => {
  const botId = (ctx.params?.botId as string) || 'default';
  const embed = ctx.query.embed === 'true';

  // Resolve API URL — use the Go gateway if reachable from SSR, fallback to
  // the same Next.js process as a proxy.
  const apiBase = process.env.GO_GATEWAY_URL || 'http://127.0.0.1:8081';

  try {
    const res = await fetch(`${apiBase}/api/public/bots/${botId}`, {
      headers: { Accept: 'application/json' },
      // Don't block the page render on a slow backend
      signal: AbortSignal.timeout(3000),
    });
    if (res.ok) {
      const config: BotConfig = await res.json();
      return { props: { botId, embed, config, error: null } };
    }
    return { props: { botId, embed, config: null, error: `Bot not found (${res.status})` } };
  } catch {
    // If the API is unreachable during SSR, render with defaults.
    // The client will retry via fetch on mount.
    return { props: { botId, embed, config: null, error: null } };
  }
};

// ── Defaults ────────────────────────────────────────────────────────────

const DEFAULT_CONFIG: BotConfig = {
  botId: 'default',
  name: 'AI Assistant',
  welcomeMessage: '你好！我是 AI 助手，有什么可以帮你的？',
  placeholder: '输入消息...',
  themeColor: '#6366f1',
  logoUrl: '',
  suggestedQuestions: ['你能做什么？', '介绍一下你自己', '帮我分析一个问题'],
  poweredBy: 'AgentHub',
};

function generateId(): string {
  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

// ── Page Component ─────────────────────────────────────────────────────

export default function BotAppPage({ botId, embed, config, error }: PageProps) {
  const cfg = config || DEFAULT_CONFIG;

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // ── Send message ─────────────────────────────────────────────────
  const sendMessage = useCallback(
    async (text: string) => {
      if (!text.trim() || sending) return;

      const userMsg: ChatMessage = {
        id: generateId(),
        role: 'user',
        content: text.trim(),
        timestamp: Date.now(),
      };

      setMessages((prev) => [...prev, userMsg]);
      setInput('');
      setSending(true);

      // Determine API endpoint — route through the same origin in prod to
      // avoid CORS, or go directly to the gateway.
      const apiBase = process.env.NEXT_PUBLIC_API_BASE || '/api';
      const chatURL = `${apiBase}/v1/public/chat`;

      try {
        const res = await fetch(chatURL, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            // Include API key if configured (for authenticated bots)
            ...(process.env.NEXT_PUBLIC_BOT_API_KEY
              ? { Authorization: `Bearer ${process.env.NEXT_PUBLIC_BOT_API_KEY}` }
              : {}),
          },
          body: JSON.stringify({
            message: text.trim(),
            agent_id: botId,
            stream: false,
          }),
          signal: AbortSignal.timeout(30000),
        });

        if (res.ok) {
          const data = await res.json();
          const assistantMsg: ChatMessage = {
            id: generateId(),
            role: 'assistant',
            content: data.reply || data.content || '抱歉，我暂时无法回复。',
            timestamp: Date.now(),
          };
          setMessages((prev) => [...prev, assistantMsg]);
        } else {
          const errMsg: ChatMessage = {
            id: generateId(),
            role: 'system',
            content: `发送失败 (${res.status})，请稍后重试。`,
            timestamp: Date.now(),
          };
          setMessages((prev) => [...prev, errMsg]);
        }
      } catch {
        const errMsg: ChatMessage = {
          id: generateId(),
          role: 'system',
          content: '网络请求失败，请检查连接后重试。',
          timestamp: Date.now(),
        };
        setMessages((prev) => [...prev, errMsg]);
      } finally {
        setSending(false);
        inputRef.current?.focus();
      }
    },
    [botId, sending],
  );

  // ── Handle keyboard submit ────────────────────────────────────────
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  // ── Theme CSS custom properties ───────────────────────────────────
  const themeStyle = {
    '--bot-primary': cfg.themeColor,
    '--bot-primary-light': `${cfg.themeColor}1a`, // 10% opacity
    '--bot-primary-dark': `${cfg.themeColor}dd`,
  } as React.CSSProperties;

  // ── Render ────────────────────────────────────────────────────────
  const pageTitle = `${cfg.name} — Powered by AgentHub`;

  return (
    <>
      <Head>
        <title>{cfg.name}</title>
        <meta name="description" content={`Chat with ${cfg.name}`} />
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
        {embed && <meta name="x-embed-mode" content="true" />}
      </Head>

      <div
        className="bot-app-container"
        style={{
          ...themeStyle,
          display: 'flex',
          flexDirection: 'column',
          height: embed ? '100vh' : '100dvh',
          maxWidth: embed ? '100%' : '896px',
          margin: embed ? 0 : '0 auto',
          fontFamily: "'Noto Sans SC', system-ui, -apple-system, sans-serif",
          background: '#fafbfc',
        }}
      >
        {/* ── Header ─────────────────────────────────────────────── */}
        {!embed && (
          <header
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              padding: '16px 20px',
              background: '#fff',
              borderBottom: '1px solid #e2e5ea',
              boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
              flexShrink: 0,
            }}
          >
            {cfg.logoUrl ? (
              <img
                src={cfg.logoUrl}
                alt={cfg.name}
                style={{ width: 36, height: 36, borderRadius: 8, objectFit: 'cover' }}
              />
            ) : (
              <div
                style={{
                  width: 36,
                  height: 36,
                  borderRadius: 8,
                  background: cfg.themeColor,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#fff',
                  fontSize: 18,
                  fontWeight: 600,
                }}
              >
                {cfg.name.charAt(0)}
              </div>
            )}
            <div>
              <h1 style={{ margin: 0, fontSize: 17, fontWeight: 600, color: '#111827' }}>
                {cfg.name}
              </h1>
              <span style={{ fontSize: 12, color: '#9ca3af' }}>
                Powered by {cfg.poweredBy}
              </span>
            </div>
          </header>
        )}

        {/* ── Error Banner ───────────────────────────────────────── */}
        {error && (
          <div
            style={{
              padding: '12px 20px',
              background: '#fef2f2',
              color: '#dc2626',
              fontSize: 14,
              flexShrink: 0,
            }}
          >
            ⚠️ {error}
          </div>
        )}

        {/* ── Messages ───────────────────────────────────────────── */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '20px 16px',
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
          }}
        >
          {/* Welcome card */}
          {messages.length === 0 && (
            <div
              style={{
                textAlign: 'center',
                padding: '40px 20px',
                animation: 'fadeIn 0.5s ease-out',
              }}
            >
              <div
                style={{
                  width: 64,
                  height: 64,
                  borderRadius: 16,
                  background: cfg.themeColor,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  margin: '0 auto 16px',
                  color: '#fff',
                  fontSize: 28,
                }}
              >
                {cfg.logoUrl ? (
                  <img
                    src={cfg.logoUrl}
                    alt=""
                    style={{ width: 48, height: 48, borderRadius: 12, objectFit: 'cover' }}
                  />
                ) : (
                  <Sparkles size={32} />
                )}
              </div>
              <h2
                style={{
                  margin: '0 0 8px',
                  fontSize: 22,
                  fontWeight: 700,
                  color: '#111827',
                }}
              >
                {cfg.name}
              </h2>
              <p style={{ margin: 0, fontSize: 15, color: '#6b7280', maxWidth: 400, marginInline: 'auto' }}>
                {cfg.welcomeMessage}
              </p>
              {/* Suggested questions */}
              <div
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: 8,
                  justifyContent: 'center',
                  marginTop: 20,
                }}
              >
                {cfg.suggestedQuestions.map((q) => (
                  <button
                    key={q}
                    onClick={() => sendMessage(q)}
                    disabled={sending}
                    style={{
                      padding: '8px 16px',
                      borderRadius: 20,
                      border: `1px solid ${cfg.themeColor}33`,
                      background: `${cfg.themeColor}0d`,
                      color: cfg.themeColor,
                      fontSize: 14,
                      cursor: 'pointer',
                      transition: 'all 0.2s',
                      whiteSpace: 'nowrap',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = cfg.themeColor;
                      e.currentTarget.style.color = '#fff';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = `${cfg.themeColor}0d`;
                      e.currentTarget.style.color = cfg.themeColor;
                    }}
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Chat messages */}
          {messages.map((msg) => (
            <div
              key={msg.id}
              style={{
                display: 'flex',
                gap: 10,
                alignItems: 'flex-start',
                flexDirection: msg.role === 'user' ? 'row-reverse' : 'row',
                animation: 'fadeIn 0.3s ease-out',
              }}
            >
              {/* Avatar */}
              <div
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: '50%',
                  flexShrink: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  background:
                    msg.role === 'user'
                      ? '#e5e7eb'
                      : msg.role === 'system'
                        ? '#fef2c7'
                        : cfg.themeColor,
                  color:
                    msg.role === 'user'
                      ? '#374151'
                      : msg.role === 'system'
                        ? '#92400e'
                        : '#fff',
                }}
              >
                {msg.role === 'user' ? (
                  <User size={16} />
                ) : msg.role === 'system' ? (
                  '!'
                ) : cfg.logoUrl ? (
                  <img
                    src={cfg.logoUrl}
                    alt=""
                    style={{ width: 24, height: 24, borderRadius: '50%' }}
                  />
                ) : (
                  <Bot size={16} />
                )}
              </div>

              {/* Bubble */}
              <div
                style={{
                  maxWidth: '75%',
                  padding: '10px 16px',
                  borderRadius: 16,
                  fontSize: 15,
                  lineHeight: 1.6,
                  wordBreak: 'break-word',
                  whiteSpace: 'pre-wrap',
                  background:
                    msg.role === 'user'
                      ? cfg.themeColor
                      : msg.role === 'system'
                        ? '#fef3c7'
                        : '#fff',
                  color:
                    msg.role === 'user'
                      ? '#fff'
                      : msg.role === 'system'
                        ? '#92400e'
                        : '#111827',
                  boxShadow:
                    msg.role === 'assistant'
                      ? '0 1px 3px rgba(0,0,0,0.08)'
                      : 'none',
                  borderBottomRightRadius: msg.role === 'user' ? 4 : 16,
                  borderBottomLeftRadius: msg.role === 'assistant' ? 4 : 16,
                }}
              >
                {msg.content}
              </div>
            </div>
          ))}

          {/* Sending indicator */}
          {sending && (
            <div
              style={{
                display: 'flex',
                gap: 10,
                alignItems: 'center',
                animation: 'fadeIn 0.3s ease-out',
              }}
            >
              <div
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: '50%',
                  background: cfg.themeColor,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#fff',
                }}
              >
                <Bot size={16} />
              </div>
              <div
                style={{
                  display: 'flex',
                  gap: 4,
                  padding: '10px 16px',
                }}
              >
                {[0, 1, 2].map((i) => (
                  <div
                    key={i}
                    style={{
                      width: 7,
                      height: 7,
                      borderRadius: '50%',
                      background: cfg.themeColor,
                      opacity: 0.4,
                      animation: `pulse 0.8s ease-in-out ${i * 0.15}s infinite`,
                    }}
                  />
                ))}
              </div>
            </div>
          )}

          <div ref={chatEndRef} />
        </div>

        {/* ── Input ──────────────────────────────────────────────── */}
        <div
          style={{
            padding: '12px 16px',
            background: '#fff',
            borderTop: '1px solid #e2e5ea',
            flexShrink: 0,
          }}
        >
          <div
            style={{
              display: 'flex',
              gap: 8,
              alignItems: 'flex-end',
              background: '#f3f4f6',
              borderRadius: 16,
              padding: '8px 12px',
              border: '1px solid transparent',
              transition: 'border-color 0.2s',
            }}
          >
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={cfg.placeholder}
              disabled={sending}
              rows={1}
              style={{
                flex: 1,
                border: 'none',
                outline: 'none',
                background: 'transparent',
                fontSize: 15,
                lineHeight: 1.5,
                resize: 'none',
                fontFamily: 'inherit',
                color: '#111827',
                maxHeight: 120,
              }}
              onInput={(e) => {
                const el = e.currentTarget;
                el.style.height = 'auto';
                el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
              }}
            />
            <button
              onClick={() => sendMessage(input)}
              disabled={!input.trim() || sending}
              title="发送 (Enter)"
              style={{
                width: 36,
                height: 36,
                borderRadius: '50%',
                border: 'none',
                background: input.trim() && !sending ? cfg.themeColor : '#d1d5db',
                color: '#fff',
                cursor: input.trim() && !sending ? 'pointer' : 'not-allowed',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
                transition: 'background 0.2s, transform 0.15s',
                transform: input.trim() && !sending ? 'scale(1)' : 'scale(0.95)',
              }}
            >
              <Send size={16} />
            </button>
          </div>

          {/* Embed watermark */}
          {embed && (
            <div
              style={{
                textAlign: 'center',
                marginTop: 8,
                fontSize: 11,
                color: '#d1d5db',
              }}
            >
              Powered by {cfg.poweredBy}
            </div>
          )}
        </div>
      </div>

      {/* ── Inline Styles (animations + responsive) ──────────────── */}
      <style jsx global>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(8px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes pulse {
          0%, 100% { opacity: 0.3; transform: scale(0.8); }
          50%      { opacity: 1;   transform: scale(1.2); }
        }

        html, body, #__next {
          margin: 0;
          padding: 0;
          height: 100%;
          background: #fafbfc;
        }

        .bot-app-container {
          box-shadow: 0 0 0 1px rgba(0,0,0,0.04), 0 4px 24px rgba(0,0,0,0.06);
        }

        @media (max-width: 768px) {
          .bot-app-container {
            box-shadow: none;
            border-radius: 0;
          }
        }

        /* Scrollbar styling */
        .bot-app-container ::-webkit-scrollbar {
          width: 4px;
        }
        .bot-app-container ::-webkit-scrollbar-track {
          background: transparent;
        }
        .bot-app-container ::-webkit-scrollbar-thumb {
          background: #d1d5db;
          border-radius: 2px;
        }
      `}</style>
    </>
  );
}
