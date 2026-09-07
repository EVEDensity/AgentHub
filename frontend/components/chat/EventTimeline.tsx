'use client';

import { memo, type JSX } from 'react';
import { GitCommit, MessageSquare, Sparkles, Swords, Crown, CheckCircle, XCircle, HelpCircle, Activity, Wifi, WifiOff } from 'lucide-react';
import type { SessionEvent, SessionEventType } from '../../types';
import type { SessionEventStreamState } from '../../hooks/useSessionEvents';

interface EventTimelineProps {
  events: SessionEvent[];
  state: SessionEventStreamState;
  lastConnectedAt: string | null;
}

const EVENT_META: Record<
  SessionEventType,
  { icon: JSX.Element; label: string; color: string }
> = {
  'message.created': {
    icon: <MessageSquare className="h-3.5 w-3.5" />,
    label: '消息',
    color: 'bg-sky-100 text-sky-700 border-sky-200',
  },
  'mention.detected': {
    icon: <Sparkles className="h-3.5 w-3.5" />,
    label: '@mention',
    color: 'bg-amber-100 text-amber-700 border-amber-200',
  },
  'rule.triggered': {
    icon: <Swords className="h-3.5 w-3.5" />,
    label: '规则触发',
    color: 'bg-fuchsia-100 text-fuchsia-700 border-fuchsia-200',
  },
  'mission.created': {
    icon: <GitCommit className="h-3.5 w-3.5" />,
    label: '任务创建',
    color: 'bg-indigo-100 text-indigo-700 border-indigo-200',
  },
  'mission.completed': {
    icon: <Crown className="h-3.5 w-3.5" />,
    label: '任务完成',
    color: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  },
  'decision.recorded': {
    icon: <HelpCircle className="h-3.5 w-3.5" />,
    label: '决策',
    color: 'bg-violet-100 text-violet-700 border-violet-200',
  },
  'member.joined': {
    icon: <CheckCircle className="h-3.5 w-3.5" />,
    label: '成员加入',
    color: 'bg-teal-100 text-teal-700 border-teal-200',
  },
  'member.left': {
    icon: <XCircle className="h-3.5 w-3.5" />,
    label: '成员离开',
    color: 'bg-rose-100 text-rose-700 border-rose-200',
  },
};

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return iso;
  }
}

function summarizeEvent(evt: SessionEvent): string {
  const p = evt.payload;
  switch (evt.eventType) {
    case 'message.created':
      return (p.content as string) || '(消息)';
    case 'mention.detected': {
      const names = (p.names as string[]) || [];
      return names.length ? `@${names.join(', @')}` : '(mention)';
    }
    case 'rule.triggered':
      return `${p.rule_id || p.rule_description || '(规则)'}`;
    case 'mission.created':
      return `${p.mission_id || '(?)'} → ${truncate((p.objective as string) || '', 36)}`;
    case 'mission.completed':
      return `${p.mission_id || '(?)'} · ${p.terminal_status || p.status || '?'}`;
    case 'decision.recorded':
      return `${p.rule_id || '(规则)'} → ${p.resolution || '?'}`;
    case 'member.joined':
    case 'member.left':
      return `${p.user_id || p.member_id || '(?)'}`;
    default:
      return JSON.stringify(p).slice(0, 60);
  }
}

function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

function connectionBadge(state: SessionEventStreamState, lastConnectedAt: string | null): JSX.Element | null {
  if (state === 'streaming') {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-emerald-600">
        <Wifi className="h-3 w-3" /> 实时
      </span>
    );
  }
  if (state === 'reconnecting') {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-amber-600 animate-pulse">
        <WifiOff className="h-3 w-3" /> 重连中
      </span>
    );
  }
  if (state === 'error') {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-red-600">
        <WifiOff className="h-3 w-3" /> 错误
      </span>
    );
  }
  if (state === 'closed' || state === 'idle') {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-slate-400">
        <Activity className="h-3 w-3" /> 离线
        {lastConnectedAt && <span className="ml-1">· {formatTime(lastConnectedAt)}</span>}
      </span>
    );
  }
  return null;
}

const EventTimeline = memo(function EventTimeline({ events, state, lastConnectedAt }: EventTimelineProps): JSX.Element {
  return (
    <div className="flex h-full flex-col bg-white rounded-xl border border-warm-200 shadow-sm">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-warm-100">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-indigo-500" />
          <span className="text-sm font-semibold text-warm-700">会话事件</span>
          <span className="rounded bg-warm-100 px-1.5 py-0.5 text-xs text-warm-500">{events.length}</span>
        </div>
        {connectionBadge(state, lastConnectedAt)}
      </div>

      {/* Timeline body */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1.5">
        {events.length === 0 ? (
          <div className="text-sm text-warm-400 text-center py-8">
            {state === 'connecting' || state === 'reconnecting'
              ? '正在连接事件流...'
              : '暂无事件 — 发送消息后这里会实时显示会话事件'}
          </div>
        ) : (
          events.map((evt) => {
            const meta = EVENT_META[evt.eventType] ?? {
              icon: <HelpCircle className="h-3.5 w-3.5" />,
              label: evt.eventType,
              color: 'bg-slate-100 text-slate-600 border-slate-200',
            };
            return (
              <div
                key={evt.id}
                className="flex items-start gap-2 rounded-lg px-2 py-1.5 hover:bg-warm-50 transition-colors"
              >
                {/* Icon dot */}
                <div className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded border ${meta.color}`}>
                  {meta.icon}
                </div>
                {/* Content */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded border ${meta.color}`}>
                      {meta.label}
                    </span>
                    <span className="text-[10px] text-warm-400">
                      {evt.actor.displayName || evt.actor.id}
                    </span>
                  </div>
                  <div className="text-xs text-warm-600 mt-0.5 truncate">
                    {summarizeEvent(evt)}
                  </div>
                </div>
                {/* Timestamp */}
                <div className="text-[10px] text-warm-400 shrink-0">
                  {formatTime(evt.createdAt)}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
});

export default EventTimeline;
