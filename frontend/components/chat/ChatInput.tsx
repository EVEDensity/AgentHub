import { memo, useCallback, useEffect, useRef, useState } from 'react';
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

const EMOJI_GROUPS: Array<{ label: string; emojis: string[] }> = [
  { label: '常用', emojis: ['😀','😁','😂','🤣','😊','😍','😘','😎','🤔','😴','😅','😭','🥺','😡','🤩','🥳','🤯','😇'] },
  { label: '手势', emojis: ['👍','👎','👏','🙏','👌','✌️','🤝','💪','✋','🫶','🤞','👋'] },
  { label: '符号', emojis: ['❤️','🔥','✨','🎉','🎊','💯','✅','❌','⭐','🌟','💡','📌','📎','📁','🚀','💎'] },
  { label: '工作', emojis: ['💻','⌨️','🖥️','📱','🔧','⚙️','🛠️','🧪','📊','📈','📉','📝','🔍','🔒','🔓','📡'] },
];

/* ── Quick action toolbar item definition ── */
interface QuickAction {
  id: string;
  label: string;
  icon: string;       // SVG path or material icon name
  shortcut?: string;
}

const QUICK_ACTIONS: QuickAction[] = [
  { id: 'attach', label: '附件', icon: 'attach', shortcut: 'Ctrl+U' },
  { id: 'agent', label: 'Agent', icon: 'agent' },
  { id: 'memory', label: '记忆', icon: 'memory' },
  { id: 'code', label: '代码', icon: 'code' },
  { id: 'draw', label: '绘图', icon: 'draw' },
  { id: 'data', label: '数据', icon: 'data' },
];

/* ── Helpers ── */

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

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
    case 'code': return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>;
    case 'document': return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>;
    case 'image': return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>;
    case 'archive': return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 8v13H3V8"/><path d="M1 3h22v5H1z"/><path d="M10 12h4"/></svg>;
    case 'spreadsheet': return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/></svg>;
    case 'presentation': return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>;
    case 'config': return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>;
    default: return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>;
  }
}

/* ── Quick action icon renderer ── */
function QuickActionIcon({ iconId }: { iconId: string }) {
  const cls = "qa-icon-svg";
  switch (iconId) {
    case 'attach':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>;
    case 'agent':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>;
    case 'memory':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2a10 10 0 1 0 10 10 4 4 0 0 1-5-5 4 4 0 0 1-5-5"/><path d="M8.5 8.5v.01"/><path d="M16 15.5v.01"/><path d="M12 12v.01"/><path d="M11 17v.01"/><path d="M7 14v.01"/></svg>;
    case 'code':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>;
    case 'draw':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9"/><path d="M16.376 3.622a1 1 0 0 1 3.002 3.002L7.368 18.635a2 2 0 0 1-.855.506l-2.872.838a.5.5 0 0 1-.62-.62l.838-2.872a2 2 0 0 1 .506-.854z"/></svg>;
    case 'data':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>;
    default:
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>;
  }
}

/* ───────────────────────────────────────────
   Props (same interface, fully compatible)
   ─────────────────────────────────────────── */

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
  onPreviewFile?: (file: AttachedFile) => void;
  onClearAllFiles: () => void;
  onPasteFiles: (files: File[]) => void;
  fileReferences?: FileReference[];
  onRemoveReference?: (index: number) => void;
  onClearAllReferences?: () => void;
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
  execPermission: ExecPermission;
  onExecPermissionChange: (mode: ExecPermission) => void;
  autoReply: boolean;
  onAutoReplyChange: (mode: boolean) => void;
  userRole?: string;
  memberCount?: number;
}

/* ───────────────────────────────────────────
   Component
   ─────────────────────────────────────────── */

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

  const isObserverInMultiUser = userRole === 'viewer' && (memberCount ?? 0) > 1;

  /* ── Emoji popover ── */
  const [emojiOpen, setEmojiOpen] = useState(false);
  const emojiButtonRef = useRef<HTMLButtonElement | null>(null);
  const emojiPanelRef = useRef<HTMLDivElement | null>(null);

  /* ── Show more quick actions ── */
  const [showMoreActions, setShowMoreActions] = useState(false);

  /* ── Textarea auto-height ── */
  const [textareaHeight, setTextareaHeight] = useState<number | null>(null);

  useEffect(() => {
    if (!emojiOpen) return;
    const onDocClick = (e: MouseEvent) => {
      const t = e.target as Node | null;
      if (!t) return;
      if (emojiPanelRef.current?.contains(t)) return;
      if (emojiButtonRef.current?.contains(t)) return;
      setEmojiOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') setEmojiOpen(false); };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onEsc);
    };
  }, [emojiOpen]);

  useEffect(() => {
    if (!mentionOpen) return;
    const container = mentionPanelRef.current;
    if (!container) return;
    const el = container.querySelector(`[data-mention-index="${mentionActiveIndex}"]`);
    if (el) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [mentionActiveIndex, mentionOpen]);

  /* ── Auto-expand textarea ── */
  const autoExpand = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    const newHeight = Math.min(ta.scrollHeight, 320);
    ta.style.height = `${newHeight}px`;
    setTextareaHeight(newHeight);
  }, [textareaRef]);

  useEffect(() => {
    autoExpand();
  }, [input, autoExpand]);

  /* ── Emoji insert ── */
  function handleEmojiSelect(emoji: string) {
    const ta = textareaRef.current;
    if (!ta) {
      onInputChange({ target: { value: input + emoji, selectionStart: input.length + emoji.length } } as unknown as React.ChangeEvent<HTMLTextAreaElement>);
      return;
    }
    const start = ta.selectionStart ?? input.length;
    const end = ta.selectionEnd ?? input.length;
    const next = input.slice(0, start) + emoji + input.slice(end);
    onInputChange({ target: { value: next, selectionStart: start + emoji.length, selectionEnd: start + emoji.length } } as unknown as React.ChangeEvent<HTMLTextAreaElement>);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (el) { el.focus(); const pos = start + emoji.length; el.selectionStart = pos; el.selectionEnd = pos; }
    });
  }

  /* ── Paste handler ── */
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
          imageFiles.push(new File([blob], `clipboard-${Date.now()}-${i}.${ext}`, { type: item.type }));
        }
      }
    }
    if (imageFiles.length > 0) { e.preventDefault(); onPasteFiles(imageFiles); }
  }

  const canSend = input.trim().length > 0 || attachedFiles.length > 0 || (fileReferences && fileReferences.length > 0) || (quoteReferences && quoteReferences.length > 0);

  /* ───────────────────────────────────────────
     Render
     ─────────────────────────────────────────── */

  return (
    <div className="chat-composer-wrapper">
      <div className="chat-composer">
        {/* ═══════════════════════════════════════
            Popovers (absolute positioned above)
            ═══════════════════════════════════════ */}

        {/* @Agent mention popover */}
        {mentionOpen && mentionTrigger === '@' && (
          <div ref={mentionPanelRef} className="composer-popover">
            <div className="composer-popover-header">
              <span>@ 选择智能体</span>
              <button className="composer-popover-all-btn" onClick={onInsertAllMentions}>@全部Agent</button>
            </div>
            <div className="composer-popover-search">
              <input type="text" placeholder="搜索agent..." value={mentionSearch}
                onChange={(e) => { onMentionSearchChange(e.target.value); onMentionActiveIndexChange(0); }}
                onKeyDown={onKeyDown as any} />
            </div>
            <div className="composer-popover-chips">
              {['all', 'L1', 'L2', 'L3'].map((level) => (
                <button key={level} onClick={() => { onRiskLevelChange(level); onMentionActiveIndexChange(0); }}
                  className={`composer-chip ${selectedRiskLevel === level ? 'active' : ''}`}>
                  {level === 'all' ? '全部' : level}
                </button>
              ))}
            </div>
            <div className="composer-popover-list">
              <div className="composer-popover-grid">
                {filteredAgents.length === 0 ? (
                  <div className="composer-popover-empty">No matching agents</div>
                ) : (
                  filteredAgents.map((agent, idx) => (
                    <button key={agent.agentId} data-mention-index={idx}
                      className={`composer-popover-item ${idx === mentionActiveIndex ? 'active' : ''}`}
                      onClick={() => onInsertMention(agent.agentId)}
                      onMouseEnter={() => onMentionActiveIndexChange(idx)}>
                      <div className="composer-popover-item-inner">
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
                            {agent.displayName && <span className="text-xs text-warm-500">{agent.displayName}</span>}
                            {agent.agentId === 'Architect' && (
                              <span className="composer-badge-primary">
                                <svg className="h-2.5 w-2.5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M5 16h14l1.5-9-4.5 3-4-6-4 6L3.5 7 5 16Zm0 2v2h14v-2H5Z" /></svg>
                                主 Agent
                              </span>
                            )}
                            <span className="rounded px-1.5 py-0.5 text-[9px] font-medium"
                              style={{ backgroundColor: (PLATFORM_COLORS[agent.adapterType] || '#6b7280') + '18', color: PLATFORM_COLORS[agent.adapterType] || '#6b7280' }}>
                              {PLATFORM_LABELS[agent.adapterType] || agent.adapterType}
                            </span>
                          </div>
                          {agent.capabilityTags && agent.capabilityTags.length > 0 && (
                            <div className="mt-1 flex flex-wrap gap-1">
                              {agent.capabilityTags.slice(0, 3).map((tag) => (
                                <span key={tag} className="rounded bg-warm-100 px-1.5 py-0.5 text-[10px] text-warm-500">{tag}</span>
                              ))}
                              {agent.capabilityTags.length > 3 && <span className="text-[10px] text-warm-400">+{agent.capabilityTags.length - 3}</span>}
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

        {/* #Workflow mention popover */}
        {mentionOpen && mentionTrigger === '#' && (
          <div ref={mentionPanelRef} className="composer-popover">
            <div className="composer-popover-header">
              <span># 选择工作流</span>
              <span className="text-warm-400 text-xs">{filteredWorkflows.length} workflows</span>
            </div>
            <div className="composer-popover-search">
              <input type="text" placeholder="搜索工作流..." value={mentionSearch}
                onChange={(e) => { onMentionSearchChange(e.target.value); onMentionActiveIndexChange(0); }}
                onKeyDown={onKeyDown as any} />
            </div>
            <div className="composer-popover-list">
              {filteredWorkflows.length === 0 ? (
                <div className="composer-popover-empty">No matching workflows</div>
              ) : (
                filteredWorkflows.map((wf, idx) => (
                  <button key={wf.routeId} data-mention-index={idx}
                    className={`composer-popover-item ${idx === mentionActiveIndex ? 'active' : ''}`}
                    onClick={() => onInsertWorkflow(wf)}
                    onMouseEnter={() => onMentionActiveIndexChange(idx)}>
                    <span className="font-medium text-warm-800">#{wf.name}</span>
                    <span className="text-xs text-warm-400 ml-2">{wf.description.slice(0, 40)}{wf.description.length > 40 ? '...' : ''}</span>
                  </button>
                ))
              )}
            </div>
          </div>
        )}

        {/* /Skill mention popover */}
        {mentionOpen && mentionTrigger === '/' && (
          <div ref={mentionPanelRef} className="composer-popover">
            <div className="composer-popover-header">
              <span>/ 选择技能</span>
              <span className="text-warm-400 text-xs">{filteredSkills.length} skills</span>
            </div>
            <div className="composer-popover-search">
              <input type="text" placeholder="搜索技能..." value={mentionSearch}
                onChange={(e) => { onMentionSearchChange(e.target.value); onMentionActiveIndexChange(0); }}
                onKeyDown={onKeyDown as any} />
            </div>
            <div className="composer-popover-list">
              {filteredSkills.length === 0 ? (
                <div className="composer-popover-empty">No matching skills</div>
              ) : (
                filteredSkills.map((skill, idx) => (
                  <button key={skill.name} data-mention-index={idx}
                    className={`composer-popover-item ${idx === mentionActiveIndex ? 'active' : ''}`}
                    onClick={() => onInsertSkill(skill)}
                    onMouseEnter={() => onMentionActiveIndexChange(idx)}>
                    <span className="shrink-0 text-lg">{skill.icon || '◇'}</span>
                    <div className="flex-1 min-w-0 ml-2">
                      <span className="font-medium text-warm-800">/{skill.name}</span>
                      <span className="text-xs text-warm-400 ml-1">{skill.category}</span>
                      <div className="text-xs text-warm-500 truncate mt-0.5">{skill.description.slice(0, 80)}</div>
                    </div>
                  </button>
                ))
              )}
            </div>
          </div>
        )}

        {/* Emoji popover */}
        {emojiOpen && (
          <div ref={emojiPanelRef} className="composer-emoji-popover">
            <div className="composer-emoji-popover-header">
              <span>选择表情</span>
              <button onClick={() => setEmojiOpen(false)} title="关闭">
                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>
            <div className="composer-emoji-popover-body">
              {EMOJI_GROUPS.map((group) => (
                <div key={group.label} className="composer-emoji-group">
                  <div className="composer-emoji-group-label">{group.label}</div>
                  <div className="composer-emoji-grid">
                    {group.emojis.map((emoji) => (
                      <button key={emoji} type="button" onClick={() => handleEmojiSelect(emoji)}
                        className="composer-emoji-btn" title={emoji}>{emoji}</button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ═══════════════════════════════════════
            Observer mode indicator
            ═══════════════════════════════════════ */}
        {isObserverInMultiUser && (
          <div className="composer-observer-bar">
            <svg className="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/><line x1="1" y1="12" x2="23" y2="12"/>
            </svg>
            <span className="font-medium">观察者模式</span>
            <span>— 多人对话中仅允许发送纯文本消息</span>
          </div>
        )}

        {/* ═══════════════════════════════════════
            Attachment / Reference chips
            ═══════════════════════════════════════ */}

        {/* File attachments */}
        {attachedFiles.length > 0 && (
          <div className="composer-chip-row">
            <div className="composer-chip-scroll">
              <div className="composer-chip-inner">
                {attachedFiles.map((f, i) => {
                  const isImage = f.category === 'image' && f.content;
                  const isUploading = f.uploadStatus === 'uploading';
                  const isErrored = f.uploadStatus === 'error';
                  const chipClass = isErrored ? 'composer-file-chip error'
                    : isUploading ? 'composer-file-chip uploading'
                    : 'composer-file-chip';
                  return (
                    <span key={`${f.name}-${i}`} className={`${chipClass} group`}>
                      {isImage ? (
                        <img src={f.content} alt={f.name} className="composer-file-chip-img" loading="lazy" decoding="async" />
                      ) : (
                        <span className="composer-file-chip-icon"><FileIcon category={f.category} size={3.5} /></span>
                      )}
                      <span className="composer-file-chip-name">{displayName(f.name)}</span>
                      <span className="composer-file-chip-size">{formatSize(f.size)}</span>
                      {isUploading && (
                        <span className="composer-file-chip-progress">
                          <span className="composer-file-chip-progress-bar">
                            <span className="composer-file-chip-progress-fill" style={{ width: `${f.uploadProgress || 0}%` }} />
                          </span>
                          <span className="text-[10px]">{f.uploadProgress || 0}%</span>
                        </span>
                      )}
                      {isErrored && <span className="text-[10px] text-danger-500" title={f.uploadError}>失败</span>}
                      {!isUploading && onPreviewFile && f.uploadStatus === 'done' && (
                        <button type="button" className="composer-file-chip-preview-btn" onClick={() => onPreviewFile(f)} title="预览文件" aria-label={`预览 ${f.name}`}>
                          <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>
                        </button>
                      )}
                      {!isUploading && (
                        <button type="button" className="composer-file-chip-remove-btn" onClick={() => onRemoveFile(i)} title="移除" aria-label={`移除 ${f.name}`}>
                          <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                        </button>
                      )}
                    </span>
                  );
                })}
              </div>
            </div>
            <button type="button" onClick={onClearAllFiles} className="composer-chip-clear-btn">清空</button>
          </div>
        )}

        {/* Quote references */}
        {quoteReferences && quoteReferences.length > 0 && (
          <div className="composer-chip-row">
            <div className="composer-chip-scroll">
              {quoteReferences.map((qr, i) => (
                <span key={qr.id} className="composer-quote-chip group" title={`引用自: ${qr.originalSender}\n${qr.originalTimestamp}\n\n${qr.quotedText}`}>
                  <MessageSquareQuote className="h-3 w-3 shrink-0 text-blue-500" />
                  <span className="max-w-[100px] truncate">{qr.originalSender}</span>
                  <span className="max-w-[160px] truncate text-blue-400">{qr.quotedText.length > 30 ? `${qr.quotedText.slice(0, 30)}…` : qr.quotedText}</span>
                  {qr.isFullMessage && <span className="composer-quote-full-badge">全文</span>}
                  {onRemoveQuoteReference && (
                    <button type="button" onClick={(e) => { e.stopPropagation(); onRemoveQuoteReference(i); }} className="composer-quote-remove-btn" title="移除此引用">
                      <svg className="h-2.5 w-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                  )}
                </span>
              ))}
            </div>
            {onClearAllQuoteReferences && (
              <button type="button" onClick={onClearAllQuoteReferences} className="composer-chip-clear-btn">清空引用</button>
            )}
          </div>
        )}

        {/* File references */}
        {fileReferences && fileReferences.length > 0 && (
          <div className="composer-chip-row">
            <div className="composer-chip-scroll">
              <div className="composer-chip-inner">
                {fileReferences.map((ref, i) => {
                  const lineInfo = ref.lineStart ? (ref.lineEnd && ref.lineEnd !== ref.lineStart ? `L${ref.lineStart}-L${ref.lineEnd}` : `L${ref.lineStart}`) : '';
                  const truncated = (ref.quote?.length ?? 0) > 50;
                  return (
                    <span key={ref.id} className="composer-file-ref-chip group">
                      {onJumpToReference ? (
                        <button type="button" onClick={() => onJumpToReference(ref)} className="composer-file-ref-jump-btn" title="在预览面板中跳转到该文件">
                          <svg className="h-3.5 w-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
                          <span className="max-w-[100px] truncate font-medium">{ref.name}</span>
                          {lineInfo && <span className="text-purple-400 shrink-0 text-[10px]">{lineInfo}</span>}
                          {ref.quote && <span className="max-w-[140px] truncate text-purple-400 italic">"{ref.quote.slice(0, 50)}{truncated ? '…' : ''}"</span>}
                        </button>
                      ) : (
                        <span className="flex items-center gap-1.5">
                          <svg className="h-3.5 w-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
                          <span className="max-w-[100px] truncate font-medium">{ref.name}</span>
                        </span>
                      )}
                      {onRemoveReference && (
                        <button type="button" className="composer-file-ref-remove-btn" onClick={(e) => { e.stopPropagation(); onRemoveReference(i); }} title="移除引用">
                          <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                        </button>
                      )}
                    </span>
                  );
                })}
              </div>
            </div>
            {onClearAllReferences && (
              <button type="button" onClick={onClearAllReferences} className="composer-chip-clear-btn">清空引用</button>
            )}
          </div>
        )}

        {/* ═══════════════════════════════════════
            Bottom bar: toolbar + textarea + send
            ═══════════════════════════════════════ */}
        <div className="composer-bottom">
          {/* ── Left: Quick action toolbar ── */}
          <div className="composer-toolbar">
            {/* File attach */}
            <label className="composer-toolbar-btn" title="上传附件 (Ctrl+U)">
              <QuickActionIcon iconId="attach" />
              <span className="composer-toolbar-label">附件</span>
              <input ref={fileInputRef} type="file" multiple className="sr-only" onChange={onFileChange} accept={ACCEPT_STRING} />
            </label>

            {/* Quick agent select */}
            <button type="button" className="composer-toolbar-btn" title="快速唤起 Agent" onClick={() => {
              const ta = textareaRef.current; if (ta) { ta.focus(); onInputChange({ target: { value: input + '@' } } as any); }
            }}>
              <QuickActionIcon iconId="agent" />
              <span className="composer-toolbar-label">Agent</span>
            </button>

            {/* Memory search */}
            <button type="button" className="composer-toolbar-btn" title="插入记忆片段">
              <QuickActionIcon iconId="memory" />
              <span className="composer-toolbar-label">记忆</span>
            </button>

            {/* Code exec */}
            <button type="button" className="composer-toolbar-btn" title="代码执行">
              <QuickActionIcon iconId="code" />
              <span className="composer-toolbar-label">代码</span>
            </button>

            {/* Draw */}
            <button type="button" className="composer-toolbar-btn" title="绘图">
              <QuickActionIcon iconId="draw" />
              <span className="composer-toolbar-label">绘图</span>
            </button>

            {/* Data analysis */}
            <button type="button" className="composer-toolbar-btn" title="数据分析">
              <QuickActionIcon iconId="data" />
              <span className="composer-toolbar-label">数据</span>
            </button>

            {/* Emoji */}
            <button ref={emojiButtonRef} type="button"
              className={`composer-toolbar-btn ${emojiOpen ? 'active' : ''}`}
              onClick={() => setEmojiOpen((v) => !v)} title="表情" aria-haspopup="dialog" aria-expanded={emojiOpen}>
              <svg className="qa-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>
              <span className="composer-toolbar-label">表情</span>
            </button>

            {/* Permission mode */}
            <span className="composer-toolbar-sep" aria-hidden />
            <PermissionModePopover value={execPermission} onChange={onExecPermissionChange} />

            {/* Auto-reply toggle */}
            <button type="button" onClick={() => onAutoReplyChange(!autoReply)}
              className={`composer-toolbar-toggle ${autoReply ? 'active' : ''}`}
              title={autoReply ? '自动回复已启用' : '仅发送模式'}>
              <span className="text-sm leading-none">{autoReply ? 'A' : 'M'}</span>
              <span className="composer-toggle-label">{autoReply ? '自动' : '仅发送'}</span>
            </button>

            {/* More actions */}
            <button type="button" className={`composer-toolbar-btn ${showMoreActions ? 'active' : ''}`}
              onClick={() => setShowMoreActions(!showMoreActions)} title="更多">
              <svg className="qa-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></svg>
            </button>
          </div>

          {/* ── Center: Textarea ── */}
          <div className="composer-input-area">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => { onInputChange(e); autoExpand(); }}
              onBlur={onBlur}
              onKeyDown={onKeyDown}
              onPaste={handlePaste}
              rows={2}
              className="composer-textarea"
              style={textareaHeight ? { height: `${textareaHeight}px` } : undefined}
              placeholder={
                isStreaming ? 'AI 正在回复中，新消息将中断当前输出...'
                  : isObserverInMultiUser ? '观察者模式 — 仅可发送纯文本消息'
                  : '输入消息，@指定智能体协同回答 · / 唤起快捷指令'
              }
            />

          {/* ── Right: Actions (now nested inside input-area) ── */}
          <div className="composer-actions">
            {/* Voice input */}
            <button type="button" className="composer-action-btn" title="语音输入">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                <path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/>
              </svg>
            </button>

            {/* Settings */}
            <button type="button" className="composer-action-btn" title="输入偏好设置">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
              </svg>
            </button>

            {/* Send button */}
            <button
              className={`composer-send-btn ${canSend ? '' : 'disabled'}`}
              onClick={onSend}
              disabled={!canSend}
              title={canSend ? '发送 (Ctrl+Enter)' : '请输入内容或添加附件'}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
              <span className="composer-send-label">{memberCount && memberCount > 1 ? '下发协同任务' : '发送'}</span>
            </button>
          </div>
          </div>
        </div>

        {/* Ctrl+Enter hint */}
        <div className="composer-hint">Ctrl+Enter 发送</div>
      </div>
    </div>
  );
});

export default ChatInput;
