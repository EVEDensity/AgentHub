'use client';

import { type FormEvent, type JSX } from 'react';
import TagInput from './TagInput';

interface AdapterOption {
  id: string; name: string; description: string;
  default_model: string; default_base_url: string;
  requires_api_key: boolean; category: string;
}

interface AgentFormState {
  agentId: string; domain: string; adapterType: string; baseModelName: string;
  rankLevel: string; dutyNote: string; displayName: string; avatarUrl: string;
  capabilityTags: string[]; baseUrl: string; apiKey: string;
}

export interface AgentEditModalProps {
  mode: 'create' | 'edit';
  visible: boolean;
  onClose: () => void;
  // Form state
  agentForm: AgentFormState;
  setAgentForm: (updater: AgentFormState | ((prev: AgentFormState) => AgentFormState)) => void;
  // Adapter
  adapterOptions: AdapterOption[];
  selectedAdapterInfo: AdapterOption | null;
  onAdapterChange: (value: string) => void;
  // Edit-specific
  editingAgentId: string | null;
  // Actions
  onSubmit: (e: FormEvent<HTMLFormElement>) => void;
  // Upload
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
  fmtErr: (detail: unknown, fallback: string) => string;
}

export default function AgentEditModal(props: AgentEditModalProps): JSX.Element | null {
  if (!props.visible) return null;

  const { mode, agentForm: f, setAgentForm: setF, editingAgentId } = props;
  const isEdit = mode === 'edit';

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 px-4 pt-[10vh] pb-8 overflow-y-auto"
      onClick={(e) => {
        if (e.target === e.currentTarget) props.onClose();
      }}
    >
      {/* Prevent propagation to keep modal open when clicking inside */}
      <div
        className="w-full max-w-2xl rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* ── Header ────────────────────────────────────────────── */}
        <div className="flex items-center justify-between border-b border-warm-150 px-6 py-4">
          <div>
            <h3 className="text-lg font-semibold text-warm-900">
              {isEdit ? (
                <>✏️ 编辑服务商：<span className="text-primary-600">{editingAgentId}</span></>
              ) : (
                <>➕ 添加新服务商</>
              )}
            </h3>
            <p className="mt-1 text-sm text-warm-500">
              {isEdit
                ? '修改 Agent 配置信息和连接参数'
                : '配置新的大模型 API 服务商连接信息'}
            </p>
          </div>
          <button
            className="rounded-lg px-3 py-1.5 text-sm text-warm-500 hover:bg-warm-100 transition-colors"
            onClick={props.onClose}
          >
            关闭
          </button>
        </div>

        {/* ── Body ─────────────────────────────────────────────── */}
        <form
          className="px-6 py-5"
          onSubmit={(e) => { void props.onSubmit(e); }}
        >
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Agent ID */}
            {isEdit ? (
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-warm-600">Agent ID</label>
                <div className="input-field flex items-center bg-warm-50 text-warm-600 cursor-not-allowed select-none">
                  <svg className="h-4 w-4 mr-2 text-warm-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                  </svg>
                  {editingAgentId}
                </div>
              </div>
            ) : (
              <input
                className="input-field"
                placeholder="Agent ID (唯一标识)"
                value={f.agentId}
                onChange={(e) => setF((p) => ({ ...p, agentId: e.target.value }))}
                required
              />
            )}

            {/* 领域标签 */}
            <input
              className="input-field"
              placeholder="领域标签（如 backend, frontend, devops）"
              value={f.domain}
              onChange={(e) => setF((p) => ({ ...p, domain: e.target.value }))}
              required
            />

            {/* 适配器类型 */}
            <div className="flex flex-col gap-1">
              <select
                className="input-field"
                value={f.adapterType}
                onChange={(e) => props.onAdapterChange(e.target.value)}
              >
                <option value="">-- 请选择适配器类型 --</option>
                {props.adapterOptions.map((adapter) => (
                  <option key={adapter.id} value={adapter.id}>
                    {adapter.name}（{adapter.id}）
                  </option>
                ))}
              </select>
              {props.selectedAdapterInfo && (
                <div className="rounded bg-warm-50 px-3 py-2 text-xs text-warm-600">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-warm-700">{props.selectedAdapterInfo.name}</span>
                    <span className={`rounded px-1.5 py-0.5 text-[10px] ${
                      props.selectedAdapterInfo.category === 'mock' ? 'bg-green-100 text-green-700' :
                      props.selectedAdapterInfo.category === 'local' ? 'bg-blue-100 text-blue-700' :
                      props.selectedAdapterInfo.category === 'custom' ? 'bg-purple-100 text-purple-700' :
                      'bg-warm-100 text-warm-600'
                    }`}>
                      {props.selectedAdapterInfo.category === 'mock' ? '无需网络' :
                       props.selectedAdapterInfo.category === 'local' ? '本地部署' :
                       props.selectedAdapterInfo.category === 'custom' ? '自定义' : '云端服务'}
                    </span>
                  </div>
                  <p className="mt-1">{props.selectedAdapterInfo.description}</p>
                </div>
              )}
            </div>

            {/* 基座模型名称 */}
            <input
              className="input-field"
              placeholder="大模型基座名称"
              value={f.baseModelName}
              onChange={(e) => setF((p) => ({ ...p, baseModelName: e.target.value }))}
            />

            {/* 位次等级 */}
            <select
              className="input-field"
              value={f.rankLevel}
              onChange={(e) => setF((p) => ({ ...p, rankLevel: e.target.value }))}
            >
              <option value="L1">L1（一级位次）</option>
              <option value="L2">L2（二级位次）</option>
              <option value="L3">L3（三级位次）</option>
            </select>

            {/* API Base URL */}
            <input
              className="input-field"
              placeholder="API Base URL"
              value={f.baseUrl}
              onChange={(e) => setF((p) => ({ ...p, baseUrl: e.target.value }))}
            />

            {/* 展示名称 */}
            <input
              className="input-field"
              placeholder="展示名称"
              value={f.displayName}
              onChange={(e) => setF((p) => ({ ...p, displayName: e.target.value }))}
            />

            {/* 职责备注 */}
            <textarea
              className="input-field md:col-span-2"
              rows={2}
              placeholder="职责备注"
              value={f.dutyNote}
              onChange={(e) => setF((p) => ({ ...p, dutyNote: e.target.value }))}
            />

            {/* 头像 URL + 上传 */}
            <div className="flex items-end gap-2 md:col-span-2">
              <input
                className="input-field flex-1"
                placeholder="头像 URL（可选）"
                value={f.avatarUrl}
                onChange={(e) => setF((p) => ({ ...p, avatarUrl: e.target.value }))}
              />
              <label className="btn-secondary px-3 py-2 text-sm cursor-pointer whitespace-nowrap">
                上传头像
                <input
                  type="file"
                  accept="image/*"
                  className="hidden"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    const formData = new FormData();
                    formData.append('file', file);
                    if (f.agentId.trim()) formData.append('agentId', f.agentId.trim());
                    try {
                      const res = await fetch('/api/agent/registry/avatar', {
                        method: 'POST',
                        headers: props.authHeaders(),
                        body: formData,
                      });
                      const data = await res.json();
                      if (res.ok && data.avatarUrl) {
                        setF((p) => ({ ...p, avatarUrl: data.avatarUrl }));
                        props.setNotice(data.storedInDb ? '头像已上传至数据库' : '头像上传成功');
                      } else {
                        props.setNotice(props.fmtErr(data.detail, '上传失败'));
                      }
                    } catch { props.setNotice('上传失败，请检查网络'); }
                  }}
                />
              </label>
            </div>

            {/* 能力标签 */}
            <div className="md:col-span-2">
              <label className="mb-1 block text-sm font-medium text-warm-600">能力标签</label>
              <TagInput
                tags={f.capabilityTags}
                onChange={(tags) => setF((p) => ({ ...p, capabilityTags: tags }))}
                placeholder="输入标签后按 Enter 添加..."
                maxTags={8}
              />
            </div>

            {/* API Key */}
            <input
              className="input-field md:col-span-2"
              placeholder={isEdit ? 'API Key（留空则保持不变，输入新值将替换）' : 'API Key'}
              type="password"
              value={f.apiKey}
              onChange={(e) => setF((p) => ({ ...p, apiKey: e.target.value }))}
            />
          </div>

          {/* ── Footer actions ─────────────────────────────────── */}
          <div className="mt-6 flex justify-end gap-2 border-t border-warm-150 pt-4">
            <button type="button" className="btn-secondary" onClick={props.onClose}>
              取消
            </button>
            <button type="submit" className="btn-primary">
              {isEdit ? '保存修改' : '创建服务商'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
