'use client';

import { type JSX } from 'react';
import type { AgentTemplate } from '../../types';
import { useTemplateStore } from '../../stores/templateStore';

interface Props {
  template: AgentTemplate;
  onUse: () => void;
}

const CATEGORY_LABELS: Record<string, string> = {
  customer_service: '客服', devops: 'DevOps', data: '数据分析',
  knowledge: '知识库', api: 'API', content: '内容创作',
  productivity: '效率工具', security: '安全', hr: '人事',
  legal: '法务', education: '教育',
};

export function TemplateCard({ template, onUse }: Props): JSX.Element {
  const exportTemplate = useTemplateStore((s) => s.exportTemplate);
  const deleteTemplate = useTemplateStore((s) => s.deleteTemplate);

  const catLabel = CATEGORY_LABELS[template.category] || template.category;

  return (
    <div className="card p-4 hover:shadow-card-elevated transition-shadow group">
      {/* Header */}
      <div className="flex items-start gap-3 mb-2">
        <div className="shrink-0 w-10 h-10 rounded-xl bg-primary-50 flex items-center justify-center">
          <span className="material-symbols-outlined text-lg text-primary-500">{template.icon}</span>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h4 className="text-sm font-semibold text-warm-700 truncate">{template.name}</h4>
            {template.source === 'builtin' && (
              <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-primary-50 text-primary-600 shrink-0">内置</span>
            )}
          </div>
          <div className="flex items-center gap-2 text-[10px] text-warm-400 mt-0.5">
            <span>{catLabel}</span>
            <span>·</span>
            <span>v{template.version}</span>
            <span>·</span>
            <span>⭐ {(template.rating || 0).toFixed(1)}</span>
          </div>
        </div>
      </div>

      {/* Description */}
      <p className="text-xs text-warm-500 leading-relaxed mb-2 line-clamp-2">{template.description}</p>

      {/* Tags */}
      {template.tags.length > 0 && (
        <div className="flex items-center gap-1 flex-wrap mb-3">
          {template.tags.slice(0, 4).map((tag) => (
            <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-warm-100 text-warm-500">{tag}</span>
          ))}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between pt-2 border-t border-warm-100">
        <span className="text-[10px] text-warm-400">
          {template.usage_count > 0 ? `${template.usage_count} 次使用` : '新模板'}
        </span>
        <div className="flex items-center gap-1">
          {template.source !== 'builtin' && (
            <button
              className="btn-ghost text-[10px] px-1.5 py-0.5 text-danger-400 hover:text-danger-600"
              onClick={() => void deleteTemplate(template.id)}
            >
              <span className="material-symbols-outlined text-[12px]">delete</span>
            </button>
          )}
          <button
            className="btn-ghost text-[10px] px-1.5 py-0.5"
            onClick={() => exportTemplate(template)}
          >
            <span className="material-symbols-outlined text-[12px]">download</span>
          </button>
          <button
            className="btn-primary text-[10px] px-2 py-1"
            onClick={onUse}
          >
            使用模板
          </button>
        </div>
      </div>
    </div>
  );
}
