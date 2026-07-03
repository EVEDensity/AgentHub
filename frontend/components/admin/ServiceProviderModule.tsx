import { type FormEvent, type JSX, useState } from 'react';
import dynamic from 'next/dynamic';
import type { Agent } from '../../types';
import { PLATFORM_LABELS, PLATFORM_COLORS } from '../../types';

const LocalAgentModal = dynamic(() => import('./LocalAgentModal'), {
  ssr: false,
  loading: () => null,
});
const AgentEditModal = dynamic(() => import('./AgentEditModal'), {
  ssr: false,
  loading: () => null,
});
const AgentVersionHistory = dynamic(() => import('./AgentVersionHistory'), {
  ssr: false,
  loading: () => null,
});

interface AdapterOption {
  id: string; name: string; description: string;
  default_model: string; default_base_url: string;
  requires_api_key: boolean; category: string;
}

export interface ServiceProviderModuleProps {
  // State
  agents: Agent[];
  agentTests: Record<string, { status: 'checking' | 'success' | 'failed'; message: string }>;
  adapterOptions: AdapterOption[];
  selectedAdapterInfo: AdapterOption | null;
  editSelectedAdapterInfo: AdapterOption | null;
  isCreatingAgent: boolean;
  showLocalAgentModal: boolean;
  editingAgentId: string | null;
  newAgent: {
    agentId: string; domain: string; adapterType: string; baseModelName: string;
    rankLevel: string; dutyNote: string; displayName: string; avatarUrl: string;
    capabilityTags: string[]; baseUrl: string; apiKey: string;
    systemPrompt: string; userPrompt: string; assistantPrompt: string;
    promptVariables: Record<string, string>;
  };
  editAgent: {
    agentId: string; domain: string; adapterType: string; baseModelName: string;
    rankLevel: string; dutyNote: string; displayName: string; avatarUrl: string;
    capabilityTags: string[]; baseUrl: string; apiKey: string;
    systemPrompt: string; userPrompt: string; assistantPrompt: string;
    promptVariables: Record<string, string>;
  };
  defaultChatAgent: string;
  // Setters
  setNewAgent: React.Dispatch<React.SetStateAction<ServiceProviderModuleProps['newAgent']>>;
  setEditAgent: React.Dispatch<React.SetStateAction<ServiceProviderModuleProps['editAgent']>>;
  setSelectedAdapterInfo: (v: AdapterOption | null) => void;
  setEditSelectedAdapterInfo: (v: AdapterOption | null) => void;
  setIsCreatingAgent: (v: boolean) => void;
  setShowLocalAgentModal: (v: boolean) => void;
  setEditingAgentId: (v: string | null) => void;
  // Actions
  handleAdapterChange: (value: string, mode: 'create' | 'edit') => void;
  createAgent: (e: FormEvent<HTMLFormElement>) => Promise<void>;
  testAgent: (agentId: string) => Promise<void>;
  removeAgent: (agentId: string) => Promise<void>;
  startEditAgent: (agent: Agent) => void;
  cancelEditAgent: () => void;
  saveAgentEdit: (e: FormEvent<HTMLFormElement>) => Promise<void>;
  handleSetDefaultChatAgent: (agentId: string) => Promise<void>;
  refresh: () => Promise<void>;
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
  fmtErr: (detail: unknown, fallback: string) => string;
}

export default function ServiceProviderModule(props: ServiceProviderModuleProps): JSX.Element {
  const isDefault = (a: Agent) => a.agentId === props.defaultChatAgent;
  const [versionAgentId, setVersionAgentId] = useState<string | null>(null);

  return (
    <section className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-[34px] font-semibold leading-tight text-warm-900">服务商</h2>
          <p className="mt-1 text-sm text-warm-500">管理 API 服务商以访问模型。</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-secondary" onClick={() => props.setShowLocalAgentModal(true)}>
            💻 接入本地 Agent
          </button>
          <button className="btn-primary" onClick={() => props.setIsCreatingAgent(true)}>
            + 添加服务商
          </button>
        </div>
      </div>

      {/* Default model explanation banner */}
      <div className="rounded-xl border border-primary-200 bg-primary-50 px-4 py-3">
        <div className="flex items-start gap-2">
          <svg className="h-5 w-5 shrink-0 text-primary-500 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
          <div>
            <p className="text-sm font-medium text-primary-800">关于默认对话模型</p>
            <p className="mt-0.5 text-sm text-primary-600">将某个服务商设为默认后，<strong>不含 @Agent 指令的日常对话</strong>将默认使用该模型进行响应。如需指定其他 Agent，在输入框中 @Agent 名称即可临时切换。</p>
          </div>
        </div>
      </div>

      {/* Create Agent Modal */}
      <AgentEditModal
        mode="create"
        visible={props.isCreatingAgent}
        onClose={() => { props.setIsCreatingAgent(false); props.setSelectedAdapterInfo(null); }}
        agentForm={props.newAgent}
        setAgentForm={props.setNewAgent}
        adapterOptions={props.adapterOptions}
        selectedAdapterInfo={props.selectedAdapterInfo}
        onAdapterChange={(value) => props.handleAdapterChange(value, 'create')}
        editingAgentId={null}
        onSubmit={(e) => { void props.createAgent(e); }}
        authHeaders={props.authHeaders}
        setNotice={props.setNotice}
        fmtErr={props.fmtErr}
      />

      {/* Edit Agent Modal */}
      <AgentEditModal
        mode="edit"
        visible={props.editingAgentId !== null}
        onClose={props.cancelEditAgent}
        agentForm={props.editAgent}
        setAgentForm={props.setEditAgent}
        adapterOptions={props.adapterOptions}
        selectedAdapterInfo={props.editSelectedAdapterInfo}
        onAdapterChange={(value) => props.handleAdapterChange(value, 'edit')}
        editingAgentId={props.editingAgentId}
        onSubmit={(e) => { void props.saveAgentEdit(e); }}
        authHeaders={props.authHeaders}
        setNotice={props.setNotice}
        fmtErr={props.fmtErr}
      />

      {/* Agent list */}
      <div className="space-y-3">
        {props.agents.map((a) => {
          const test = props.agentTests[a.agentId];
          const online = a.status === 'online';
          const isDefaultAgent = isDefault(a);
          return (
            <div
              key={a.agentId}
              className={`rounded-2xl border bg-white px-5 py-4 ${isDefaultAgent ? 'border-primary-400 ring-1 ring-primary-200' : online ? 'border-green-400' : 'border-warm-200'}`}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0 flex items-start gap-3">
                  {a.avatarUrl ? (
                    <img src={a.avatarUrl} className="h-10 w-10 rounded-full object-cover shrink-0" alt={a.displayName || a.agentId} loading="lazy" decoding="async" />
                  ) : (
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-warm-200 text-warm-600 text-sm font-bold">
                      {(a.displayName || a.agentId)[0]}
                    </span>
                  )}
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={`inline-block h-2.5 w-2.5 rounded-full ${online ? 'bg-green-500' : 'bg-warm-400'}`} />
                      <span className="truncate text-2xl font-semibold text-warm-900">{a.agentId}</span>
                      {a.displayName && <span className="text-sm text-warm-500">{a.displayName}</span>}
                      {a.agentId === 'Architect' && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-gradient-to-r from-amber-500 to-orange-500 px-2 py-0.5 text-xs font-semibold text-white shadow-sm ring-1 ring-amber-300/60" title="主 Agent（PM / PMO）：负责任务拆解、调度、降级、仲裁与人工交接">
                          <svg className="h-3 w-3" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M5 16h14l1.5-9-4.5 3-4-6-4 6L3.5 7 5 16Zm0 2v2h14v-2H5Z" /></svg>
                          主 Agent
                        </span>
                      )}
                      <span className="rounded px-2 py-0.5 text-xs font-medium" style={{ backgroundColor: (PLATFORM_COLORS[a.adapterType] || '#6b7280') + '18', color: PLATFORM_COLORS[a.adapterType] || '#6b7280' }}>
                        {PLATFORM_LABELS[a.adapterType] || a.adapterType}
                      </span>
                      {a.adapterType?.startsWith('local_') && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700">💻 本地</span>
                      )}
                      {isDefaultAgent ? <span className="rounded bg-primary-100 px-2 py-0.5 text-xs font-medium text-primary-700">默认对话模型</span> : null}
                    </div>
                    <div className="mt-1 truncate text-sm text-warm-500">{a.baseUrl || '未配置地址'} · {a.baseModelName || '未配置模型'}</div>
                    <div className="mt-1 text-xs text-warm-400">Domain: {a.domain} · 职责: {a.dutyNote || '无'} · 位次: {a.rankLevel || 'L1'}</div>
                    {a.capabilityTags && a.capabilityTags.length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {a.capabilityTags.map((tag) => (
                          <span key={tag} className="rounded bg-warm-100 px-2 py-0.5 text-[10px] text-warm-500">{tag}</span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {!isDefaultAgent && (
                    <button className="btn-secondary px-3 py-1 text-sm" onClick={() => { void props.handleSetDefaultChatAgent(a.agentId); }}>设为默认对话模型</button>
                  )}
                  {isDefaultAgent && (
                    <span className="rounded bg-primary-50 px-3 py-1 text-xs text-primary-600">当前默认 · 日常对话使用此模型</span>
                  )}
                  <button className="btn-ghost px-3 py-1 text-sm" onClick={() => props.startEditAgent(a)}>编辑</button>
                  <button className="btn-ghost px-3 py-1 text-sm" onClick={() => { void props.testAgent(a.agentId); }}>测试</button>
                  <button
                    className={`btn-ghost px-3 py-1 text-sm ${versionAgentId === a.agentId ? 'text-primary-600 bg-primary-50' : ''}`}
                    onClick={() => setVersionAgentId(versionAgentId === a.agentId ? null : a.agentId)}
                    title="版本历史"
                  >
                    <span className="material-symbols-outlined text-[14px] align-middle">history</span> 版本
                  </button>
                  {a.agentId !== 'Orchestrator' && (
                    <button className="btn-ghost px-3 py-1 text-sm text-red-500" onClick={() => { void props.removeAgent(a.agentId); }}>删除</button>
                  )}
                </div>
              </div>
              {test ? (
                <div className={`mt-3 rounded px-3 py-2 text-sm ${test.status === 'success' ? 'bg-green-50 text-green-700' : test.status === 'failed' ? 'bg-red-50 text-red-600' : 'bg-blue-50 text-blue-600'}`}>
                  {test.message}
                </div>
              ) : null}
              {/* Version History Panel (P1-6) */}
              {versionAgentId === a.agentId && (
                <div className="mt-3">
                  <AgentVersionHistory agentId={a.agentId} onClose={() => setVersionAgentId(null)} />
                </div>
              )}
            </div>
          );
        })}
        {!props.agents.length && <div className="text-caption text-warm-400">暂无服务商</div>}
      </div>

      {/* Local Agent Modal */}
      <LocalAgentModal
        visible={props.showLocalAgentModal}
        onClose={() => props.setShowLocalAgentModal(false)}
        onRegistered={() => { void props.refresh(); }}
        authHeaders={props.authHeaders}
      />
    </section>
  );
}
