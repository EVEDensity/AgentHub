// ────────────────────────────────────────────────────────────────────
// FileReference 工具函数：截断、命中查询、规范化
// ────────────────────────────────────────────────────────────────────
import type { FileReference } from '../types';

/** 单条引用的最大字符数（避免用户选 1 万字把上下文打爆） */
export const REFERENCE_MAX_QUOTE_CHARS = 1200;

/** 单次发送时允许的最大引用条数 */
export const REFERENCE_MAX_COUNT = 20;

/**
 * 把单条引用规范化：截断过长的 quote、补全缺省的 lineEnd
 */
export function normalizeReference(ref: FileReference): FileReference {
  const quote = ref.quote;
  let trimmed = quote;
  let truncated = false;
  if (typeof quote === 'string' && quote.length > REFERENCE_MAX_QUOTE_CHARS) {
    trimmed = quote.slice(0, REFERENCE_MAX_QUOTE_CHARS) + '\n… [已截断]';
    truncated = true;
  }
  return {
    ...ref,
    quote: trimmed,
    lineEnd: ref.lineEnd ?? ref.lineStart,
    kind: ref.kind ?? 'chat-selection',
    // 把截断标记放一个隐藏字段供 UI 渲染时使用
    ...(truncated ? { name: ref.name } : {}),
  };
}

/**
 * 把多引用按文件聚合 + 截断 + 限条数。
 * 同 path 后续引用追加到前一条 quote 里。
 */
export function normalizeReferences(refs: FileReference[]): FileReference[] {
  const capped = refs.slice(0, REFERENCE_MAX_COUNT);
  return capped.map(normalizeReference);
}

/**
 * 判断某条引用是否命中指定文件路径
 */
export function isReferenceForPath(ref: FileReference, path: string): boolean {
  return ref.path === path;
}

/**
 * 取出属于指定 path 的所有引用（按 lineStart 升序）
 */
export function referencesForPath(
  refs: FileReference[] | undefined,
  path: string,
): FileReference[] {
  if (!refs) return [];
  return refs
    .filter((r) => r.path === path)
    .sort((a, b) => (a.lineStart ?? 0) - (b.lineStart ?? 0));
}

/**
 * 判断给定行号是否被任何引用区间覆盖（用于代码 / diff 高亮）
 */
export function lineHasReference(
  refs: FileReference[] | undefined,
  lineNumber: number,
): FileReference | undefined {
  if (!refs) return undefined;
  return refs.find((r) => {
    if (!r.lineStart) return false;
    const s = r.lineStart;
    const e = r.lineEnd ?? r.lineStart;
    return lineNumber >= s && lineNumber <= e;
  });
}
