'use client';

import { useState, useMemo, type JSX } from 'react';
import type { PromptBlockData } from './PromptEditor';

interface Props {
  blocks: PromptBlockData[];
  variables: Record<string, string>;
}

const SAMPLE_DATA: Record<string, string> = {
  user_name: '张三',
  session_context: '当前正在讨论微服务架构迁移方案',
  knowledge_snippets: '微服务架构最佳实践: 使用事件驱动解耦...',
  current_time: new Date().toLocaleString('zh-CN'),
  agent_id: 'code-reviewer-01',
  workspace_name: '核心项目组',
  user_message: '请帮我审查这段代码的安全漏洞',
};

export function PromptLivePreview({ blocks, variables }: Props): JSX.Element {
  const [useCustomSample, setUseCustomSample] = useState(false);
  const [sampleOverrides, setSampleOverrides] = useState<Record<string, string>>({});

  const mergedVars = useMemo(() => {
    return { ...SAMPLE_DATA, ...variables, ...(useCustomSample ? sampleOverrides : {}) };
  }, [variables, useCustomSample, sampleOverrides]);

  const substitute = (text: string): string => {
    return text.replace(/\{\{(\w+)\}\}/g, (_, key: string) => {
      return mergedVars[key] !== undefined ? mergedVars[key] : `{{${key}}}`;
    });
  };

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-1 text-[10px] text-warm-500 cursor-pointer">
          <input
            type="checkbox"
            className="rounded"
            checked={useCustomSample}
            onChange={(e) => setUseCustomSample(e.target.checked)}
          />
          使用自定义样本数据
        </label>
      </div>

      {/* Preview blocks */}
      <div className="space-y-3">
        {blocks.map((block) => {
          const rendered = substitute(block.content);
          if (!block.content.trim()) return null;

          return (
            <div key={block.type} className="border border-warm-100 rounded-lg overflow-hidden">
              <div className="px-3 py-1.5 bg-warm-50/50 border-b border-warm-100">
                <span className="text-[10px] font-semibold text-warm-500 uppercase">
                  {block.type === 'system' ? 'System Prompt' :
                   block.type === 'user' ? 'User Prompt' : 'Assistant Prefix'}
                </span>
              </div>
              <div className="px-3 py-2">
                <div className="text-xs text-warm-600 leading-relaxed whitespace-pre-wrap font-mono">
                  {rendered}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Custom sample data editor */}
      {useCustomSample && (
        <div className="border border-warning-200 rounded-lg p-3 bg-warning-50/30">
          <h5 className="text-[10px] font-semibold text-warning-600 mb-2">自定义样本数据</h5>
          <div className="space-y-2">
            {Object.keys(mergedVars).map((key) => (
              <div key={key} className="flex items-center gap-2">
                <span className="text-[10px] text-warm-500 w-32 shrink-0 truncate">{key}</span>
                <input
                  className="input-field flex-1 text-[10px] py-1"
                  value={sampleOverrides[key] || ''}
                  placeholder={SAMPLE_DATA[key] || ''}
                  onChange={(e) => setSampleOverrides((prev) => ({ ...prev, [key]: e.target.value }))}
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Empty state */}
      {blocks.every((b) => !b.content.trim()) && (
        <div className="text-center py-8 text-xs text-warm-400">
          先在编辑器中输入 Prompt 内容，然后点击预览
        </div>
      )}
    </div>
  );
}
