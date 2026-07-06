'use client';

import { useState, type JSX, type FormEvent } from 'react';
import type { AgentTemplate } from '../../types';
import { useTemplateStore } from '../../stores/templateStore';

interface Props {
  template: AgentTemplate;
  onClose: () => void;
}

export function TemplateCreateModal({ template, onClose }: Props): JSX.Element {
  const createAgentFromTemplate = useTemplateStore((s) => s.createAgentFromTemplate);

  const [agentId, setAgentId] = useState(`${template.id}-${Date.now().toString(36)}`);
  const [domain, setDomain] = useState('general');
  const [creating, setCreating] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setCreating(true);
    const ok = await createAgentFromTemplate(template, agentId, domain);
    setCreating(false);
    if (ok) onClose();
  };

  // Parse config for preview
  let config: Record<string, unknown> = {};
  if (typeof template.agent_config === 'string') {
    try { config = JSON.parse(template.agent_config); } catch { /* ignore */ }
  } else {
    config = template.agent_config as Record<string, unknown>;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="card w-full max-w-lg shadow-2xl max-h-[85vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-warm-700">
            从模板创建 Agent
          </h3>
          <button className="text-warm-400 hover:text-warm-600" onClick={onClose}>
            <span className="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>

        {/* Template preview */}
        <div className="bg-warm-50 rounded-lg p-3 mb-4">
          <div className="flex items-center gap-2 mb-1">
            <span className="material-symbols-outlined text-[16px] text-primary-500">{template.icon}</span>
            <span className="text-xs font-medium text-warm-700">{template.name}</span>
          </div>
          <p className="text-xs text-warm-500">{template.description}</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="text-xs text-warm-500 block mb-1">Agent ID</label>
            <input
              className="input-field w-full text-sm"
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
              required
            />
          </div>
          <div>
            <label className="text-xs text-warm-500 block mb-1">领域标签</label>
            <input
              className="input-field w-full text-sm"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              placeholder="例如: backend, frontend, general"
            />
          </div>

          {/* Config preview */}
          <div className="text-xs text-warm-400">
            <span className="mr-1">适配器:</span>
            <span className="text-warm-600 font-medium">{String(config.adapterType || 'deepseek')}</span>
            <span className="mx-2">·</span>
            <span className="mr-1">模型:</span>
            <span className="text-warm-600 font-medium">{String(config.baseModelName || 'deepseek-chat')}</span>
          </div>

          {template.tags.length > 0 && (
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-[10px] text-warm-400">标签:</span>
              {template.tags.map((tag) => (
                <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-warm-100 text-warm-500">{tag}</span>
              ))}
            </div>
          )}

          {/* Footer */}
          <div className="flex items-center justify-end gap-2 pt-3 border-t border-warm-100">
            <button type="button" className="btn-ghost text-xs" onClick={onClose} disabled={creating}>
              取消
            </button>
            <button type="submit" className="btn-primary text-xs" disabled={creating}>
              {creating ? (
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white inline-block" />
              ) : (
                <span className="material-symbols-outlined text-[14px]">add</span>
              )}
              创建 Agent
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
