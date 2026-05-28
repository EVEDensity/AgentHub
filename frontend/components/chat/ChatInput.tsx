import { memo } from 'react';
import type { Agent, AttachedFile, WorkflowSummary } from '../../types';

const FILE_CATEGORY_CONFIG: Record<string, { label: string; extensions: string[]; mimePattern: RegExp }> = {
  code: { label: 'Code', extensions: ['py','js','ts','jsx','tsx','java','go','rs','c','cpp','h','hpp','swift','kt','rb','php','sql','sh','bash','vue','svelte','astro'], mimePattern: /^(text\/|\b(?:javascript|typescript|json)\b)/ },
  document: { label: 'Document', extensions: ['txt','md','pdf','docx','rtf','tex','rst','org','log'], mimePattern: /^(text\/|application\/pdf|application\/vnd\.openxmlformats)/ },
  image: { label: 'Image', extensions: ['png','jpg','jpeg','gif','svg','webp','bmp','ico'], mimePattern: /^image\// },
  archive: { label: 'Archive', extensions: ['zip','rar','7z','tar','gz','bz2','xz'], mimePattern: /^(application\/zip|application\/x-rar|application\/x-7z|application\/gzip|application\/x-tar)/ },
  spreadsheet: { label: 'Sheet', extensions: ['xlsx','xls','csv','tsv'], mimePattern: /^(application\/vnd\.(ms-excel|openxmlformats-officedocument\.spreadsheetml)|text\/csv)/ },
  config: { label: 'Config', extensions: ['json','yaml','yml','xml','toml','ini','cfg','env','conf','cnf','editorconfig','gitignore','dockerfile','makefile','prisma','graphql','proto'], mimePattern: /^(application\/json|application\/xml|text\/(xml|yaml|toml))/ },
  unknown: { label: 'File', extensions: [], mimePattern: /^$/ },
};

const ALL_EXTENSIONS: Set<string> = new Set();
Object.values(FILE_CATEGORY_CONFIG).forEach((c) => c.extensions.forEach((e) => ALL_EXTENSIONS.add(e)));
const ACCEPT_STRING = Array.from(ALL_EXTENSIONS).map((e) => `.${e}`).join(',');

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

interface ChatInputProps {
  input: string;
  isStreaming: boolean;
  attachedFiles: AttachedFile[];
  mentionOpen: boolean;
  mentionTrigger: '@' | '#';
  mentionSearch: string;
  mentionActiveIndex: number;
  selectedRiskLevel: string;
  filteredAgents: Agent[];
  filteredWorkflows: WorkflowSummary[];
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  mentionPanelRef: React.RefObject<HTMLDivElement | null>;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  onInputChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
  onBlur: () => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  onSend: () => void;
  onPreview: () => void;
  onFileChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onRemoveFile: (index: number) => void;
  onInsertMention: (agentId: string) => void;
  onInsertAllMentions: () => void;
  onInsertWorkflow: (wf: WorkflowSummary) => void;
  onMentionSearchChange: (q: string) => void;
  onMentionActiveIndexChange: (idx: number) => void;
  onRiskLevelChange: (level: string) => void;
}

const ChatInput = memo(function ChatInput({
  input, isStreaming, attachedFiles,
  mentionOpen, mentionTrigger, mentionSearch, mentionActiveIndex, selectedRiskLevel,
  filteredAgents, filteredWorkflows,
  textareaRef, mentionPanelRef, fileInputRef,
  onInputChange, onBlur, onKeyDown, onSend, onPreview, onFileChange, onRemoveFile,
  onInsertMention, onInsertAllMentions, onInsertWorkflow,
  onMentionSearchChange, onMentionActiveIndexChange, onRiskLevelChange,
}: ChatInputProps) {
  return (
    <footer className="relative border-t border-warm-150 bg-white px-6 py-4">
      {mentionOpen && mentionTrigger === '@' && (
        <div ref={mentionPanelRef} className="absolute bottom-24 left-6 z-20 w-[520px] rounded-xl border border-warm-150 bg-white p-3 shadow-modal">
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
                    className={`rounded-lg px-3 py-2 text-left border ${
                      idx === mentionActiveIndex
                        ? 'bg-primary-50 border-primary-300 ring-1 ring-primary-300'
                        : 'bg-warm-50 border-transparent hover:bg-primary-50'
                    }`}
                    onClick={() => onInsertMention(agent.agentId)}
                    onMouseEnter={() => onMentionActiveIndexChange(idx)}
                  >
                    <div className="font-medium text-warm-700">@{agent.agentId}</div>
                    <div className="text-caption text-warm-500">{agent.domain} / {agent.rankLevel || 'L1'}</div>
                  </button>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      {mentionOpen && mentionTrigger === '#' && (
        <div ref={mentionPanelRef} className="absolute bottom-24 left-6 z-20 w-[520px] rounded-xl border border-warm-150 bg-white p-3 shadow-modal">
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
      <div className="flex gap-3">
        <div className="flex flex-1 flex-col gap-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={onInputChange}
            onBlur={onBlur}
            onKeyDown={onKeyDown}
            rows={3}
            className="input-field w-full resize-none"
            placeholder={isStreaming ? 'AI is streaming, new message will interrupt current output...' : 'Type message, supports @Agent directives...'}
          />
          {attachedFiles.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {attachedFiles.map((f, i) => (
                <span key={`${f.name}-${i}`} className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs border ${
                  f.uploadStatus === 'error' ? 'bg-danger-50 border-danger-200 text-danger-700' :
                  f.uploadStatus === 'uploading' ? 'bg-primary-50 border-primary-200 text-warm-700' :
                  'bg-warm-100 border-warm-150 text-warm-700'
                }`}>
                  <FileIcon category={f.category} size={3.5} />
                  <span className="max-w-[140px] truncate">{f.name}</span>
                  <span className="text-warm-400">{formatSize(f.size)}</span>
                  {f.uploadStatus === 'uploading' && (
                    <span className="flex items-center gap-1 text-primary-600">
                      <span className="h-2 w-12 overflow-hidden rounded-full bg-primary-100">
                        <span className="block h-full rounded-full bg-primary-500 transition-all" style={{ width: `${f.uploadProgress || 0}%` }} />
                      </span>
                      <span className="text-[10px]">{f.uploadProgress || 0}%</span>
                    </span>
                  )}
                  {f.uploadStatus === 'error' && (
                    <span className="text-[10px] text-danger-500" title={f.uploadError}>失败</span>
                  )}
                  {f.uploadStatus !== 'uploading' && (
                    <button className="ml-0.5 text-warm-400 hover:text-danger-500" onClick={() => onRemoveFile(i)} title="Remove">
                      <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                  )}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <button className="btn-primary" onClick={onSend}>Send</button>
          <button className="btn-secondary" onClick={onPreview}>Preview</button>
          <label className="btn-ghost flex cursor-pointer items-center justify-center p-2" title="Attach file">
            <svg className="h-5 w-5 text-warm-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
            <input ref={fileInputRef} type="file" multiple className="sr-only" onChange={onFileChange} accept={ACCEPT_STRING} />
          </label>
        </div>
      </div>
    </footer>
  );
});

export default ChatInput;
