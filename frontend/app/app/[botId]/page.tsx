'use client';

import { useState, useRef, useEffect, type JSX } from 'react';
import { useParams } from 'next/navigation';

// ── Types ────────────────────────────────────────────────────────

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  status?: 'sending' | 'sent' | 'error';
}

// ── Published Bot Chat Page ─────────────────────────────────────

export default function PublishedBotPage(): JSX.Element {
  const params = useParams();
  const botId = (params?.botId as string) || 'unknown';

  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content: `👋 你好！我是 **${botId}** Agent。有什么可以帮助你的？`,
      timestamp: new Date().toISOString(),
    },
  ]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [config, setConfig] = useState<{
    theme_color?: string;
    welcome_message?: string;
    suggestions?: string[];
    logo_url?: string;
    title?: string;
  } | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // Load bot config
  useEffect(() => {
    fetch(`/api/agent/registry/${botId}`)
      .then((r) => r.json())
      .then((data) => {
        setConfig({
          theme_color: data.theme_color || '#6366f1',
          welcome_message: data.welcome_message || `你好！我是 ${botId} Agent。有什么可以帮助你的？`,
          suggestions: data.suggestions || [],
          logo_url: data.logo_url || '',
          title: data.display_name || botId,
        });
        if (data.welcome_message || data.display_name) {
          setMessages([{
            id: 'welcome',
            role: 'assistant',
            content: data.welcome_message || `你好！我是 **${data.display_name || botId}** Agent。有什么可以帮助你的？`,
            timestamp: new Date().toISOString(),
          }]);
        }
      })
      .catch(() => { /* use defaults */ });
  }, [botId]);

  // Auto-scroll
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Send message
  const handleSend = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput('');
    setSending(true);

    const userMsg: ChatMessage = {
      id: 'user-' + Date.now(),
      role: 'user',
      content: text,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);

    const assistantMsg: ChatMessage = {
      id: 'assistant-' + Date.now(),
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      status: 'sending',
    };
    setMessages((prev) => [...prev, assistantMsg]);

    try {
      const res = await fetch('/api/chat/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          agent_id: botId,
          stream: true,
        }),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const reader = res.body?.getReader();
      if (reader) {
        const decoder = new TextDecoder();
        let fullContent = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const chunk = decoder.decode(value, { stream: true });
          // Parse SSE
          const lines = chunk.split('\n');
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6));
                if (data.content) {
                  fullContent += data.content;
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === assistantMsg.id ? { ...m, content: fullContent, status: 'sent' } : m
                    )
                  );
                }
              } catch { /* ignore parse errors */ }
            }
          }
        }
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsg.id ? { ...m, content: fullContent || '(empty response)', status: 'sent' } : m
          )
        );
      }
    } catch (err) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantMsg.id
            ? { ...m, content: `发送失败：${(err as Error).message}`, status: 'error' }
            : m
        )
      );
    }
    setSending(false);
  };

  const themeColor = config?.theme_color || '#6366f1';
  const lighterTheme = themeColor + '18';

  return (
    <div className="flex flex-col h-screen bg-warm-50">
      {/* ── Header ───────────────────────────────────────────────── */}
      <header
        className="shrink-0 px-6 py-4 flex items-center gap-3 border-b border-warm-150 bg-white/80 backdrop-blur-sm"
        style={{ borderTop: `3px solid ${themeColor}` }}
      >
        {config?.logo_url ? (
          <img src={config.logo_url} alt={config.title} className="w-8 h-8 rounded-full object-cover" />
        ) : (
          <div
            className="w-8 h-8 rounded-full flex items-center justify-center text-white text-sm font-bold"
            style={{ backgroundColor: themeColor }}
          >
            {(config?.title || botId)[0].toUpperCase()}
          </div>
        )}
        <div>
          <h1 className="text-sm font-semibold text-warm-900">{config?.title || botId}</h1>
          <div className="flex items-center gap-1.5 text-[10px] text-green-600">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
            在线
          </div>
        </div>
        <div className="flex-1" />
        <span className="text-[10px] text-warm-400">
          Powered by <span className="font-semibold text-warm-600">AgentHub</span>
        </span>
      </header>

      {/* ── Messages ─────────────────────────────────────────────── */}
      <main className="flex-1 overflow-y-auto px-4 py-6">
        <div className="max-w-3xl mx-auto space-y-4">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${
                  msg.role === 'user'
                    ? 'text-white'
                    : 'bg-white text-warm-800 border border-warm-150 shadow-sm'
                }`}
                style={msg.role === 'user' ? { backgroundColor: themeColor } : undefined}
              >
                {msg.content}
                {msg.status === 'sending' && (
                  <span className="inline-flex gap-1 ml-1">
                    <span className="w-1 h-1 rounded-full bg-current animate-bounce" style={{ animationDelay: '0ms' }} />
                    <span className="w-1 h-1 rounded-full bg-current animate-bounce" style={{ animationDelay: '150ms' }} />
                    <span className="w-1 h-1 rounded-full bg-current animate-bounce" style={{ animationDelay: '300ms' }} />
                  </span>
                )}
                {msg.status === 'error' && (
                  <span className="text-red-400 text-[10px] ml-1">⚠ 发送失败</span>
                )}
              </div>
            </div>
          ))}
          <div ref={chatEndRef} />
        </div>
      </main>

      {/* ── Suggestions ──────────────────────────────────────────── */}
      {(config?.suggestions?.length || 0) > 0 && messages.length <= 1 && (
        <div className="shrink-0 px-4 pb-2">
          <div className="max-w-3xl mx-auto flex flex-wrap gap-2">
            {config?.suggestions?.map((s: string, i: number) => (
              <button
                key={i}
                className="text-xs px-3 py-1.5 rounded-full border border-warm-200 bg-white text-warm-600 hover:border-primary-300 hover:text-primary-600 transition-colors"
                onClick={() => { setInput(s); }}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ── Composer ─────────────────────────────────────────────── */}
      <div className="shrink-0 border-t border-warm-150 bg-white px-4 py-3">
        <div className="max-w-3xl mx-auto flex items-end gap-2">
          <textarea
            className="flex-1 input-field text-sm resize-none"
            rows={1}
            placeholder={`向 ${config?.title || botId} 发送消息...`}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            style={{ minHeight: '44px', maxHeight: '120px' }}
          />
          <button
            className="btn-primary shrink-0 px-4 py-2.5 rounded-xl"
            onClick={handleSend}
            disabled={!input.trim() || sending}
            style={{ backgroundColor: themeColor, opacity: input.trim() && !sending ? 1 : 0.5 }}
          >
            <span className="material-symbols-outlined text-[18px]">send</span>
          </button>
        </div>
        <p className="text-[10px] text-warm-400 text-center mt-2">
          按 Enter 发送 · Shift+Enter 换行
        </p>
      </div>
    </div>
  );
}
