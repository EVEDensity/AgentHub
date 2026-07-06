import { memo, useEffect, useRef, useState } from 'react';
import { MessageSquareQuote } from 'lucide-react';
import type { Agent, AttachedFile, FileReference, QuoteReference, SkillMeta, WorkflowSummary } from '../../types';
import { PLATFORM_LABELS, PLATFORM_COLORS } from '../../types';
import PermissionModePopover, { type ExecPermission } from './PermissionModePopover';

const FILE_CATEGORY_CONFIG: Record<string, { label: string; extensions: string[]; mimePattern: RegExp }> = {
  code: { label: 'Code', extensions: ['py','js','ts','jsx','tsx','java','go','rs','c','cpp','h','hpp','swift','kt','rb','php','sql','sh','bash','vue','svelte','astro'], mimePattern: /^(text\/|\b(?:javascript|typescript|json)\b)/ },
  document: { label: 'Document', extensions: ['txt','md','pdf','docx','rtf','tex','rst','org','log'], mimePattern: /^(text\/|application\/pdf|application\/vnd\.openxmlformats)/ },
  image: { label: 'Image', extensions: ['png','jpg','jpeg','gif','svg','webp','bmp','ico'], mimePattern: /^image\// },
  archive: { label: 'Archive', extensions: ['zip','rar','7z','tar','gz','bz2','xz'], mimePattern: /^(application\/zip|application\/x-rar|application\/x-7z|application\/gzip|application\/x-tar)/ },
  spreadsheet: { label: 'Sheet', extensions: ['xlsx','xls','csv','tsv'], mimePattern: /^(application\/vnd\.(ms-excel|openxmlformats-officedocument\.spreadsheetml)|text\/csv)/ },
  presentation: { label: 'Slide', extensions: ['pptx','ppt'], mimePattern: /^application\/vnd\.(ms-powerpoint|openxmlformats-officedocument\.presentationml)/ },
  config: { label: 'Config', extensions: ['json','yaml','yml','xml','toml','ini','cfg','env','conf','cnf','editorconfig','gitignore','dockerfile','makefile','prisma','graphql','proto'], mimePattern: /^(application\/json|application\/xml|text\/(xml|yaml|toml))/ },
  unknown: { label: 'File', extensions: [], mimePattern: /^$/ },
};

const ALL_EXTENSIONS: Set<string> = new Set();
Object.values(FILE_CATEGORY_CONFIG).forEach((c) => c.extensions.forEach((e) => ALL_EXTENSIONS.add(e)));
const ACCEPT_STRING = Array.from(ALL_EXTENSIONS).map((e) => `.${e}`).join(',');

// Emoji set used by the popover. Kept small but expressive; grouped
// for quick scanning without overwhelming the floating panel.
const EMOJI_GROUPS: Array<{ label: string; emojis: string[] }> = [
  { label: '常用', emojis: ['😀','😁','😂','🤣','😊','😍','😘','😎','🤔','😴','😅','😭','🥺','😡','🤩','🥳','🤯','😇'] },
  { label: '手势', emojis: ['👍','👎','👏','🙏','👌','✌️','🤝','💪','✋','🫶','🤞','👋'] },
  { label: '符号', emojis: ['❤️','🔥','✨','🎉','🎊','💯','✅','❌','⭐','🌟','💡','📌','📎','📁','🚀','💎'] },
  { label: '工作', emojis: ['💻','⌨️','🖥️','📱','🔧','⚙️','🛠️','🧪','📊','📈','📉','📝','🔍','🔒','🔓','📡'] },
];

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

// Shorten a "clipboard-1716123456789-0.png" style name for chip
// display while preserving the full string in the hover tooltip.
function displayName(name: string): string {
  const clipboardMatch = name.match(/^clipboard-(\d+)-(\d+)(\.[\w]+)?$/i);
  if (clipboardMatch) {
    const ext = clipboardMatch[3] || '';
    return `剪贴板图片${ext}`;
  }
  return name;
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
    case 'presentation':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>;
    case 'config':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>;
    default:
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>;
  }
}

interface ChatInputProps {
  input: string;
  isStreaming: boolean;
  attachedFiles: AttachedFile[];
  mentionOpen: boolean;
  mentionTrigger: '@' | '#' | '/';
  mentionSearch: string;
  mentionActiveIndex: number;
  selectedRiskLevel: string;
  filteredAgents: Agent[];
  filteredWorkflows: WorkflowSummary[];
  filteredSkills: SkillMeta[];
  textareaRef: React.RefObject<HTMLTextAreaElement>;
  mentionPanelRef: React.RefObject<HTMLDivElement>;
  fileInputRef: React.RefObject<HTMLInputElement>;
  onInputChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
  onBlur: () => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  onSend: () => void;
  onFileChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onRemoveFile: (index: number) => void;
  /**
   * 点击附件卡片上的预览眼睛按钮 → 打开 FilePreviewModal。
   * 未传则不显示预览按钮。已经在上传中的文件不会触发预览。
   */
  onPreviewFile?: (file: AttachedFile) => void;
  onClearAllFiles: () => void;
  onPasteFiles: (files: File[]) => void;
  fileReferences?: FileReference[];
  onRemoveReference?: (index: number) => void;
  onClearAllReferences?: () => void;
  /**
   * 点击引用芯片 → 在右侧预览面板中定位到该文件并滚动到对应行号。
   * 不传则芯片仅展示用，不可点击。
   */
  onJumpToReference?: (ref: FileReference) => void;
  quoteReferences?: QuoteReference[];
  onRemoveQuoteReference?: (index: number) => void;
  onClearAllQuoteReferences?: () => void;
  onInsertMention: (agentId: string) => void;
  onInsertAllMentions: () => void;
  onInsertWorkflow: (wf: WorkflowSummary) => void;
  onInsertSkill: (skill: SkillMeta) => void;
  onMentionSearchChange: (q: string) => void;
  onMentionActiveIndexChange: (idx: number) => void;
  onRiskLevelChange: (level: string) => void;
  // ── 执行权限 ──
  execPermission: ExecPermission;
  onExecPermissionChange: (mode: ExecPermission) => void;
  // ── 自动回复：无@Agent 时是否用默认Agent回复 ──
  autoReply: boolean;
  onAutoReplyChange: (mode: boolean) => void;
  // ── 观察者模式 ──
  userRole?: string;
  memberCount?: number;
}

const ChatInput = memo(function ChatInput({
  input, isStreaming, attachedFiles,
  mentionOpen, mentionTrigger, mentionSearch, mentionActiveIndex, selectedRiskLevel,
  filteredAgents, filteredWorkflows, filteredSkills,
  textareaRef, mentionPanelRef, fileInputRef,
  onInputChange, onBlur, onKeyDown, onSend, onFileChange, onRemoveFile, onClearAllFiles, onPasteFiles, onPreviewFile,
  onInsertMention, onInsertAllMentions, onInsertWorkflow, onInsertSkill,
  onMentionSearchChange, onMentionActiveIndexChange, onRiskLevelChange,
  fileReferences, onRemoveReference, onClearAllReferences, onJumpToReference,
  quoteReferences, onRemoveQuoteReference, onClearAllQuoteReferences,
  execPermission, onExecPermissionChange,
  autoReply, onAutoReplyChange,
  userRole, memberCount,
}: ChatInputProps) {
  // ── Observer mode detection ──────────────────────────────────────
  // Observers in multi-user sessions (≥2 members) have restricted input:
  // plain text only — no @mentions, #workflows, or /skills.
  const isObserverInMultiUser = userRole === 'viewer' && (memberCount ?? 0) > 1;

  // Emoji popover state. The panel is positioned via fixed offsets so
  // it always lands above the toolbar, regardless of scroll position.
  const [emojiOpen, setEmojiOpen] = useState(false);
  const emojiButtonRef = useRef<HTMLButtonElement | null>(null);
  const emojiPanelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!emojiOpen) return;
    const onDocClick = (e: MouseEvent) => {
      const t = e.target as Node | null;
      if (!t) return;
      if (emojiPanelRef.current?.contains(t)) return;
      if (emojiButtonRef.current?.contains(t)) return;
      setEmojiOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setEmojiOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onEsc);
    };
  }, [emojiOpen]);

  // Auto-scroll to keep the active mention item visible during keyboard navigation
  useEffect(() => {
    if (!mentionOpen) return;
    const container = mentionPanelRef.current;
    if (!container) return;
    const el = container.querySelector(`[data-mention-index="${mentionActiveIndex}"]`);
    if (el) {
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [mentionActiveIndex, mentionOpen]);

  function handleEmojiSelect(emoji: string) {
    const ta = textareaRef.current;
    if (!ta) {
      onInputChange({
        target: { value: input + emoji, selectionStart: input.length + emoji.length },
      } as unknown as React.ChangeEvent<HTMLTextAreaElement>);
      return;
    }
    const start = ta.selectionStart ?? input.length;
    const end = ta.selectionEnd ?? input.length;
    const next = input.slice(0, start) + emoji + input.slice(end);
    onInputChange({
      target: {
        value: next,
        selectionStart: start + emoji.length,
        selectionEnd: start + emoji.length,
      },
    } as unknown as React.ChangeEvent<HTMLTextAreaElement>);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (el) {
        el.focus();
        const pos = start + emoji.length;
        el.selectionStart = pos;
        el.selectionEnd = pos;
      }
    });
  }

  function handlePaste(e: React.ClipboardEvent<HTMLTextAreaElement>) {
    const items = e.clipboardData?.items;
    if (!items || items.length === 0) return;

    const imageFiles: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.type.startsWith('image/')) {
        const blob = item.getAsFile();
        if (blob) {
          const ext = item.type.split('/')[1] || 'png';
          const name = `clipboard-${Date.now()}-${i}.${ext}`;
          imageFiles.push(new File([blob], name, { type: item.type }));
        }
      }
    }

    if (imageFiles.length > 0) {
      e.preventDefault();
      onPasteFiles(imageFiles);
    }
  }

  const canSend = input.trim().length > 0 || attachedFiles.length > 0 || (fileReferences && fileReferences.length > 0) || (quoteReferences && quoteReferences.length > 0);

  return (
    <footer className="shrink-0 relative border-t border-warm-200 px-6 py-4">
      {/* ── Observer-mode indicator ─────────────────────────────── */}
      {isObserverInMultiUser && (
        <div className="mb-3 flex items-center gap-2 border border-warning-100 bg-warning-50 px-3 py-2 text-sm text-warning-600">
          <svg className="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
            <circle cx="12" cy="12" r="3"/>
            <line x1="1" y1="12" x2="23" y2="12"/>
          </svg>
          <span className="font-medium">观察者模式</span>
          <span className="text-warning-600">— 多人对话中仅允许发送纯文本消息</span>
        </div>
      )}

      {mentionOpen && mentionTrigger === '@' && (
        <div ref={mentionPanelRef} className="absolute bottom-24 left-6 z-20 w-[520px] border border-warm-200 bg-warm-100 p-3 shadow-card">
          <div className="mb-2 flex items-center justify-between text-caption text-warm-500">
            <span>@ Select Agent</span>
            <button className="text-primary-500" onClick={onInsertAllMentions}>@All Agents</button>
          </div>
          <div className="mb-2">
            <input
              type="text"
              placeholder="搜索agent..."
              value={mentionSearch}
              onChange={(e) => { onMentionSearchChange(e.target.value); onMentionActiveIndexChange(0); }}
              onKeyDown={onKeyDown as any}
              className="w-full rounded-lg border border-warm-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
          </div>
          <div className="mb-2 flex gap-1">
            {['all', 'L1', 'L2', 'L3'].map((level) => (
              <button
                key={level}
                onClick={() => { onRiskLevelChange(level); onMentionActiveIndexChange(0); }}
                className={`rounded-md px-2 py-1 text-xs ${
                  selectedRiskLevel === level
                    ? 'bg-primary-500 text-white'
                    : 'bg-warm-100 text-warm-600 hover:bg-warm-200'
                }`}
              >
                {level === 'all' ? '全部' : level}
              </button>
            ))}
          </div>
          <div className="max-h-60 overflow-y-auto">
            <div className="grid grid-cols-2 gap-2">
              {filteredAgents.length === 0 ? (
                <div className="col-span-2 py-4 text-center text-sm text-warm-400">No matching agents</div>
              ) : (
                filteredAgents.map((agent, idx) => (
                  <button
                    key={agent.agentId}
                    data-mention-index={idx}
                    className={`rounded-lg px-3 py-2 text-left border ${
                      idx === mentionActiveIndex
                        ? 'bg-primary-50 border-primary-300 ring-1 ring-primary-300'
                        : 'bg-warm-50 border-transparent hover:bg-primary-50'
                    }`}
                    onClick={() => onInsertMention(agent.agentId)}
                    onMouseEnter={() => onMentionActiveIndexChange(idx)}
                  >
                    <div className="flex items-center gap-2">
                      {/* Avatar or fallback initial */}
                      {agent.avatarUrl ? (
                        <img src={agent.avatarUrl} className="h-7 w-7 rounded-full object-cover shrink-0" alt={agent.displayName || agent.agentId} loading="lazy" decoding="async" />
                      ) : (
                        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-warm-200 text-warm-600 text-xs font-bold">
                          {(agent.displayName || agent.agentId)[0]}
                        </span>
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="font-medium text-warm-700 text-sm">@{agent.agentId}</span>
                          {agent.displayName && (
                            <span className="text-xs text-warm-500">{agent.displayName}</span>
                          )}
                          {agent.agentId === 'Architect' && (
                            <span
                              className="inline-flex items-center gap-0.5 bg-primary-500 px-1.5 py-0.5 text-[9px] font-semibold text-warm-50"
                              title="主 Agent（PM / PMO）：负责任务拆解、调度、降级、仲裁与人工交接"
                            >
                              <svg
                                className="h-2.5 w-2.5"
                                viewBox="0 0 24 24"
                                fill="currentColor"
                                aria-hidden="true"
                              >
                                <path d="M5 16h14l1.5-9-4.5 3-4-6-4 6L3.5 7 5 16Zm0 2v2h14v-2H5Z" />
                              </svg>
                              主 Agent
                            </span>
                          )}
                          <span
                            className="rounded px-1.5 py-0.5 text-[9px] font-medium"
                            style={{
                              backgroundColor: (PLATFORM_COLORS[agent.adapterType] || '#6b7280') + '18',
                              color: PLATFORM_COLORS[agent.adapterType] || '#6b7280',
                            }}
                          >
                            {PLATFORM_LABELS[agent.adapterType] || agent.adapterType}
                          </span>
                        </div>
                        {agent.capabilityTags && agent.capabilityTags.length > 0 && (
                          <div className="mt-1 flex flex-wrap gap-1">
                            {agent.capabilityTags.slice(0, 3).map((tag) => (
                              <span key={tag} className="rounded bg-warm-100 px-1.5 py-0.5 text-[10px] text-warm-500">{tag}</span>
                            ))}
                            {agent.capabilityTags.length > 3 && (
                              <span className="text-[10px] text-warm-400">+{agent.capabilityTags.length - 3}</span>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  </button>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      {mentionOpen && mentionTrigger === '#' && (
        <div ref={mentionPanelRef} className="absolute bottom-24 left-6 z-20 w-[520px] border border-warm-200 bg-warm-100 p-3 shadow-card">
          <div className="mb-2 flex items-center justify-between text-caption text-warm-500">
            <span># Select Workflow</span>
            <span className="text-warm-400">{filteredWorkflows.length} workflows</span>
          </div>
          <div className="mb-2">
            <input
              type="text"
              placeholder="搜索工作流..."
              value={mentionSearch}
              onChange={(e) => { onMentionSearchChange(e.target.value); onMentionActiveIndexChange(0); }}
              onKeyDown={onKeyDown as any}
              className="w-full rounded-lg border border-warm-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
          </div>
          <div className="max-h-60 overflow-y-auto">
            <div className="space-y-1">
              {filteredWorkflows.length === 0 ? (
                <div className="py-4 text-center text-sm text-warm-400">No matching workflows</div>
              ) : (
                filteredWorkflows.map((wf, idx) => (
                  <button
                    key={wf.routeId}
                    data-mention-index={idx}
                    className={`w-full rounded-lg px-3 py-2 text-left border ${
                      idx === mentionActiveIndex
                        ? 'bg-primary-50 border-primary-300 ring-1 ring-primary-300'
                        : 'bg-warm-50 border-transparent hover:bg-primary-50'
                    }`}
                    onClick={() => onInsertWorkflow(wf)}
                    onMouseEnter={() => onMentionActiveIndexChange(idx)}
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-warm-800">#{wf.name}</span>
                      <span className="text-xs text-warm-400">{wf.description.slice(0, 40)}{wf.description.length > 40 ? '...' : ''}</span>
                    </div>
                    {wf.triggerKeywords.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {wf.triggerKeywords.map((k) => (
                          <span key={k} className="rounded bg-warm-100 px-1.5 py-0.5 text-xs text-warm-500">{k}</span>
                        ))}
                      </div>
                    )}
                  </button>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      {mentionOpen && mentionTrigger === '/' && (
        <div ref={mentionPanelRef} className="absolute bottom-24 left-6 z-20 w-[560px] border border-warm-200 bg-warm-100 p-3 shadow-card">
          <div className="mb-2 flex items-center justify-between text-caption text-warm-500">
            <span>/ Select Skill</span>
            <span className="text-warm-400">{filteredSkills.length} skills</span>
          </div>
          <div className="mb-2">
            <input
              type="text"
              placeholder="搜索技能..."
              value={mentionSearch}
              onChange={(e) => { onMentionSearchChange(e.target.value); onMentionActiveIndexChange(0); }}
              onKeyDown={onKeyDown as any}
              className="w-full rounded-lg border border-warm-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
          </div>
          <div className="mb-2 flex gap-1 flex-wrap">
            {(() => {
              // Collect unique categories from filtered skills
              const cats = [...new Set(filteredSkills.map((s) => s.category || '其他'))];
              return cats.map((cat) => (
                <span key={cat} className="rounded bg-warm-100 px-1.5 py-0.5 text-xs text-warm-500">{cat}</span>
              ));
            })()}
          </div>
          <div className="max-h-60 overflow-y-auto">
            <div className="space-y-1">
              {filteredSkills.length === 0 ? (
                <div className="py-4 text-center text-sm text-warm-400">No matching skills</div>
              ) : (
                filteredSkills.map((skill, idx) => (
                  <button
                    key={skill.name}
                    data-mention-index={idx}
                    className={`w-full rounded-lg px-3 py-2 text-left border ${
                      idx === mentionActiveIndex
                        ? 'bg-primary-50 border-primary-300 ring-1 ring-primary-300'
                        : 'bg-warm-50 border-transparent hover:bg-primary-50'
                    }`}
                    onClick={() => onInsertSkill(skill)}
                    onMouseEnter={() => onMentionActiveIndexChange(idx)}
                  >
                    <div className="flex items-center gap-2">
                      <span className="shrink-0 text-lg">{skill.icon || '◇'}</span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-warm-800">/{skill.name}</span>
                          <span className="text-xs text-warm-400">{skill.category}{skill.subcategory ? ` / ${skill.subcategory}` : ''}</span>
                        </div>
                        <div className="text-xs text-warm-500 truncate mt-0.5">{skill.description.slice(0, 80)}{skill.description.length > 80 ? '...' : ''}</div>
                      </div>
                      {skill.version && <span className="shrink-0 text-xs text-warm-400">{skill.version}</span>}
                    </div>
                  </button>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      {emojiOpen && (
        <div
          ref={emojiPanelRef}
          className="absolute bottom-16 left-6 z-30 w-[320px] border border-warm-200 bg-warm-100 p-3 shadow-card"
        >
          <div className="mb-2 flex items-center justify-between text-caption text-warm-500">
            <span>选择表情</span>
            <button
              className="text-warm-400 hover:text-warm-600"
              onClick={() => setEmojiOpen(false)}
              title="关闭"
            >
              <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
          <div className="max-h-64 overflow-y-auto pr-1">
            {EMOJI_GROUPS.map((group) => (
              <div key={group.label} className="mb-2">
                <div className="mb-1 text-[11px] text-warm-400">{group.label}</div>
                <div className="grid grid-cols-8 gap-1">
                  {group.emojis.map((emoji) => (
                    <button
                      key={emoji}
                      type="button"
                      onClick={() => handleEmojiSelect(emoji)}
                      className="flex h-8 w-8 items-center justify-center rounded-md text-lg transition-colors hover:bg-primary-50"
                      title={emoji}
                    >
                      {emoji}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/*
        Layout (top → bottom in the left column):
          1) 附件标签层  – horizontal-scroll chips, hidden scrollbar, "clear all" trailing button
          2) 工具栏层    – emoji / paperclip / skill icons on the left, Send on the right
          3) 输入框层    – multi-line textarea
      */}
      <div className="grid gap-3 items-end overflow-visible" style={{ gridTemplateColumns: '1fr auto' }}>
        <div className="flex flex-col gap-2 min-w-0">
          {/* ── Layer 1: attachment chips ─────────────────────── */}
          {attachedFiles.length > 0 && (
            <div className="flex items-center gap-2">
              <div
                className="chat-attach-scroll flex-1 min-w-0 overflow-x-auto overflow-y-hidden whitespace-nowrap"
                style={{ scrollbarWidth: 'none' }}
              >
                <div className="inline-flex gap-1.5 py-0.5 pr-2">
                  {attachedFiles.map((f, i) => {
                    const isImage = f.category === 'image' && f.content;
                    const isUploading = f.uploadStatus === 'uploading';
                    const isErrored = f.uploadStatus === 'error';
                    // 设计图样式: 暗色卡片, 蓝色文件图标, 右侧悬浮眼睛按钮 + 关闭按钮
                    const chipClass = isErrored
                      ? 'bg-danger-50 border-danger-200 text-danger-700'
                      : isUploading
                        ? 'bg-primary-50 border-primary-200 text-warm-700'
                        : 'bg-warm-900 border-warm-800 text-warm-50 hover:bg-warm-800';
                    return (
                      <span
                        key={`${f.name}-${i}`}
                        className={`group relative inline-flex items-center gap-1.5 rounded-lg pl-2 pr-1 py-1 text-xs border shrink-0 transition-colors ${chipClass}`}
                      >
                        {isImage ? (
                          <img src={f.content} alt={f.name} className="h-7 w-7 rounded object-cover shrink-0" loading="lazy" decoding="async" />
                        ) : (
                          <span
                            className={`shrink-0 flex items-center justify-center h-5 w-5 rounded ${isUploading || isErrored ? 'bg-white/40' : 'bg-primary-500/20'}`}
                          >
                            <FileIcon category={f.category} size={3.5} />
                          </span>
                        )}
                        <span className={`max-w-[120px] truncate ${isUploading || isErrored ? '' : 'text-warm-50'}`}>
                          {displayName(f.name)}
                        </span>
                        <span className={`shrink-0 ${isUploading || isErrored ? 'text-warm-500' : 'text-warm-300'}`}>
                          {formatSize(f.size)}
                        </span>
                        {isUploading && (
                          <span className="flex items-center gap-1 text-primary-600">
                            <span className="h-2 w-12 overflow-hidden rounded-full bg-primary-100">
                              <span className="block h-full rounded-full bg-primary-500 transition-all" style={{ width: `${f.uploadProgress || 0}%` }} />
                            </span>
                            <span className="text-[10px]">{f.uploadProgress || 0}%</span>
                          </span>
                        )}
                        {isErrored && (
                          <span className="text-[10px] text-danger-500" title={f.uploadError}>失败</span>
                        )}

                        {/* 预览按钮 (眼睛) - 设计图核心交互: 上传成功后可点 */}
                        {!isUploading && onPreviewFile && f.uploadStatus === 'done' && (
                          <button
                            type="button"
                            className={`ml-0.5 shrink-0 inline-flex h-5 w-5 items-center justify-center rounded transition-colors ${
                              isErrored
                                ? 'text-warm-400 hover:text-danger-600 hover:bg-white/30'
                                : 'text-warm-200 hover:text-white hover:bg-white/15'
                            }`}
                            onClick={() => onPreviewFile(f)}
                            title="预览文件"
                            aria-label={`预览 ${f.name}`}
                          >
                            <svg
                              className="h-3.5 w-3.5"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            >
                              <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
                              <circle cx="12" cy="12" r="3" />
                            </svg>
                          </button>
                        )}

                        {!isUploading && (
                          <button
                            type="button"
                            className={`ml-0.5 shrink-0 inline-flex h-5 w-5 items-center justify-center rounded transition-colors ${
                              isErrored
                                ? 'text-warm-400 hover:text-danger-600 hover:bg-white/30'
                                : 'text-warm-200 hover:text-white hover:bg-white/15'
                            }`}
                            onClick={() => onRemoveFile(i)}
                            title="移除"
                            aria-label={`移除 ${f.name}`}
                          >
                            <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                              <line x1="18" y1="6" x2="6" y2="18" />
                              <line x1="6" y1="6" x2="18" y2="18" />
                            </svg>
                          </button>
                        )}

                        {/* Hover bubble – full filename + size */}
                        <span
                          role="tooltip"
                          className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-warm-200 bg-warm-900 px-2 py-1 text-[11px] text-white opacity-0 shadow-modal transition-opacity duration-150 group-hover:opacity-100"
                        >
                          <span className="block max-w-[260px] truncate">{f.name}</span>
                          <span className="block text-[10px] text-warm-300">{formatSize(f.size)} · {f.category}</span>
                        </span>
                      </span>
                    );
                  })}
                </div>
              </div>
              <button
                type="button"
                onClick={onClearAllFiles}
                className="shrink-0 inline-flex items-center gap-1 rounded-md border border-warm-200 bg-warm-50 px-2 py-1 text-[11px] text-warm-600 transition-colors hover:border-danger-200 hover:bg-danger-50 hover:text-danger-600"
                title="清空全部附件"
              >
                <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="3 6 5 6 21 6"/>
                  <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                  <path d="M10 11v6M14 11v6"/>
                  <path d="M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/>
                </svg>
                清空
              </button>
            </div>
          )}

          {/* ── Layer 1.2: quote reference chips (quoted chat messages) ── */}
          {quoteReferences && quoteReferences.length > 0 && (
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-thin max-w-full" style={{ scrollbarWidth: 'thin', scrollbarColor: '#93c5fd transparent' }}>
                {quoteReferences.map((qr, i) => (
                  <span
                    key={qr.id}
                    className="group shrink-0 inline-flex items-center gap-1 rounded-full pl-2 pr-1 py-1 text-[11px] font-medium bg-blue-50 border border-blue-200 text-blue-700 shadow-sm transition hover:border-blue-300"
                    title={`引用自: ${qr.originalSender}\n${qr.originalTimestamp}\n\n${qr.quotedText}`}
                  >
                    <MessageSquareQuote className="h-3 w-3 shrink-0 text-blue-500" />
                    <span className="max-w-[100px] truncate">{qr.originalSender}</span>
                    <span className="max-w-[160px] truncate text-blue-400">
                      {qr.quotedText.length > 30 ? `${qr.quotedText.slice(0, 30)}…` : qr.quotedText}
                    </span>
                    {qr.isFullMessage && (
                      <span className="rounded bg-blue-100 px-1 text-[9px] text-blue-500 shrink-0">全文</span>
                    )}
                    {onRemoveQuoteReference && (
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); onRemoveQuoteReference(i); }}
                        className="ml-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full text-blue-400 hover:bg-blue-200 hover:text-blue-700 transition-colors"
                        title="移除此引用"
                        aria-label="移除此引用"
                      >
                        <svg className="h-2.5 w-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round">
                          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                        </svg>
                      </button>
                    )}
                  </span>
                ))}
              </div>
              {onClearAllQuoteReferences && (
                <button
                  type="button"
                  onClick={onClearAllQuoteReferences}
                  className="shrink-0 inline-flex items-center gap-1 rounded-md border border-blue-200 bg-blue-50 px-2 py-1 text-[11px] text-blue-600 transition-colors hover:border-blue-300 hover:bg-blue-100"
                  title="清空全部引用"
                >
                  <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="3 6 5 6 21 6"/>
                    <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                    <path d="M10 11v6M14 11v6"/>
                  </svg>
                  清空引用
                </button>
              )}
            </div>
          )}

          {/* ── Layer 1.5: file reference chips (quoted text from preview) ── */}
          {fileReferences && fileReferences.length > 0 && (
            <div className="flex items-center gap-2">
              <div
                className="chat-attach-scroll flex-1 min-w-0 overflow-x-auto overflow-y-hidden whitespace-nowrap"
                style={{ scrollbarWidth: 'none' }}
              >
                <div className="inline-flex gap-1.5 py-0.5 pr-2">
                  {fileReferences.map((ref, i) => {
                    const lineInfo = ref.lineStart
                      ? ref.lineEnd && ref.lineEnd !== ref.lineStart
                        ? `L${ref.lineStart}-L${ref.lineEnd}`
                        : `L${ref.lineStart}`
                      : '';
                    const truncated = (ref.quote?.length ?? 0) > 50;
                    return (
                      <span
                        key={ref.id}
                        className="group relative inline-flex items-center gap-1.5 rounded-lg pl-2.5 pr-1 py-1 text-xs border shrink-0 bg-purple-50 border-purple-200 text-purple-700"
                      >
                        {/* 整块可点击 → 跳转回源文件 */}
                        {onJumpToReference ? (
                          <button
                            type="button"
                            onClick={() => onJumpToReference(ref)}
                            className="flex items-center gap-1.5 outline-none focus-visible:ring-2 focus-visible:ring-purple-400 rounded-md"
                            title="在预览面板中跳转到该文件"
                          >
                            <svg className="h-3.5 w-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
                            </svg>
                            <span className="max-w-[100px] truncate font-medium">{ref.name}</span>
                            {lineInfo && (
                              <span className="text-purple-400 shrink-0 text-[10px]">{lineInfo}</span>
                            )}
                            {ref.quote && (
                              <span className="max-w-[140px] truncate text-purple-400 italic">
                                "{ref.quote.slice(0, 50)}{truncated ? '…' : ''}"
                              </span>
                            )}
                            <svg className="h-3 w-3 shrink-0 text-purple-300 group-hover:text-purple-500 transition-colors" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M7 17l10-10"/>
                              <path d="M7 7h10v10"/>
                            </svg>
                          </button>
                        ) : (
                          <span className="flex items-center gap-1.5">
                            <svg className="h-3.5 w-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
                            </svg>
                            <span className="max-w-[100px] truncate font-medium">{ref.name}</span>
                            {lineInfo && (
                              <span className="text-purple-400 shrink-0 text-[10px]">{lineInfo}</span>
                            )}
                            {ref.quote && (
                              <span className="max-w-[140px] truncate text-purple-400 italic">
                                "{ref.quote.slice(0, 50)}{truncated ? '…' : ''}"
                              </span>
                            )}
                          </span>
                        )}
                        {onRemoveReference && (
                          <button
                            type="button"
                            className="ml-0.5 shrink-0 text-purple-400 hover:text-danger-500"
                            onClick={(e) => {
                              e.stopPropagation();
                              onRemoveReference(i);
                            }}
                            title="移除引用"
                          >
                            <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                          </button>
                        )}

                        {/* Hover tooltip — 展示完整路径 + 完整 quote */}
                        <span
                          role="tooltip"
                          className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1.5 -translate-x-1/2 max-w-[360px] whitespace-pre-wrap break-words rounded-md border border-purple-200 bg-purple-900 px-2.5 py-1.5 text-[11px] text-white opacity-0 shadow-modal transition-opacity duration-150 group-hover:opacity-100"
                        >
                          <span className="block text-[10px] text-purple-300 mb-0.5">{ref.path}</span>
                          {lineInfo && <span className="block text-[10px] text-purple-300 mb-1">{lineInfo}</span>}
                          {ref.quote && (
                            <span className="block text-[11px] text-purple-100 italic">"{ref.quote}"</span>
                          )}
                          {onJumpToReference && (
                            <span className="block text-[10px] text-purple-300 mt-1">点击跳转回源文件</span>
                          )}
                        </span>
                      </span>
                    );
                  })}
                </div>
              </div>
              {onClearAllReferences && (
                <button
                  type="button"
                  onClick={onClearAllReferences}
                  className="shrink-0 inline-flex items-center gap-1 rounded-md border border-purple-200 bg-purple-50 px-2 py-1 text-[11px] text-purple-600 transition-colors hover:border-danger-200 hover:bg-danger-50 hover:text-danger-600"
                  title="清空全部引用"
                >
                  <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="3 6 5 6 21 6"/>
                    <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                    <path d="M10 11v6M14 11v6"/>
                    <path d="M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/>
                  </svg>
                  清空引用
                </button>
              )}
            </div>
          )}

          {/* ── Layer 2: toolbar (emoji / paperclip / skill) ──── */}
          <div className="flex items-center gap-1.5">
            <button
              ref={emojiButtonRef}
              type="button"
              onClick={() => setEmojiOpen((v) => !v)}
              className={`inline-flex h-8 w-8 items-center justify-center rounded-lg border border-transparent text-warm-500 transition-colors hover:border-primary-200 hover:bg-primary-50 hover:text-primary-600 ${
                emojiOpen ? 'border-primary-200 bg-primary-50 text-primary-600' : ''
              }`}
              title="插入表情"
              aria-haspopup="dialog"
              aria-expanded={emojiOpen}
            >
              <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"/>
                <path d="M8 14s1.5 2 4 2 4-2 4-2"/>
                <line x1="9" y1="9" x2="9.01" y2="9"/>
                <line x1="15" y1="9" x2="15.01" y2="9"/>
              </svg>
            </button>

            <label
              className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-lg border border-transparent text-warm-500 transition-colors hover:border-primary-200 hover:bg-primary-50 hover:text-primary-600"
              title="Attach file"
            >
              <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
              <input ref={fileInputRef} type="file" multiple className="sr-only" onChange={onFileChange} accept={ACCEPT_STRING} />
            </label>

            <button
              type="button"
              className={`inline-flex h-8 w-8 items-center justify-center rounded-lg border border-transparent transition-colors ${
                isObserverInMultiUser
                  ? 'text-warm-300 cursor-not-allowed'
                  : 'text-warm-500 hover:border-primary-200 hover:bg-primary-50 hover:text-primary-600'
              }`}
              title={isObserverInMultiUser ? '观察者模式下不可使用技能' : 'Insert skill / tool'}
              onClick={() => {
                if (isObserverInMultiUser) return;
                onInsertSkill && filteredSkills[0] && onInsertSkill(filteredSkills[0]);
              }}
            >
              <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
              </svg>
            </button>

            {/* 视觉分隔 — 在工具按钮组和权限模式之间留 1px breathing room */}
            <span className="mx-0.5 h-5 w-px bg-warm-150" aria-hidden />

            {/* 权限模式 popover（替代原独立 PermissionToggle 行） */}
            <PermissionModePopover
              value={execPermission}
              onChange={onExecPermissionChange}
            />

            {/* 自动回复 toggle — 无@Agent时是否自动用默认Agent回复 */}
            <button
              type="button"
              onClick={() => onAutoReplyChange(!autoReply)}
              className={`relative inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium border transition-colors ${
                autoReply
                  ? 'bg-primary-500 text-warm-50 border-primary-500'
                  : 'border-warm-300 text-warm-400 hover:border-primary-400 hover:text-primary-500'
              }`}
              title={autoReply ? '自动回复已启用：无@Agent时默认Agent回复' : '仅发送模式：无@Agent时只发送消息不回复。@Agent始终有效'}
            >
              <span className="text-sm leading-none">{autoReply ? 'A' : 'M'}</span>
              <span>{autoReply ? '自动' : '仅发送'}</span>
            </button>
          </div>

          {/* ── Layer 3: textarea ─────────────────────────────── */}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={onInputChange}
            onBlur={onBlur}
            onKeyDown={onKeyDown}
            onPaste={handlePaste}
            rows={3}
            className="input-field w-full resize-none"
            placeholder={
              isStreaming ? 'AI is streaming, new message will interrupt current output...'
                : isObserverInMultiUser ? '观察者模式 — 仅可发送纯文本消息'
                : '输入消息，支持@Agent唤起智能体指令'
            }
          />
        </div>

        {/* Send button (right column) */}
        <div className="flex flex-col gap-2 pb-0.5" style={{ flexShrink: 0 }}>
          <button
            className={`btn-primary whitespace-nowrap ${canSend ? '' : 'cursor-not-allowed opacity-50 hover:bg-primary-500 hover:shadow-none'}`}
            onClick={onSend}
            disabled={!canSend}
            title={canSend ? '发送（Enter / Ctrl+Enter）' : '请输入内容或添加附件'}
          >
            Send
          </button>
          <span className="text-center text-[10px] text-warm-400">Ctrl+Enter</span>
        </div>
      </div>
    </footer>
  );
});

export default ChatInput;
