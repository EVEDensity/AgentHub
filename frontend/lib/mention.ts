// ────────────────────────────────────────────────────────────────────
// @mention / #workflow / /skill 触发与过滤的纯函数
// （从 app/page.tsx 抽出，保持行为一致）
// ────────────────────────────────────────────────────────────────────
import type { Agent, ChatSession, SkillMeta, WorkflowSummary } from '../types';

export type MentionTrigger = '@' | '#' | '/';

export interface MentionHit {
  trigger: MentionTrigger;
  pos: number;
  search: string;
}

/**
 * 在光标前的文本里查找最近的触发符（@ / # /）。
 * 规则：
 *  - 触发符必须位于行首，或前面是空格/换行
 *  - 触发符之后到光标之间不能有空格或换行
 *  - '/' 跳过 URL 协议段（如 "https://"）中的斜杠
 * 返回 null 表示当前没有激活的 mention。
 */
export function detectMentionTrigger(value: string, cursor: number): MentionHit | null {
  const textBefore = value.slice(0, cursor);
  const lastAt = textBefore.lastIndexOf('@');
  const lastHash = textBefore.lastIndexOf('#');
  const lastSlash = textBefore.lastIndexOf('/');

  const candidates: Array<{ pos: number; trigger: MentionTrigger }> = [];
  if (lastAt >= 0) candidates.push({ pos: lastAt, trigger: '@' });
  if (lastHash >= 0) candidates.push({ pos: lastHash, trigger: '#' });
  if (lastSlash >= 0) candidates.push({ pos: lastSlash, trigger: '/' });
  candidates.sort((a, b) => b.pos - a.pos);

  for (const c of candidates) {
    const charBefore = c.pos === 0 ? ' ' : value[c.pos - 1];
    const textAfter = textBefore.slice(c.pos + 1);
    // For / trigger, also skip if preceded by a protocol scheme (e.g. "https://")
    if (c.trigger === '/' && textBefore.slice(Math.max(0, c.pos - 7), c.pos).match(/(?:https?|ftp|file):$/)) {
      continue;
    }
    if (!textAfter.includes(' ') && !textAfter.includes('\n') &&
        (c.pos === 0 || charBefore === ' ' || charBefore === '\n')) {
      return { trigger: c.trigger, pos: c.pos, search: textAfter };
    }
  }
  return null;
}

/**
 * 多人会话中的观察者（viewer）只能发纯文本——
 * 不允许 @mention / #workflow / /skill。
 */
export function isObserverRestrictedSession(session: ChatSession | undefined): boolean {
  const myRole = session?.myRole || 'viewer';
  const memberCount = session?.memberCount ?? 0;
  return myRole === 'viewer' && memberCount > 1;
}

export function filterAgentsForMention(
  agents: Agent[],
  search: string,
  riskLevel: string,
): Agent[] {
  return agents.filter((agent) => {
    const matchesSearch = search === '' ||
      agent.agentId.toLowerCase().includes(search.toLowerCase()) ||
      agent.domain.toLowerCase().includes(search.toLowerCase());
    const matchesLevel = riskLevel === 'all' || agent.rankLevel === riskLevel;
    return matchesSearch && matchesLevel;
  });
}

export function filterWorkflowsForMention(
  workflows: WorkflowSummary[],
  search: string,
): WorkflowSummary[] {
  if (search === '') return workflows;
  const q = search.toLowerCase();
  return workflows.filter((w) => (
    w.name.toLowerCase().includes(q) ||
    w.description.toLowerCase().includes(q) ||
    w.triggerKeywords.some((k) => k.toLowerCase().includes(q))
  ));
}

export function filterSkillsForMention(
  skills: SkillMeta[],
  search: string,
): SkillMeta[] {
  if (search === '') return skills;
  const q = search.toLowerCase();
  return skills.filter((s) => (
    s.name.toLowerCase().includes(q) ||
    (s.display_name || '').toLowerCase().includes(q) ||
    (s.description || '').toLowerCase().includes(q)
  ));
}
