'use client';

import { useEffect, useState, type JSX } from 'react';
import { useTemplateStore } from '../../stores/templateStore';
import { TEMPLATE_CATEGORIES } from '../../data/presetTemplates';
import { TemplateCard } from './TemplateCard';
import { TemplateCreateModal } from './TemplateCreateModal';
import { TemplateImportModal } from './TemplateImportModal';

interface Props {
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
}

export default function TemplateMarketplace({ authHeaders: _authHeaders, setNotice: _setNotice }: Props): JSX.Element {
  const templates = useTemplateStore((s) => s.templates);
  const activeCategory = useTemplateStore((s) => s.activeCategory);
  const searchKeyword = useTemplateStore((s) => s.searchKeyword);
  const loading = useTemplateStore((s) => s.loading);
  const isImportModalOpen = useTemplateStore((s) => s.isImportModalOpen);
  const isCreateModalOpen = useTemplateStore((s) => s.isCreateModalOpen);
  const selectedTemplate = useTemplateStore((s) => s.selectedTemplate);

  const loadTemplates = useTemplateStore((s) => s.loadTemplates);
  const setActiveCategory = useTemplateStore((s) => s.setActiveCategory);
  const setSearchKeyword = useTemplateStore((s) => s.setSearchKeyword);
  const setIsImportModalOpen = useTemplateStore((s) => s.setIsImportModalOpen);
  const setIsCreateModalOpen = useTemplateStore((s) => s.setIsCreateModalOpen);
  const setSelectedTemplate = useTemplateStore((s) => s.setSelectedTemplate);
  const getFilteredTemplates = useTemplateStore((s) => s.getFilteredTemplates);

  const [debouncedKeyword, setDebouncedKeyword] = useState('');

  useEffect(() => {
    void loadTemplates();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Debounce search
  useEffect(() => {
    const timer = setTimeout(() => setSearchKeyword(debouncedKeyword), 300);
    return () => clearTimeout(timer);
  }, [debouncedKeyword]); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = getFilteredTemplates();
  const builtinCount = templates.filter((t) => t.source === 'builtin').length;
  const userCount = templates.filter((t) => t.source === 'user').length;

  return (
    <div className="flex flex-col h-full gap-3">
      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <h3 className="text-sm font-semibold text-warm-700">
          模板市场
          <span className="ml-2 text-xs font-normal text-warm-400">
            {builtinCount} 内置 · {userCount} 自定义
          </span>
        </h3>
        <div className="flex-1" />
        {/* Search */}
        <div className="relative">
          <span className="material-symbols-outlined absolute left-2 top-1/2 -translate-y-1/2 text-[14px] text-warm-400">search</span>
          <input
            className="input-field text-xs pl-7 py-1.5 w-48"
            placeholder="搜索模板..."
            value={debouncedKeyword}
            onChange={(e) => setDebouncedKeyword(e.target.value)}
          />
        </div>
        <button className="btn-secondary text-xs" onClick={() => setIsImportModalOpen(true)}>
          <span className="material-symbols-outlined text-[14px]">file_upload</span>
          导入
        </button>
      </div>

      {/* Category tabs */}
      <div className="flex items-center gap-1 overflow-x-auto pb-1">
        {TEMPLATE_CATEGORIES.map((cat) => (
          <button
            key={cat.key}
            className={`text-xs px-3 py-1 rounded-full whitespace-nowrap transition-colors ${
              activeCategory === cat.key
                ? 'bg-primary-500 text-white'
                : 'bg-warm-100 text-warm-600 hover:bg-warm-200'
            }`}
            onClick={() => setActiveCategory(cat.key)}
          >
            <span className="material-symbols-outlined text-[12px] align-text-bottom mr-0.5">{cat.icon}</span>
            {cat.label}
          </button>
        ))}
      </div>

      {/* Template grid */}
      {loading ? (
        <div className="flex justify-center py-12">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-12 text-xs text-warm-400">
          <span className="material-symbols-outlined text-3xl mb-2 block">category</span>
          {searchKeyword ? '未找到匹配的模板' : '暂无模板'}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 overflow-y-auto">
          {filtered.map((tpl) => (
            <TemplateCard
              key={tpl.id}
              template={tpl}
              onUse={() => { setSelectedTemplate(tpl); setIsCreateModalOpen(true); }}
            />
          ))}
        </div>
      )}

      {/* Modals */}
      {isCreateModalOpen && selectedTemplate && (
        <TemplateCreateModal
          template={selectedTemplate}
          onClose={() => { setIsCreateModalOpen(false); setSelectedTemplate(null); }}
        />
      )}
      {isImportModalOpen && (
        <TemplateImportModal
          onClose={() => setIsImportModalOpen(false)}
        />
      )}
    </div>
  );
}
