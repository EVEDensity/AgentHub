import { useState, type JSX } from 'react';
import dynamic from 'next/dynamic';
import type { MemoryFileInfo, MemoryDetail, MemorySearchResult, ConsolidationResult } from '../../types';

const MarkdownRenderer = dynamic(() => import('../chat/MarkdownRenderer'), {
  ssr: false,
  loading: () => <div className="skeleton skeleton-text !h-4 w-3/4" />,
});

type MemorySubTab = 'files' | 'sessions' | 'conversation' | 'consolidation';
type SessionMemoryItem = {
  session_id: string; session_name: string; topic: string;
  created_at: string; updated_at: string;
  conversation_size_chars: number; turn_count: number; is_active: boolean;
};

export interface MemoryModuleProps {
  // State
  memoryLoading: boolean; memoryError: string; memoryKeyword: string;
  memoryFiles: MemoryFileInfo[]; activeMemoryFile: string | null;
  memoryDetail: MemoryDetail | null; memoryBodyDraft: string;
  memoryDirty: boolean; memoryPreview: boolean;
  memorySubTab: MemorySubTab;
  sessionList: Array<{ session_id: string; preview: string; updated_at: string }>;
  sessionsLoading: boolean; activeSessionId: string | null;
  activeSessionSummary: string; globalSummary: string;
  globalSummaryLoading: boolean; consolidationLoading: boolean;
  consolidationResult: ConsolidationResult | null; consolidationError: string;
  consolidationDryRun: boolean; memorySearchQuery: string;
  memorySearchResults: MemorySearchResult[] | null; memorySearchLoading: boolean;
  showTrash: boolean;
  trashItems: Array<{ trash_name: string; original_name: string; deleted_at: string; days_elapsed: number; days_remaining: number; expired: boolean }>;
  trashLoading: boolean; showDeleteConfirm: boolean;
  pendingDeleteFile: { filename: string; name: string } | null;
  // Session Memory Store
  sessionMemoryList: SessionMemoryItem[];
  sessionMemoryLoading: boolean;
  activeSessionMemoryId: string | null;
  sessionMemoryConversation: string;
  sessionMemoryConversationLoading: boolean;
  // Derived
  filteredMemoryFiles: MemoryFileInfo[];
  // Setters
  setMemoryKeyword: (v: string) => void;
  setMemorySearchQuery: (v: string) => void;
  setMemorySearchResults: (v: MemorySearchResult[] | null) => void;
  setActiveMemoryFile: (v: string | null) => void;
  setMemoryBodyDraft: (v: string) => void;
  setMemoryDirty: (v: boolean) => void;
  setMemoryPreview: (v: boolean) => void;
  setMemorySubTab: (v: MemorySubTab) => void;
  setShowTrash: (v: boolean) => void;
  setShowDeleteConfirm: (v: boolean) => void;
  setPendingDeleteFile: (v: { filename: string; name: string } | null) => void;
  setConsolidationDryRun: (v: boolean) => void;
  // Actions
  loadMemoryFiles: () => Promise<void>;
  loadMemoryDetail: (filename: string) => Promise<void>;
  saveMemoryDetail: () => Promise<void>;
  handleExportMemory: () => void;
  handleImportMemory: (e: React.ChangeEvent<HTMLInputElement>) => void;
  confirmDeleteMemory: (filename: string, name: string) => void;
  handleDeleteMemory: () => Promise<void>;
  loadTrash: () => Promise<void>;
  handleRecoverFromTrash: (trashName: string) => Promise<void>;
  handlePurgeFromTrash: (trashName: string) => Promise<void>;
  loadSessionSummaries: () => Promise<void>;
  loadSessionDetail: (sessionId: string) => Promise<void>;
  loadGlobalSummary: () => Promise<void>;
  refreshGlobalSummary: () => Promise<void>;
  runConsolidation: (dryRun: boolean) => Promise<void>;
  runMemorySearch: () => Promise<void>;
  // Session Memory Store actions
  loadSessionMemoryList: () => Promise<void>;
  loadSessionMemoryConversation: (sessionId: string) => Promise<void>;
  consolidateSessionMemory: (sessionId: string) => Promise<void>;
  createMemorySession: (sessionId: string, sessionName: string, topic: string) => Promise<void>;
  updateSessionTopic: (sessionId: string, topic: string) => Promise<void>;
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
}

const SUB_TABS: Array<{ key: MemorySubTab; label: string }> = [
  { key: 'files', label: '文件管理' },
  { key: 'sessions', label: '会话摘要' },
  { key: 'conversation', label: '对话记忆' },
  { key: 'consolidation', label: '记忆整理' },
];

export default function MemoryModule(props: MemoryModuleProps): JSX.Element {
  return (
    <section className="rounded-2xl border border-warm-200 bg-white flex-1 flex flex-col min-h-0">
      <nav className="flex shrink-0 border-b border-warm-150">
        {SUB_TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => props.setMemorySubTab(tab.key)}
            className={`px-6 py-3 text-sm font-medium border-b-2 transition-colors ${props.memorySubTab === tab.key ? 'border-primary-500 text-primary-700' : 'border-transparent text-warm-500 hover:text-warm-700'}`}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      {props.memorySubTab === 'files' && <FilesTab {...props} />}
      {props.memorySubTab === 'sessions' && <SessionsTab {...props} />}
      {props.memorySubTab === 'conversation' && <ConversationTab {...props} />}
      {props.memorySubTab === 'consolidation' && <ConsolidationTab {...props} />}
    </section>
  );
}

function FilesTab(props: MemoryModuleProps): JSX.Element {
  return (
    <div className="grid flex-1 grid-cols-[300px_1fr] min-h-0">
      {/* ── Left sidebar ── */}
      <aside className="border-r border-warm-150 bg-[#FBFAF8] flex flex-col overflow-hidden">
        <div className="border-b border-warm-150 px-4 py-3 shrink-0">
          <div className="text-base font-semibold text-warm-900">项目记忆</div>
          <div className="text-xs text-warm-500">共 {props.memoryFiles.length} 个文件</div>
        </div>

        {/* Content search bar */}
        <div className="border-b border-warm-150 px-4 py-3 space-y-2 shrink-0">
          <div className="text-xs font-medium text-warm-600">内容搜索</div>
          <div className="flex items-center gap-1">
            <input
              className="flex-1 min-w-0 rounded-lg border border-warm-200 bg-white px-3 py-1.5 text-sm outline-none focus:border-primary-300"
              placeholder="搜索记忆内容..."
              value={props.memorySearchQuery}
              onChange={(e) => props.setMemorySearchQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { void props.runMemorySearch(); } }}
            />
            <button
              className="btn-primary shrink-0 px-3 py-1.5 text-xs rounded-lg"
              disabled={!props.memorySearchQuery.trim() || props.memorySearchLoading}
              onClick={() => { void props.runMemorySearch(); }}
            >
              {props.memorySearchLoading ? '...' : '搜索'}
            </button>
          </div>
          {props.memorySearchResults !== null && (
            <button
              className="text-xs text-warm-400 hover:text-warm-600 underline"
              onClick={() => { props.setMemorySearchResults(null); props.setMemorySearchQuery(''); }}
            >
              清除搜索结果（{props.memorySearchResults.length} 条）
            </button>
          )}
        </div>

        {/* Filename filter */}
        <div className="border-b border-warm-150 px-4 py-3 shrink-0">
          <div className="mb-2 text-xs font-medium text-warm-600">文件筛选</div>
          <input
            className="w-full rounded-lg border border-warm-200 bg-white px-3 py-1.5 text-sm outline-none focus:border-primary-300"
            placeholder="筛选文件名..."
            value={props.memoryKeyword}
            onChange={(e) => props.setMemoryKeyword(e.target.value)}
          />
        </div>

        <div className="flex-1 overflow-auto px-3 py-3">
          {props.memoryLoading ? <div className="px-2 py-2 text-xs text-warm-500">加载中...</div> : null}
          {props.memoryError ? <div className="px-2 py-2 text-xs text-red-500">{props.memoryError}</div> : null}

          {/* Content search results */}
          {props.memorySearchResults !== null && (
            <>
              <div className="mb-2 text-xs font-medium text-primary-600">搜索结果</div>
              {props.memorySearchResults.length === 0 && (
                <div className="px-2 py-2 text-xs text-warm-400">无匹配结果</div>
              )}
              {props.memorySearchResults.map((r) => (
                <button
                  key={r.filename}
                  className="mb-1 block w-full rounded-lg border px-3 py-2 text-left border-transparent hover:border-primary-200 hover:bg-primary-50/50"
                  onClick={() => {
                    props.setMemorySearchResults(null);
                    props.setMemorySearchQuery('');
                    props.setActiveMemoryFile(r.filename);
                    void props.loadMemoryDetail(r.filename);
                  }}
                >
                  <div className="truncate text-sm font-medium text-warm-800">{r.name}</div>
                  <div className="mt-0.5 truncate text-xs text-warm-500">{r.snippet}</div>
                  <div className="mt-0.5 text-[10px] text-warm-400">{r.filename} · 相关度: {(r.score * 100).toFixed(0)}%</div>
                </button>
              ))}
              <div className="my-2 border-t border-warm-150" />
              <div className="mb-2 text-xs font-medium text-warm-400">全部文件</div>
            </>
          )}

          {/* File list */}
          {!props.memoryLoading && !props.filteredMemoryFiles.length && props.memorySearchResults === null ? (
            <div className="px-2 py-2 text-xs text-warm-400">暂无记忆文件</div>
          ) : null}

          {props.memorySearchResults === null && props.filteredMemoryFiles.map((f) => (
            <button
              key={f.filename}
              className={`mb-1 block w-full rounded-lg border px-3 py-2 text-left ${props.activeMemoryFile === f.filename ? 'border-warm-300 bg-warm-100' : 'border-transparent hover:border-warm-200 hover:bg-warm-50'}`}
              onClick={() => {
                props.setActiveMemoryFile(f.filename);
                void props.loadMemoryDetail(f.filename);
              }}
            >
              <div className="truncate text-sm font-medium text-warm-800">{f.name}</div>
              <div className="mt-1 truncate text-xs text-warm-500">{f.filename}</div>
            </button>
          ))}
        </div>
      </aside>

      {/* ── Right: editor + preview ── */}
      <div className="min-w-0 flex flex-col overflow-hidden">
        <header className="flex items-center justify-between border-b border-warm-150 px-5 py-3 shrink-0">
          <div>
            <div className="text-lg font-semibold text-warm-900">{props.memoryDetail?.meta.name || 'MEMORY.md'}</div>
            <div className="text-xs text-warm-500">{props.activeMemoryFile || '未选择文件'}</div>
          </div>
          <div className="flex items-center gap-2">
            <button className="btn-secondary px-3 py-1.5 text-sm" onClick={() => { void props.loadMemoryFiles(); }}>刷新</button>
            <button className="btn-secondary px-3 py-1.5 text-sm" onClick={() => props.setMemoryPreview(!props.memoryPreview)}>{props.memoryPreview ? '编辑' : '预览'}</button>
            <button className="btn-primary px-3 py-1.5 text-sm" disabled={!props.memoryDirty} onClick={() => { void props.saveMemoryDetail(); }}>保存</button>
            <button className="btn-primary px-3 py-1.5 text-sm" disabled={!props.activeMemoryFile} onClick={props.handleExportMemory}>导出</button>
            <button className="btn-secondary px-3 py-1.5 text-sm" onClick={() => document.getElementById('memory-import-input')?.click()}>导入</button>
            <input id="memory-import-input" type="file" accept=".md,.markdown,.txt" className="hidden" onChange={(e) => { props.handleImportMemory(e); }} />
            <button
              className="btn-secondary px-3 py-1.5 text-sm text-red-600 border-red-200 hover:bg-red-50"
              disabled={!props.activeMemoryFile}
              onClick={() => {
                if (props.activeMemoryFile) {
                  const file = props.filteredMemoryFiles.find(f => f.filename === props.activeMemoryFile);
                  const name = props.memoryDetail?.meta?.name || file?.name || props.activeMemoryFile;
                  props.confirmDeleteMemory(props.activeMemoryFile, name);
                }
              }}
            >删除</button>
            <button
              className={`px-3 py-1.5 text-sm ${props.showTrash ? 'bg-amber-100 text-amber-800 border border-amber-300' : 'btn-secondary'}`}
              onClick={() => {
                props.setShowTrash(!props.showTrash);
                if (!props.showTrash) { void props.loadTrash(); }
              }}
            >暂存区{props.trashItems.length > 0 ? ` (${props.trashItems.length})` : ''}</button>
          </div>
        </header>

        {/* Trash panel */}
        {props.showTrash && (
          <div className="border-b border-amber-200 bg-amber-50/50 px-5 py-3">
            <div className="flex items-center justify-between mb-3">
              <div>
                <span className="text-sm font-semibold text-amber-800">🗑 暂存区</span>
                <span className="ml-2 text-xs text-amber-600">已删除的记忆在此保留 30 天，过期后自动永久删除</span>
              </div>
              <button className="text-xs text-amber-600 hover:text-amber-800 underline" onClick={() => props.setShowTrash(false)}>关闭</button>
            </div>
            {props.trashLoading ? (
              <div className="text-xs text-warm-500 py-2">加载中...</div>
            ) : props.trashItems.length === 0 ? (
              <div className="text-xs text-warm-400 py-2">暂存区为空</div>
            ) : (
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {props.trashItems.map((item) => (
                  <div key={item.trash_name} className={`flex items-center justify-between rounded-lg border px-3 py-2 ${item.expired ? 'border-red-200 bg-red-50' : 'border-amber-200 bg-white'}`}>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-warm-800">{item.original_name}</div>
                      <div className="text-xs text-warm-500">
                        删除于 {item.deleted_at} · {item.days_remaining > 0 ? `剩余 ${Math.ceil(item.days_remaining)} 天` : '已过期'}
                      </div>
                    </div>
                    <div className="flex items-center gap-1 ml-3 shrink-0">
                      {!item.expired && (
                        <button className="rounded px-2 py-1 text-xs text-green-700 bg-green-100 hover:bg-green-200" onClick={() => { void props.handleRecoverFromTrash(item.trash_name); }}>恢复</button>
                      )}
                      <button className="rounded px-2 py-1 text-xs text-red-600 bg-red-100 hover:bg-red-200" onClick={() => { void props.handlePurgeFromTrash(item.trash_name); }}>永久删除</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="grid flex-1 grid-cols-2 min-h-0">
          <div className="border-r border-warm-150 flex flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b border-warm-150 px-4 py-2 shrink-0">
              <span className="text-xs font-medium tracking-wide text-warm-600">编辑</span>
              <span className="text-xs text-warm-400">MARKDOWN</span>
            </div>
            <textarea
              className="flex-1 w-full resize-none border-0 bg-white px-4 py-3 font-mono text-[13px] leading-6 text-warm-800 outline-none"
              value={props.memoryBodyDraft}
              onChange={(e) => {
                props.setMemoryBodyDraft(e.target.value);
                props.setMemoryDirty(true);
              }}
              placeholder="请输入记忆内容..."
              disabled={props.memoryPreview || !props.memoryDetail}
            />
          </div>
          <div className="flex flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b border-warm-150 px-4 py-2 shrink-0">
              <span className="text-xs font-medium tracking-wide text-warm-600">预览</span>
              <span className="text-xs text-warm-400">已渲染</span>
            </div>
            <div className="flex-1 overflow-auto bg-[#FCFCFB] px-4 py-3">
              <MarkdownRenderer content={props.memoryBodyDraft || '（空内容）'} />
            </div>
          </div>
        </div>
      </div>

      {/* Delete confirmation modal */}
      {props.showDeleteConfirm && props.pendingDeleteFile && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => { props.setShowDeleteConfirm(false); props.setPendingDeleteFile(null); }}>
          <div className="bg-white rounded-xl shadow-modal max-w-md w-full mx-4 p-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-3 mb-4">
              <span className="text-2xl">⚠️</span>
              <div>
                <div className="text-lg font-semibold text-warm-900">确认删除记忆</div>
                <div className="text-sm text-warm-600 mt-0.5">「{props.pendingDeleteFile.name}」</div>
              </div>
            </div>
            <div className="mb-2 p-3 rounded-lg bg-amber-50 border border-amber-200 text-sm text-amber-800">
              <p className="font-medium mb-1">📋 删除说明：</p>
              <ul className="list-disc list-inside space-y-1 text-xs">
                <li>删除后文件将进入<strong>暂存区</strong>保存 <strong>30 天</strong></li>
                <li>30 天内可随时从暂存区恢复到原位置</li>
                <li>超过 30 天后系统将<strong>永久删除</strong>，无法恢复</li>
              </ul>
            </div>
            <div className="flex justify-end gap-3 mt-4">
              <button className="px-4 py-2 text-sm rounded-lg border border-warm-200 text-warm-700 hover:bg-warm-50" onClick={() => { props.setShowDeleteConfirm(false); props.setPendingDeleteFile(null); }}>取消</button>
              <button className="px-4 py-2 text-sm rounded-lg bg-red-600 text-white hover:bg-red-700" onClick={() => { void props.handleDeleteMemory(); }}>确认删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SessionsTab(props: MemoryModuleProps): JSX.Element {
  return (
    <div className="grid flex-1 grid-cols-[320px_1fr] min-h-0">
      <aside className="border-r border-warm-150 bg-[#FBFAF8] flex flex-col overflow-hidden">
        <div className="border-b border-warm-150 px-4 py-3 shrink-0">
          <div className="text-base font-semibold text-warm-900">会话摘要</div>
          <div className="text-xs text-warm-500">共 {props.sessionList.length} 个会话</div>
        </div>

        {/* Global summary card */}
        <div className="border-b border-warm-150 px-4 py-3 shrink-0">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-warm-600">全局摘要</span>
            <button
              className="text-[10px] text-primary-500 hover:text-primary-700"
              disabled={props.globalSummaryLoading}
              onClick={() => { void props.refreshGlobalSummary(); }}
            >
              {props.globalSummaryLoading ? '刷新中...' : '强制刷新'}
            </button>
          </div>
          {props.globalSummaryLoading ? (
            <div className="text-xs text-warm-400">加载中...</div>
          ) : props.globalSummary ? (
            <div className="text-xs text-warm-600 leading-relaxed max-h-24 overflow-auto">{props.globalSummary}</div>
          ) : (
            <div className="text-xs text-warm-400">暂无全局摘要</div>
          )}
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-auto px-3 py-3">
          {props.sessionsLoading ? <div className="px-2 py-2 text-xs text-warm-500">加载中...</div> : null}
          {!props.sessionsLoading && !props.sessionList.length ? (
            <div className="px-2 py-2 text-xs text-warm-400">暂无会话摘要 — 发送消息后系统会自动生成</div>
          ) : null}
          {props.sessionList.map((s) => (
            <button
              key={s.session_id}
              className={`mb-1 block w-full rounded-lg border px-3 py-2 text-left ${props.activeSessionId === s.session_id ? 'border-primary-300 bg-primary-50' : 'border-transparent hover:border-warm-200 hover:bg-warm-50'}`}
              onClick={() => { void props.loadSessionDetail(s.session_id); }}
            >
              <div className="truncate text-xs font-medium text-warm-800 font-mono">{s.session_id}</div>
              <div className="mt-1 text-xs text-warm-500 leading-relaxed line-clamp-2">{s.preview}</div>
              <div className="mt-1 text-[10px] text-warm-400">{s.updated_at ? new Date(s.updated_at).toLocaleString() : '—'}</div>
            </button>
          ))}
        </div>
      </aside>

      <div className="min-w-0 flex flex-col overflow-hidden">
        <header className="flex items-center justify-between border-b border-warm-150 px-5 py-3 shrink-0">
          <div>
            <div className="text-base font-semibold text-warm-900 font-mono text-sm">{props.activeSessionId || '未选择会话'}</div>
            <div className="text-xs text-warm-500">会话摘要详情</div>
          </div>
          {props.activeSessionId && (
            <button
              className="btn-ghost px-3 py-1.5 text-xs text-red-500"
              onClick={async () => {
                if (!window.confirm(`确认重置会话 ${props.activeSessionId} 的摘要？`)) return;
                await fetch(`/api/memory/sessions/reset/${encodeURIComponent(props.activeSessionId!)}`, { method: 'POST', headers: props.authHeaders() });
                void props.loadSessionSummaries();
                props.setNotice('会话摘要已重置');
              }}
            >重置摘要</button>
          )}
        </header>
        <div className="flex-1 overflow-auto bg-[#FCFCFB] px-6 py-4">
          {!props.activeSessionId ? (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <span className="text-5xl text-warm-300 mb-4">📋</span>
              <p className="text-sm text-warm-500">从左侧选择一个会话查看其自动生成的摘要</p>
              <p className="text-xs text-warm-400 mt-1">会话摘要在您发送消息后自动生成，汇总了对话中的关键信息</p>
            </div>
          ) : props.activeSessionSummary ? (
            <div className="text-sm text-warm-700 leading-relaxed whitespace-pre-wrap">{props.activeSessionSummary}</div>
          ) : (
            <div className="text-sm text-warm-400">（空摘要）</div>
          )}
        </div>
      </div>
    </div>
  );
}

function ConsolidationTab(props: MemoryModuleProps): JSX.Element {
  return (
    <div className="flex-1 flex flex-col min-h-0">
      <header className="flex items-center justify-between border-b border-warm-150 px-5 py-3 shrink-0">
        <div>
          <div className="text-base font-semibold text-warm-900">记忆整理（AutoDream）</div>
          <div className="text-xs text-warm-500">合并重复记忆、删除过时内容、更新过期信息</div>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-warm-600 cursor-pointer select-none">
            <input
              type="checkbox"
              className="rounded"
              checked={props.consolidationDryRun}
              onChange={(e) => props.setConsolidationDryRun(e.target.checked)}
            />
            仅分析（dry run）
          </label>
          <button
            className="btn-secondary px-4 py-1.5 text-sm"
            disabled={props.consolidationLoading}
            onClick={() => { void props.runConsolidation(true); }}
          >{props.consolidationLoading ? '分析中...' : '分析'}</button>
          <button
            className="btn-primary px-4 py-1.5 text-sm"
            disabled={props.consolidationLoading}
            onClick={() => {
              if (!window.confirm('确认执行记忆整理？此操作将实际合并/删除记忆文件。建议先执行"仅分析"预览变更。')) return;
              void props.runConsolidation(false);
            }}
          >{props.consolidationLoading ? '执行中...' : '执行整理'}</button>
        </div>
      </header>

      <div className="flex-1 overflow-auto px-5 py-4">
        {props.consolidationError && (
          <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600 mb-4">{props.consolidationError}</div>
        )}

        {!props.consolidationResult && !props.consolidationLoading && (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <span className="text-5xl text-warm-300 mb-4">🧠</span>
            <p className="text-sm text-warm-600 font-medium">AutoDream 记忆整理</p>
            <p className="text-xs text-warm-500 mt-1 max-w-md leading-relaxed">
              通过 LLM 分析所有记忆文件，自动检测重复内容、过时信息和需要更新的条目。
              建议先执行"仅分析"预览变更，确认无误后再执行实际整理。
            </p>
            <div className="mt-4 flex gap-3">
              <div className="rounded-lg border border-warm-200 bg-warm-50 px-4 py-2 text-center">
                <div className="text-lg font-semibold text-warm-800">{props.memoryFiles.length}</div>
                <div className="text-[10px] text-warm-400">当前记忆文件</div>
              </div>
            </div>
          </div>
        )}

        {props.consolidationLoading && (
          <div className="flex items-center justify-center py-12">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary-400 border-t-transparent" />
            <span className="ml-3 text-sm text-warm-500">正在分析记忆文件...</span>
          </div>
        )}

        {props.consolidationResult && (
          <div className="space-y-4">
            <div className="rounded-xl border border-primary-200 bg-primary-50/50 px-5 py-3">
              <div className="flex items-center gap-3 text-sm">
                {props.consolidationResult.dry_run ? (
                  <span className="tag tag-blue">仅分析 · DRY RUN</span>
                ) : (
                  <span className="tag tag-green">已执行</span>
                )}
                <span className="text-warm-600">
                  合并 {props.consolidationResult.merged?.length || 0} 项 ·
                  删除 {props.consolidationResult.deleted?.length || 0} 项 ·
                  更新 {props.consolidationResult.updated?.length || 0} 项 ·
                  保留 {props.consolidationResult.unchanged?.length || 0} 项
                </span>
              </div>
              {props.consolidationResult.summary && (
                <div className="mt-2 text-sm text-warm-600 leading-relaxed">{props.consolidationResult.summary}</div>
              )}
            </div>

            {/* Merged items */}
            {props.consolidationResult.merged && props.consolidationResult.merged.length > 0 && (
              <div>
                <h4 className="text-sm font-semibold text-green-700 mb-2">🟢 待合并（{props.consolidationResult.merged.length} 项）</h4>
                <div className="space-y-2">
                  {props.consolidationResult.merged.map((m, i) => (
                    <div key={i} className="rounded-lg border border-green-200 bg-green-50/30 px-4 py-2">
                      <div className="flex items-center gap-2 text-sm">
                        <span className="font-medium text-warm-800">{m.file}</span>
                        <span className="text-warm-400">←</span>
                        <span className="text-xs text-warm-500 font-mono">{m.targets.join(', ')}</span>
                      </div>
                      <div className="text-xs text-warm-500 mt-1">{m.reason}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Deleted items */}
            {props.consolidationResult.deleted && props.consolidationResult.deleted.length > 0 && (
              <div>
                <h4 className="text-sm font-semibold text-red-700 mb-2">🔴 待删除（{props.consolidationResult.deleted.length} 项）</h4>
                <div className="space-y-2">
                  {props.consolidationResult.deleted.map((d, i) => (
                    <div key={i} className="rounded-lg border border-red-200 bg-red-50/30 px-4 py-2">
                      <div className="text-sm font-medium text-red-800">{d.file}</div>
                      <div className="text-xs text-warm-500 mt-1">{d.reason}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Updated items */}
            {props.consolidationResult.updated && props.consolidationResult.updated.length > 0 && (
              <div>
                <h4 className="text-sm font-semibold text-blue-700 mb-2">🔵 待更新（{props.consolidationResult.updated.length} 项）</h4>
                <div className="space-y-2">
                  {props.consolidationResult.updated.map((u, i) => (
                    <div key={i} className="rounded-lg border border-blue-200 bg-blue-50/30 px-4 py-2">
                      <div className="text-sm font-medium text-blue-800">{u.file}</div>
                      <div className="text-xs text-warm-500 mt-1">{u.reason}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Unchanged items */}
            {props.consolidationResult.unchanged && props.consolidationResult.unchanged.length > 0 && (
              <details className="rounded-lg border border-warm-200 overflow-hidden">
                <summary className="px-4 py-2 text-sm text-warm-500 cursor-pointer select-none hover:bg-warm-50">
                  保留不变（{props.consolidationResult.unchanged.length} 项）
                </summary>
                <div className="px-4 py-2 border-t border-warm-150 max-h-48 overflow-auto">
                  {props.consolidationResult.unchanged.map((u, i) => (
                    <div key={i} className="text-xs text-warm-500 py-0.5">{u.file}</div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ConversationTab(props: MemoryModuleProps): JSX.Element {
  const [createForm, setCreateForm] = useState<{ visible: boolean; sessionId: string; name: string; topic: string }>({
    visible: false, sessionId: '', name: '', topic: '',
  });

  return (
    <div className="grid flex-1 grid-cols-[320px_1fr] min-h-0">
      {/* ── Left sidebar: session list ── */}
      <aside className="border-r border-warm-150 bg-[#FBFAF8] flex flex-col overflow-hidden">
        <div className="border-b border-warm-150 px-4 py-3 shrink-0">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-base font-semibold text-warm-900">对话记忆</div>
              <div className="text-xs text-warm-500">共 {props.sessionMemoryList.length} 个会话</div>
            </div>
            <button
              className="btn-primary px-3 py-1 text-xs rounded-lg"
              onClick={() => setCreateForm({ visible: true, sessionId: '', name: '', topic: '' })}
            >+ 新建</button>
          </div>
          <div className="mt-2 rounded-lg bg-primary-50 px-3 py-2 text-[11px] text-primary-700 leading-relaxed">
            每个会话的对话内容会自动追加到其专属记忆文件中，永久保存不会遗忘。
          </div>
        </div>

        {/* Create form */}
        {createForm.visible && (
          <div className="border-b border-warm-150 px-4 py-3 bg-amber-50/50">
            <div className="text-xs font-medium text-warm-700 mb-2">新建记忆会话</div>
            <input
              className="w-full mb-2 rounded-lg border border-warm-200 px-3 py-1.5 text-xs outline-none"
              placeholder="会话 ID"
              value={createForm.sessionId}
              onChange={(e) => setCreateForm(p => ({ ...p, sessionId: e.target.value }))}
            />
            <input
              className="w-full mb-2 rounded-lg border border-warm-200 px-3 py-1.5 text-xs outline-none"
              placeholder="会话名称（可选）"
              value={createForm.name}
              onChange={(e) => setCreateForm(p => ({ ...p, name: e.target.value }))}
            />
            <input
              className="w-full mb-2 rounded-lg border border-warm-200 px-3 py-1.5 text-xs outline-none"
              placeholder="话题标签（可选）"
              value={createForm.topic}
              onChange={(e) => setCreateForm(p => ({ ...p, topic: e.target.value }))}
            />
            <div className="flex items-center gap-2">
              <button
                className="btn-primary px-3 py-1 text-xs rounded-lg"
                disabled={!createForm.sessionId.trim()}
                onClick={() => {
                  void props.createMemorySession(createForm.sessionId, createForm.name, createForm.topic);
                  setCreateForm({ visible: false, sessionId: '', name: '', topic: '' });
                }}
              >创建</button>
              <button
                className="btn-secondary px-3 py-1 text-xs rounded-lg"
                onClick={() => setCreateForm({ visible: false, sessionId: '', name: '', topic: '' })}
              >取消</button>
            </div>
          </div>
        )}

        {/* Session list */}
        <div className="flex-1 overflow-auto px-3 py-3">
          {props.sessionMemoryLoading ? <div className="px-2 py-2 text-xs text-warm-500">加载中...</div> : null}
          {!props.sessionMemoryLoading && !props.sessionMemoryList.length ? (
            <div className="px-2 py-2 text-xs text-warm-400">暂无对话记忆 — 发送消息后自动创建</div>
          ) : null}
          {props.sessionMemoryList.map((s) => (
            <button
              key={s.session_id}
              className={`mb-1 block w-full rounded-lg border px-3 py-2 text-left ${props.activeSessionMemoryId === s.session_id ? 'border-primary-300 bg-primary-50' : 'border-transparent hover:border-warm-200 hover:bg-warm-50'}`}
              onClick={() => { void props.loadSessionMemoryConversation(s.session_id); }}
            >
              <div className="flex items-center gap-2">
                <span className="truncate text-xs font-medium text-warm-800 font-mono">{s.session_name || s.session_id}</span>
                {s.is_active && <span className="shrink-0 rounded-full bg-green-100 px-1.5 py-0.5 text-[10px] text-green-700">活跃</span>}
              </div>
              {s.topic && <div className="mt-0.5 text-[11px] text-primary-600">{s.topic}</div>}
              <div className="mt-1 flex items-center gap-2 text-[10px] text-warm-400">
                <span>{s.turn_count} 轮对话</span>
                <span>·</span>
                <span>{(s.conversation_size_chars / 1024).toFixed(1)} KB</span>
                <span>·</span>
                <span>{s.updated_at ? new Date(s.updated_at).toLocaleDateString() : '—'}</span>
              </div>
            </button>
          ))}
        </div>
      </aside>

      {/* ── Right: conversation viewer ── */}
      <div className="min-w-0 flex flex-col overflow-hidden">
        <header className="flex items-center justify-between border-b border-warm-150 px-5 py-3 shrink-0">
          <div>
            <div className="text-base font-semibold text-warm-900 font-mono text-sm">{props.activeSessionMemoryId || '未选择会话'}</div>
            <div className="text-xs text-warm-500">对话记忆 — 自动追加模式</div>
          </div>
          <div className="flex items-center gap-2">
            {props.activeSessionMemoryId && (
              <>
                <button
                  className="btn-secondary px-3 py-1.5 text-xs"
                  onClick={() => {
                    void props.loadSessionMemoryConversation(props.activeSessionMemoryId!);
                    props.setNotice('对话记忆已刷新');
                  }}
                >刷新</button>
                <button
                  className="btn-ghost px-3 py-1.5 text-xs text-primary-600"
                  onClick={() => {
                    void props.consolidateSessionMemory(props.activeSessionMemoryId!);
                  }}
                >整合压缩</button>
              </>
            )}
          </div>
        </header>

        {/* Conversation content */}
        <div className="flex-1 overflow-auto bg-[#FCFCFB] px-6 py-4">
          {!props.activeSessionMemoryId ? (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <span className="text-5xl text-warm-300 mb-4">💬</span>
              <p className="text-sm text-warm-500">从左侧选择一个会话查看其对话记忆</p>
              <p className="text-xs text-warm-400 mt-1 max-w-md leading-relaxed">
                对话记忆以追加模式维护，每次对话自动添加。长对话会自动压缩早期内容。
              </p>
            </div>
          ) : props.sessionMemoryConversationLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary-400 border-t-transparent" />
              <span className="ml-3 text-sm text-warm-500">加载中...</span>
            </div>
          ) : props.sessionMemoryConversation ? (
            <div className="text-sm text-warm-700 leading-relaxed whitespace-pre-wrap font-mono text-xs">
              {props.sessionMemoryConversation}
            </div>
          ) : (
            <div className="text-sm text-warm-400">（此会话暂无对话记忆）</div>
          )}
        </div>
      </div>
    </div>
  );
}
