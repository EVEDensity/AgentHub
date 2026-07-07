'use client';

import { useState, useMemo, useRef, useEffect, type JSX } from 'react';
import type { VariableReference } from '../../types';
import { extractVariables } from '../../lib/workflow/variableEngine';

// ── Props ─────────────────────────────────────────────────────────────

interface Props {
  /** The template text containing {{node_id.field}} references */
  template: string;
  onChange: (template: string) => void;
  /** Available variable keys, e.g. ["codegen.output", "http.status"] */
  availableVariables: string[];
  /** Human-readable labels for node IDs (for display grouping) */
  nodeLabels?: Record<string, string>;
  /** Placeholder text for the input */
  placeholder?: string;
  /** Label shown above the input */
  label?: string;
  /** Whether to show as textarea (multiline) */
  multiline?: boolean;
  /** Number of rows when multiline */
  rows?: number;
  className?: string;
}

// ── Color palette for variable chips ──────────────────────────────────

const NODE_COLORS = [
  '#4F6CF7', '#8B5CF6', '#22A06B', '#D97706',
  '#E8710A', '#0EA5E9', '#DC2626', '#0891B2',
  '#6366F1', '#EC4899', '#14B8A6', '#F59E0B',
];

function getNodeColor(nodeId: string): string {
  let hash = 0;
  for (let i = 0; i < nodeId.length; i++) {
    hash = nodeId.charCodeAt(i) + ((hash << 5) - hash);
  }
  return NODE_COLORS[Math.abs(hash) % NODE_COLORS.length];
}

// ── Component ─────────────────────────────────────────────────────────

export default function WorkflowVariablePicker({
  template,
  onChange,
  availableVariables,
  nodeLabels = {},
  placeholder = '输入内容，使用 {{node_id.field}} 引用变量...',
  label,
  multiline = false,
  rows = 3,
  className = '',
}: Props): JSX.Element {
  const [showPicker, setShowPicker] = useState(false);
  const [filterText, setFilterText] = useState('');
  const [cursorPos, setCursorPos] = useState<number | null>(null);
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement>(null);
  const pickerRef = useRef<HTMLDivElement>(null);

  // Extract current variables from template
  const extractedVars = useMemo(() => extractVariables(template), [template]);

  // Close picker on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent): void {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setShowPicker(false);
        setFilterText('');
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  // Filter available variables
  const filteredVars = useMemo(() => {
    if (!filterText) return availableVariables;
    const q = filterText.toLowerCase();
    return availableVariables.filter((v) => v.toLowerCase().includes(q));
  }, [availableVariables, filterText]);

  // Group variables by nodeId
  const groupedVars = useMemo(() => {
    const groups: Record<string, string[]> = {};
    for (const v of filteredVars) {
      const dotIdx = v.indexOf('.');
      const nodeId = dotIdx > 0 ? v.slice(0, dotIdx) : v;
      const field = dotIdx > 0 ? v.slice(dotIdx + 1) : '';
      if (!groups[nodeId]) groups[nodeId] = [];
      groups[nodeId].push(field);
    }
    return groups;
  }, [filteredVars]);

  // Insert variable at cursor position
  function insertVariable(varRef: string): void {
    const el = inputRef.current;
    if (!el) {
      onChange(`${template}{{${varRef}}}`);
      setShowPicker(false);
      setFilterText('');
      return;
    }

    const start = el.selectionStart ?? cursorPos ?? template.length;
    const end = el.selectionEnd ?? start;
    const before = template.slice(0, start);
    const after = template.slice(end);
    const insertion = `{{${varRef}}}`;

    const newTemplate = before + insertion + after;
    onChange(newTemplate);
    setShowPicker(false);
    setFilterText('');

    // Restore cursor after insertion
    requestAnimationFrame(() => {
      el.focus();
      const newPos = start + insertion.length;
      el.setSelectionRange(newPos, newPos);
    });
  }

  // Handle keyboard navigation in picker
  function handleKeyDown(e: React.KeyboardEvent): void {
    if (e.key === '{' && showPicker === false) {
      // Detect "{{" trigger
      const el = inputRef.current;
      if (el) {
        const pos = el.selectionStart ?? 0;
        if (template[pos - 1] === '{') {
          setCursorPos(pos + 1);
          setShowPicker(true);
        }
      }
    }

    if ((e.ctrlKey || e.metaKey) && e.key === ' ') {
      // Ctrl+Space to toggle picker
      e.preventDefault();
      const el = inputRef.current;
      if (el) setCursorPos(el.selectionStart);
      setShowPicker(true);
    }
  }

  // Render highlighted template preview
  function renderHighlightedPreview(): JSX.Element {
    if (!template) {
      return <span className="text-warm-300">{placeholder}</span>;
    }

    const parts: JSX.Element[] = [];
    let lastIndex = 0;

    for (const match of template.matchAll(/\{\{\s*([a-zA-Z_][a-zA-Z0-9_-]*)\.([a-zA-Z_][a-zA-Z0-9_.\[\]]*)\s*\}\}/g)) {
      // Text before match
      if (match.index! > lastIndex) {
        parts.push(
          <span key={`t-${lastIndex}`}>{template.slice(lastIndex, match.index)}</span>
        );
      }

      const nodeId = match[1];
      const color = getNodeColor(nodeId);

      parts.push(
        <span
          key={match.index}
          className="inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 mx-px text-[10px] font-mono font-medium"
          style={{
            background: `${color}14`,
            color: color,
            border: `1px solid ${color}30`,
          }}
        >
          <span className="text-[10px] opacity-60">{nodeId}.</span>
          <span>{match[2]}</span>
        </span>
      );

      lastIndex = match.index! + match[0].length;
    }

    // Remaining text
    if (lastIndex < template.length) {
      parts.push(<span key={`t-${lastIndex}`}>{template.slice(lastIndex)}</span>);
    }

    return <div className="whitespace-pre-wrap break-words text-xs text-warm-700">{parts}</div>;
  }

  // ── Render ─────────────────────────────────────────────────────────

  const Tag = multiline ? 'textarea' : 'input';

  return (
    <div className={`space-y-2 ${className}`}>
      {/* Label */}
      {label && (
        <label className="block text-[11px] font-medium text-warm-400 uppercase tracking-wide">
          {label}
        </label>
      )}

      {/* Input area */}
      <div className="relative">
        {multiline ? (
          <textarea
            ref={inputRef as React.RefObject<HTMLTextAreaElement>}
            className="input-field min-h-[80px] resize-y text-sm font-mono"
            rows={rows}
            placeholder={placeholder}
            value={template}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
          />
        ) : (
          <input
            ref={inputRef as React.RefObject<HTMLInputElement>}
            className="input-field text-sm font-mono"
            placeholder={placeholder}
            value={template}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
          />
        )}

        {/* Ctrl+Space trigger hint */}
        <button
          className="absolute right-2 bottom-2 rounded-md bg-warm-100 px-1.5 py-0.5 text-[10px] text-warm-500 hover:bg-warm-200 transition-colors"
          onClick={() => {
            const el = inputRef.current;
            if (el) setCursorPos(el.selectionStart);
            setShowPicker(!showPicker);
          }}
          title="Ctrl+Space 打开变量选择器"
        >
          <span className="font-mono text-[10px]">{'{ }'}</span>
        </button>

        {/* Variable Picker Dropdown */}
        {showPicker && (
          <div
            ref={pickerRef}
            className="absolute left-0 right-0 top-full mt-1 z-50 max-h-[260px] overflow-hidden rounded-xl border border-warm-200 bg-warm-100 shadow-card-hover shadow-black/5"
          >
            {/* Search */}
            <div className="border-b border-warm-100 px-2 py-1.5">
              <input
                className="w-full rounded-lg border border-warm-150 bg-warm-50/50 px-2.5 py-1.5 text-[11px] text-warm-700 placeholder:text-warm-400 focus:outline-none focus:ring-1 focus:ring-primary-300"
                placeholder="搜索变量..."
                value={filterText}
                onChange={(e) => setFilterText(e.target.value)}
                autoFocus
              />
            </div>

            {/* Variable list grouped by node */}
            <div className="overflow-y-auto max-h-[220px]">
              {Object.keys(groupedVars).length === 0 ? (
                <div className="px-4 py-6 text-center text-[11px] text-warm-400">
                  {availableVariables.length === 0
                    ? '暂无可用变量（请先在画布中定义上游节点输出）'
                    : '无匹配变量'}
                </div>
              ) : (
                Object.entries(groupedVars).map(([nodeId, fields]) => {
                  const color = getNodeColor(nodeId);
                  const label = nodeLabels[nodeId] || nodeId;
                  return (
                    <div key={nodeId} className="border-b border-warm-50 last:border-b-0">
                      {/* Node group header */}
                      <div className="flex items-center gap-1.5 px-3 py-1.5">
                        <span
                          className="h-1.5 w-1.5 rounded-full shrink-0"
                          style={{ background: color }}
                        />
                        <span className="text-[10px] font-medium text-warm-500">{label}</span>
                      </div>
                      {/* Fields */}
                      {fields.filter(Boolean).map((field) => {
                        const fullRef = `${nodeId}.${field}`;
                        return (
                          <button
                            key={fullRef}
                            className="w-full flex items-center gap-2 px-5 py-1.5 text-left text-[11px] text-warm-700 hover:bg-primary-50/50 transition-colors"
                            onClick={() => insertVariable(fullRef)}
                          >
                            <span className="font-mono text-[10px] text-primary-500">{field}</span>
                            <span className="text-[10px] text-warm-300 ml-auto font-mono">
                              {`{{${fullRef}}}`}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}
      </div>

      {/* Live preview with highlighted variables */}
      <div className="rounded-lg border border-warm-100 bg-warm-50/50 px-3 py-2 min-h-[32px]">
        {renderHighlightedPreview()}
      </div>

      {/* Variable count + quick insert chips */}
      {extractedVars.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] text-warm-400 shrink-0">
            {extractedVars.length} 个变量引用:
          </span>
          {extractedVars.map((v) => {
            const color = getNodeColor(v.nodeId);
            return (
              <button
                key={v.raw}
                className="inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[10px] font-mono font-medium border transition-colors hover:shadow-sm"
                style={{
                  background: `${color}0D`,
                  color: color,
                  borderColor: `${color}25`,
                }}
                onClick={() => {
                  // Remove this variable from template
                  onChange(template.replace(v.raw, '').replace(/\s{2,}/g, ' ').trim());
                }}
                title="点击移除此变量引用"
              >
                {v.nodeId}.{v.field}
                <span className="material-symbols-outlined text-[10px] ml-0.5">close</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export { extractVariables };
