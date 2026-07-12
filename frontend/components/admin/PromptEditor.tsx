'use client';

import { useState, useMemo, type JSX } from 'react';
import { PromptVariablePicker } from './PromptVariablePicker';
import { PromptLivePreview } from './PromptLivePreview';

export interface PromptBlockData {
  type: 'system' | 'user' | 'assistant';
  content: string;
}

interface Props {
  blocks: PromptBlockData[];
  onChange: (blocks: PromptBlockData[]) => void;
  variables: Record<string, string>;
  onVariablesChange: (vars: Record<string, string>) => void;
}

const BUILTIN_VARIABLES = [
  { key: 'user_name', description: '当前用户名' },
  { key: 'session_context', description: '会话上下文摘要' },
  { key: 'knowledge_snippets', description: '知识库检索结果' },
  { key: 'current_time', description: '当前时间' },
  { key: 'agent_id', description: '当前 Agent ID' },
  { key: 'workspace_name', description: '当前工作空间名称' },
  { key: 'user_message', description: '用户最新消息' },
];

const TAB_LABELS: Record<string, string> = {
  system: 'System Prompt',
  user: 'User Prompt',
  assistant: 'Assistant Prefix',
};

export function PromptEditor({ blocks, onChange, variables, onVariablesChange }: Props): JSX.Element {
  const [activeTab, setActiveTab] = useState<'system' | 'user' | 'assistant'>('system');
  const [showPreview, setShowPreview] = useState(false);

  const activeBlock = blocks.find((b) => b.type === activeTab) || blocks[0];

  const updateBlock = (content: string) => {
    onChange(
      blocks.map((b) => (b.type === activeTab ? { ...b, content } : b))
    );
  };

  // Detect variables in content
  const detectedVars = useMemo(() => {
    const regex = /\{\{(\w+)\}\}/g;
    const vars = new Set<string>();
    let match: RegExpExecArray | null;
    while ((match = regex.exec(activeBlock.content)) !== null) {
      if (!BUILTIN_VARIABLES.find((bv) => bv.key === match![1])) {
        vars.add(match![1]);
      }
    }
    return [...vars];
  }, [activeBlock.content]);

  // Render content with highlighted variables
  const renderHighlighted = (text: string) => {
    const parts = text.split(/(\{\{(?:\w+)\}\})/g);
    return parts.map((part, i) => {
      const m = part.match(/^\{\{(\w+)\}\}$/);
      if (m) {
        const key = m[1];
        const isBuiltin = BUILTIN_VARIABLES.some((bv) => bv.key === key);
        return (
          <span
            key={i}
            className={`inline-flex items-center px-1 py-0.5 rounded text-[11px] font-medium ${
              isBuiltin
                ? 'bg-primary-100 text-primary-700'
                : 'bg-warning-100 text-warning-700'
            }`}
            title={isBuiltin ? '内置变量' : '自定义变量'}
          >
            {part}
          </span>
        );
      }
      return <span key={i}>{part}</span>;
    });
  };

  return (
    <div className="space-y-3">
      {/* Tab bar */}
      <div className="flex items-center gap-1 border-b border-warm-100 pb-2">
        {(['system', 'user', 'assistant'] as const).map((tab) => (
          <button
            key={tab}
            className={`text-xs px-3 py-1 rounded-t transition-colors ${
              activeTab === tab
                ? 'text-primary-600 font-medium border-b-2 border-primary-500 -mb-[6px] bg-transparent'
                : 'text-warm-400 hover:text-warm-600'
            }`}
            onClick={() => setActiveTab(tab)}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
        <div className="flex-1" />
        <button
          className={`btn-ghost text-[10px] ${showPreview ? 'text-primary-500' : ''}`}
          onClick={() => setShowPreview(!showPreview)}
        >
          <span className="material-symbols-outlined text-[12px]">
            {showPreview ? 'edit' : 'preview'}
          </span>
          {showPreview ? '编辑' : '预览'}
        </button>
      </div>

      {showPreview ? (
        <PromptLivePreview blocks={blocks} variables={variables} />
      ) : (
        <div className="grid grid-cols-[1fr_200px] gap-3">
          {/* Editor */}
          <div className="space-y-3">
            <div className="relative">
              <textarea
                className="input-field w-full text-xs font-mono leading-relaxed"
                rows={12}
                placeholder={`输入 ${TAB_LABELS[activeTab]}...\n使用 {{variable}} 语法插入变量`}
                value={activeBlock.content}
                onChange={(e) => updateBlock(e.target.value)}
              />
            </div>

            {/* Variable highlighting overlay */}
            {activeBlock.content && (
              <div className="text-xs leading-relaxed whitespace-pre-wrap p-2 border border-warm-100 rounded-lg bg-warm-50/50 min-h-[4rem]">
                <div className="text-[10px] text-warm-400 mb-1">渲染预览:</div>
                {renderHighlighted(activeBlock.content)}
              </div>
            )}

            {/* Detected custom variables */}
            {detectedVars.length > 0 && (
              <div className="flex items-center gap-2 text-[10px] text-warning-500">
                <span className="material-symbols-outlined text-[12px]">warning</span>
                检测到未定义的自定义变量: {detectedVars.join(', ')}
                <button
                  className="text-primary-500 hover:underline"
                  onClick={() => {
                    const newVars = { ...variables };
                    for (const v of detectedVars) {
                      if (!newVars[v]) newVars[v] = '';
                    }
                    onVariablesChange(newVars);
                  }}
                >
                  自动添加
                </button>
              </div>
            )}
          </div>

          {/* Variable picker sidebar */}
          <div>
            <PromptVariablePicker
              onInsert={(key) => updateBlock(activeBlock.content + ` {{${key}}}`)}
              builtinVars={BUILTIN_VARIABLES}
              customVars={variables}
              onCustomVarAdd={(k, v) => onVariablesChange({ ...variables, [k]: v })}
              onCustomVarRemove={(k) => {
                const newVars = { ...variables };
                delete newVars[k];
                onVariablesChange(newVars);
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
