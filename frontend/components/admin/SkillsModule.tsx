import { useCallback, useEffect, useMemo, useState, type JSX } from 'react';
import dynamic from 'next/dynamic';
import type { SkillMeta, SkillDetail } from '../../types';

const MarkdownRenderer = dynamic(() => import('../chat/MarkdownRenderer'), {
  ssr: false,
  loading: () => <div className="skeleton skeleton-text !h-4 w-3/4" />,
});

interface SkillsModuleProps {
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
}

const SOURCE_LABELS: Record<string, string> = { user: '用户', project: '项目', plugin: '插件', mcp: 'MCP' };
const SOURCE_ICONS: Record<string, string> = { user: 'person', project: 'folder', plugin: 'extension', mcp: 'hub' };
const SOURCE_ORDER = ['user', 'project', 'plugin', 'mcp'];

export default function SkillsModule({ authHeaders, setNotice }: SkillsModuleProps): JSX.Element {
  // ── Skills module state ────────────────────────────────────────
  const [skillList, setSkillList] = useState<SkillMeta[]>([]);
  const [skillLoading, setSkillLoading] = useState(false);
  const [skillError, setSkillError] = useState('');
  const [skillKeyword, setSkillKeyword] = useState('');
  const [skillCategoryFilter, setSkillCategoryFilter] = useState<string>('');
  const [activeSkillName, setActiveSkillName] = useState<string | null>(null);
  const [activeSkillSource, setActiveSkillSource] = useState<string | null>(null);
  const [skillDetail, setSkillDetail] = useState<SkillDetail | null>(null);
  const [skillDetailLoading, setSkillDetailLoading] = useState(false);
  const [skillMetaExpanded, setSkillMetaExpanded] = useState(false);

  // ── Skills module helpers ──────────────────────────────────────────
  const loadSkills = useCallback(async (): Promise<void> => {
    setSkillLoading(true);
    setSkillError('');
    try {
      const res = await fetch('/api/v1/skills', { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json() as { skills: SkillMeta[] };
      setSkillList(data.skills || []);

      if (activeSkillName && activeSkillSource) {
        const stillExists = (data.skills || []).some(
          (s) => s.name === activeSkillName && s.source === activeSkillSource
        );
        if (stillExists) {
          await loadSkillDetail(activeSkillName, activeSkillSource);
        } else {
          setActiveSkillName(null);
          setActiveSkillSource(null);
          setSkillDetail(null);
        }
      }
    } catch (e: unknown) {
      setSkillError(e instanceof Error ? e.message : '加载技能列表失败');
    } finally {
      setSkillLoading(false);
    }
  }, [activeSkillName, activeSkillSource, authHeaders]);

  const loadSkillDetail = useCallback(async (name: string, source: string): Promise<void> => {
    setSkillDetailLoading(true);
    setSkillMetaExpanded(false);
    try {
      const url = `/api/v1/skills/${encodeURIComponent(name)}?source=${encodeURIComponent(source)}`;
      const res = await fetch(url, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const detail = (await res.json()) as SkillDetail;
      setSkillDetail(detail);
      setActiveSkillName(name);
      setActiveSkillSource(source);
    } catch (e: unknown) {
      setSkillError(e instanceof Error ? e.message : '读取技能详情失败');
    } finally {
      setSkillDetailLoading(false);
    }
  }, [authHeaders]);

  const handleExportSkill = useCallback(async (): Promise<void> => {
    if (!activeSkillName || !activeSkillSource) return;
    try {
      const url = `/api/v1/skills/${encodeURIComponent(activeSkillName)}/raw?source=${encodeURIComponent(activeSkillSource)}`;
      const res = await fetch(url, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${activeSkillName}_SKILL.md`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(a.href);
    } catch {
      setNotice('导出技能失败');
    }
  }, [activeSkillName, activeSkillSource, authHeaders, setNotice]);

  // ── Load skills on mount ────────────────────────────────────────
  useEffect(() => {
    void loadSkills();
  }, []);

  // ── Derived values ───────────────────────────────────────────────
  const filteredSkills = useMemo(() => {
    let result = skillList;
    const kw = skillKeyword.trim().toLowerCase();
    if (kw) {
      result = result.filter((s) => {
        return s.name.toLowerCase().includes(kw)
          || (s.display_name || '').toLowerCase().includes(kw)
          || (s.description || '').toLowerCase().includes(kw)
          || s.source.toLowerCase().includes(kw)
          || (s.version || '').toLowerCase().includes(kw)
          || (s.category || '').toLowerCase().includes(kw)
          || (s.tags || []).some((t) => t.toLowerCase().includes(kw));
      });
    }
    if (skillCategoryFilter) {
      result = result.filter((s) => s.category === skillCategoryFilter);
    }
    return result;
  }, [skillList, skillKeyword, skillCategoryFilter]);

  const skillCategories = useMemo(() => {
    const cats = new Set(skillList.map((s) => s.category || '其他'));
    return Array.from(cats).sort((a, b) => {
      if (a === '其他') return 1;
      if (b === '其他') return -1;
      return a.localeCompare(b);
    });
  }, [skillList]);

  const skillCategoryTree = useMemo(() => {
    const tree: Record<string, Set<string>> = {};
    for (const s of skillList) {
      const pri = s.category || '其他';
      const sub = s.subcategory || '未分类';
      (tree[pri] ??= new Set()).add(sub);
    }
    return tree;
  }, [skillList]);

  const groupedSkills = useMemo(() => {
    const result: Record<string, SkillMeta[]> = {};
    for (const skill of filteredSkills) {
      (result[skill.source] ??= []).push(skill);
    }
    return result;
  }, [filteredSkills]);

  const skillTokens = useMemo(
    () => filteredSkills.reduce((sum, s) => sum + Math.ceil(s.content_length / 4), 0),
    [filteredSkills],
  );

  // ── Render ────────────────────────────────────────────────────────

  // Loading state
  if (skillLoading && !skillList.length) {
    return (
      <section className="flex justify-center py-12">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-warm-400 border-t-transparent" />
      </section>
    );
  }

  // Error state
  if (skillError && !skillList.length) {
    return (
      <section className="card p-6">
        <h2 className="text-h3">技能</h2>
        <p className="mt-3 text-sm text-danger-500">{skillError}</p>
        <button className="btn-secondary mt-3" onClick={() => { void loadSkills(); }}>重试</button>
      </section>
    );
  }

  // Empty state
  if (!skillLoading && !skillList.length) {
    return (
      <section className="rounded-2xl border border-dashed border-warm-200 bg-warm-100 p-12 text-center">
        <span className="material-symbols-outlined text-[48px] text-warm-400 mb-3 block">auto_awesome</span>
        <p className="text-sm text-warm-500">暂无本地技能</p>
        <p className="text-xs text-warm-400 mt-1">
          将 SKILL.md 文件放入 ~/.claude/skills/ 或项目 .claude/skills/ 目录即可自动识别
        </p>
      </section>
    );
  }

  return (
    <section className="overflow-hidden rounded-2xl border border-warm-200 bg-warm-100 h-[calc(100vh-165px)]">
      <div className="grid h-full grid-cols-[360px_1fr] overflow-hidden">
        {/* ── Left sidebar: stats, search, skill list ─────────────── */}
        <aside className="border-r border-warm-150 bg-[#191C22] flex flex-col overflow-hidden">
          {/* Stats bar */}
          <div className="border-b border-warm-150 px-4 py-3">
            <div className="flex items-stretch gap-0">
              <div className="flex-1 text-center py-1">
                <div className="text-[22px] font-bold leading-none text-warm-800">{filteredSkills.length}</div>
                <div className="mt-0.5 text-[10px] uppercase tracking-wider text-warm-400">总数</div>
              </div>
              <div className="w-px bg-warm-150" />
              <div className="flex-1 text-center py-1">
                <div className="text-[22px] font-bold leading-none text-warm-800">{skillCategories.length}</div>
                <div className="mt-0.5 text-[10px] uppercase tracking-wider text-warm-400">分类</div>
              </div>
              <div className="w-px bg-warm-150" />
              <div className="flex-1 text-center py-1">
                <div className="text-[22px] font-bold leading-none text-warm-800">{skillTokens.toLocaleString()}</div>
                <div className="mt-0.5 text-[10px] uppercase tracking-wider text-warm-400">Tokens</div>
              </div>
            </div>
          </div>

          {/* Search + filters */}
          <div className="border-b border-warm-150 px-4 py-3 space-y-2">
            <div className="flex items-center gap-2 rounded-lg border border-warm-200 bg-warm-100 px-3 py-2 transition-colors focus-within:border-warm-400 focus-within:ring-2 focus-within:ring-warm-300/20">
              <span className="material-symbols-outlined text-[18px] text-warm-400 shrink-0">search</span>
              <input
                className="min-w-0 flex-1 bg-transparent text-sm text-warm-800 outline-none placeholder:text-warm-400"
                placeholder="搜索名称、描述、标签..."
                value={skillKeyword}
                onChange={(e) => setSkillKeyword(e.target.value)}
              />
              {skillKeyword && (
                <button
                  type="button"
                  className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-warm-400 hover:text-warm-600"
                  onClick={() => setSkillKeyword('')}
                >
                  <span className="material-symbols-outlined text-[14px]">close</span>
                </button>
              )}
            </div>
            <div className="flex items-center gap-2">
              {skillCategories.length > 1 && (
                <select
                  className="flex-1 min-h-9 rounded-lg border border-warm-200 bg-warm-100 px-3 text-sm text-warm-700 outline-none"
                  value={skillCategoryFilter}
                  onChange={(e) => setSkillCategoryFilter(e.target.value)}
                >
                  <option value="">全部分类</option>
                  {skillCategories.map((cat) => {
                    const subs = skillCategoryTree[cat] || new Set<string>();
                    const subList = Array.from(subs).sort();
                    return (
                      <option key={cat} value={cat}>
                        {cat}{subList.length > 0 ? ` (${subList.join(' · ')})` : ''}
                      </option>
                    );
                  })}
                </select>
              )}
              <button
                className="btn-ghost shrink-0 px-2 py-1.5 rounded-lg"
                onClick={() => { void loadSkills(); }}
                title="刷新技能列表"
              >
                <span className="material-symbols-outlined text-[18px]">refresh</span>
              </button>
            </div>
            {(skillKeyword || skillCategoryFilter) && (
              <div className="text-xs text-warm-400">
                显示 {filteredSkills.length} / {skillList.length} 个技能
                <button
                  className="ml-2 underline hover:text-warm-600"
                  onClick={() => { setSkillKeyword(''); setSkillCategoryFilter(''); }}
                >
                  清除筛选
                </button>
              </div>
            )}
          </div>

          {/* Skill list grouped by source */}
          <div className="flex-1 overflow-auto">
            {filteredSkills.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
                <span className="material-symbols-outlined text-[36px] text-warm-400 mb-2">search_off</span>
                <p className="text-sm text-warm-500">没有匹配的技能</p>
                <p className="text-xs text-warm-400 mt-1">尝试调整搜索关键词或分类筛选</p>
              </div>
            ) : (
              SOURCE_ORDER.map((source) => {
                const group = groupedSkills[source];
                if (!group?.length) return null;

                const sourceLabel = SOURCE_LABELS[source] || source;
                const sourceTokenCount = group.reduce((sum, s) => sum + Math.ceil(s.content_length / 4), 0);

                return (
                  <div key={source} className="border-b border-warm-100 last:border-b-0">
                    {/* Group header */}
                    <div className="flex items-center gap-2 px-4 py-2 bg-warm-50 sticky top-0 z-[1] border-b border-warm-100">
                      <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-warm-200 text-warm-600">
                        <span className="material-symbols-outlined text-[14px]">{SOURCE_ICONS[source] || 'inventory_2'}</span>
                      </span>
                      <span className="text-xs font-semibold text-warm-700">{sourceLabel}</span>
                      <span className="text-[11px] text-warm-400">{group.length} 个</span>
                      <span className="text-[10px] text-warm-400 ml-auto">~{sourceTokenCount.toLocaleString()}t</span>
                    </div>

                    {/* Skills in group */}
                    <div className="flex flex-col p-1">
                      {group.map((skill) => (
                        <button
                          key={`${skill.source}-${skill.name}`}
                          onClick={() => { void loadSkillDetail(skill.name, skill.source); }}
                          className={`group rounded-lg border px-3 py-2 text-left transition-colors ${
                            activeSkillName === skill.name && activeSkillSource === skill.source
                              ? 'border-warm-300 bg-warm-100'
                              : 'border-transparent hover:border-warm-200 hover:bg-warm-50'
                          } ${skill.enabled === false ? 'opacity-50' : ''}`}
                        >
                          <div className="flex items-start gap-2">
                            <span className="mt-px material-symbols-outlined text-[15px] text-warm-400 shrink-0">
                              auto_awesome
                            </span>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-1.5 flex-wrap">
                                <span className="text-[12px] font-semibold text-warm-800 truncate">
                                  {skill.display_name || skill.name}
                                </span>
                                {skill.version && (
                                  <span className="rounded-full bg-warm-100 px-1.5 py-px text-[10px] text-warm-500 shrink-0">
                                    v{skill.version}
                                  </span>
                                )}
                              </div>
                              <p className="mt-0.5 text-[11px] leading-4 text-warm-500 break-words line-clamp-1">
                                {skill.description || '（无描述）'}
                              </p>
                              <div className="mt-1 flex items-center gap-x-2 text-[10px] text-warm-400">
                                {skill.subcategory && (
                                  <>
                                    <span className="text-[10px] text-warm-500">{skill.subcategory}</span>
                                    <span className="text-warm-300">·</span>
                                  </>
                                )}
                                <span>~{Math.ceil(skill.content_length / 4).toLocaleString()}t</span>
                                <span className="text-warm-300">·</span>
                                <span>{skill.body_lines}行</span>
                              </div>
                            </div>
                            <span className="material-symbols-outlined text-[14px] text-warm-400 opacity-0 group-hover:opacity-60 transition-all group-hover:translate-x-0.5 shrink-0 mt-0.5">
                              chevron_right
                            </span>
                          </div>
                        </button>
                      ))}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </aside>

        {/* ── Right panel: skill detail ───────────────────────────── */}
        <div className="min-w-0 flex flex-col overflow-hidden">
          {!skillDetail ? (
            /* Empty state: no skill selected */
            <div className="flex-1 flex flex-col items-center justify-center text-center px-8">
              <span className="material-symbols-outlined text-[56px] text-warm-300 mb-4">auto_awesome</span>
              <h3 className="text-base font-semibold text-warm-700 mb-1">选择一个技能</h3>
              <p className="text-sm text-warm-500 max-w-xs leading-relaxed">
                从左侧列表中选择一个技能，查看其详细信息、元数据和原始 SKILL.md 内容。
              </p>
              {skillList.length > 0 && (
                <p className="text-xs text-warm-400 mt-3">
                  已扫描 {skillList.length} 个本地技能，来自 {skillList.filter((s, i, arr) => arr.findIndex(x => x.source === s.source) === i).length} 个来源
                </p>
              )}
            </div>
          ) : (
            <>
              {/* Detail header — compact single row */}
              <header className="flex items-center justify-between border-b border-warm-150 px-4 py-2 shrink-0 gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-[15px] font-semibold text-warm-900 truncate">
                      {typeof skillDetail.meta?.name === 'string' ? skillDetail.meta.name : (activeSkillName || '技能详情')}
                    </span>
                    {typeof skillDetail.meta?.version === 'string' && skillDetail.meta.version && (
                      <span className="rounded-full bg-warm-100 px-1.5 py-px text-[10px] font-medium text-warm-500 shrink-0">
                        v{skillDetail.meta.version}
                      </span>
                    )}
                    <span className="rounded bg-warm-100 px-1.5 py-px text-[10px] text-warm-500 shrink-0">
                      {SOURCE_LABELS[skillDetail.source] || skillDetail.source}
                    </span>
                  </div>
                  <div className="text-[11px] text-warm-400 mt-0.5 truncate max-w-lg">{skillDetail.path}</div>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <button className="btn-ghost px-2 py-1 text-xs rounded-lg" onClick={() => { void loadSkillDetail(activeSkillName!, activeSkillSource!); }}>
                    <span className="material-symbols-outlined text-[16px]">refresh</span>
                  </button>
                  <button className="btn-primary px-3 py-1 text-xs rounded-lg" onClick={handleExportSkill}>
                    导出
                  </button>
                </div>
              </header>

              {/* Compact metadata bar */}
              {skillDetail.meta && Object.keys(skillDetail.meta).length > 0 && (() => {
                const meta = skillDetail.meta;
                const description = typeof meta.description === 'string' ? meta.description : '';
                const authorsRaw = meta.authors;
                const authorsText = Array.isArray(authorsRaw)
                  ? authorsRaw.map(String).join(' · ')
                  : typeof authorsRaw === 'string' ? authorsRaw : '';
                const category = skillDetail.category || '';
                const subcategory = skillDetail.subcategory || '';
                const credentials = Array.isArray(meta.credentials) ? meta.credentials : [];
                const tags = Array.isArray(meta.tags) ? meta.tags : [];
                const hasExtra = credentials.length > 0 || tags.length > 0;

                return (
                  <div className="border-b border-warm-100 bg-warm-100">
                    <div className="flex items-center gap-3 px-4 py-1.5 flex-wrap">
                      {description && (
                        <span className="text-[12px] text-warm-600 truncate max-w-[360px]">{description}</span>
                      )}
                      {(category || subcategory) && (
                        <span className="inline-flex items-center gap-1 text-[11px] shrink-0">
                          <span className="text-warm-300">分类</span>
                          <span className="text-warm-600 font-medium">{category}</span>
                          {subcategory && (
                            <>
                              <span className="text-warm-300">/</span>
                              <span className="text-warm-500">{subcategory}</span>
                            </>
                          )}
                        </span>
                      )}
                      {activeSkillName && (
                        <code className="text-[11px] bg-warm-50 px-1.5 py-0.5 rounded text-warm-500 font-mono shrink-0">{activeSkillName}</code>
                      )}
                      {authorsText && (
                        <span className="text-[11px] text-warm-500 truncate max-w-[200px] shrink-0">
                          <span className="text-warm-300">作者</span> {authorsText}
                        </span>
                      )}
                      {hasExtra && (
                        <button
                          className="ml-auto shrink-0 text-[11px] text-warm-400 hover:text-warm-600 flex items-center gap-0.5"
                          onClick={() => setSkillMetaExpanded(!skillMetaExpanded)}
                        >
                          {skillMetaExpanded ? '收起' : '更多详情'}
                          <span className="material-symbols-outlined text-[14px] transition-transform" style={{ transform: skillMetaExpanded ? 'rotate(180deg)' : 'rotate(0deg)' }}>
                            expand_more
                          </span>
                        </button>
                      )}
                    </div>

                    {skillMetaExpanded && hasExtra && (
                      <div className="px-4 pb-2 space-y-1.5 border-t border-warm-100 pt-1.5">
                        {credentials.length > 0 && (
                          <div>
                            <div className="text-[10px] font-medium text-warm-400 mb-1">入参 / 凭据</div>
                            <div className="flex flex-wrap gap-1">
                              {credentials.map((cred, idx) => {
                                const credName = typeof cred === 'string' ? cred : (cred?.name || String(cred));
                                return (
                                  <span key={idx} className="inline-flex items-center gap-1 rounded bg-warm-50 border border-warm-200 px-1.5 py-0.5 text-[10px] text-warm-600">
                                    <span className="material-symbols-outlined text-[11px] text-warm-400">key</span>
                                    {credName}
                                  </span>
                                );
                              })}
                            </div>
                          </div>
                        )}
                        {tags.length > 0 && (
                          <div className="flex flex-wrap gap-1">
                            {tags.map((tag: unknown, idx: number) => (
                              <span key={idx} className="rounded-full bg-warm-100 px-1.5 py-0.5 text-[10px] text-warm-500">
                                #{String(tag)}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* Raw content */}
              <div className="flex-1 min-h-0 flex flex-col">
                <div className="flex items-center justify-between border-b border-warm-100 bg-warm-50 px-4 py-1.5 shrink-0">
                  <span className="text-[11px] font-medium text-warm-500">SKILL.md</span>
                  <span className="text-[10px] text-warm-400">
                    {typeof skillDetail.meta?.content_length === 'number'
                      ? `${(skillDetail.meta.content_length / 1024).toFixed(1)} KB`
                      : ''}
                  </span>
                </div>
                {skillDetailLoading ? (
                  <div className="flex justify-center py-12">
                    <div className="h-5 w-5 animate-spin rounded-full border-2 border-warm-400 border-t-transparent" />
                  </div>
                ) : (
                  <div className="flex-1 overflow-auto bg-[#121418] px-5 py-4">
                    {skillDetail.raw ? (
                      <MarkdownRenderer content={skillDetail.raw} />
                    ) : (
                      <span className="text-sm text-warm-400">（空内容）</span>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
