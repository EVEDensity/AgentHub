'use client';

import { useState, type JSX } from 'react';
import type {
  BranchCondition,
  ConditionRule,
  ConditionOperator,
} from '../../types';

// ── Operator metadata ────────────────────────────────────────────────

interface OperatorMeta {
  symbol: string;
  label: string;
  needsValue: boolean;
}

const OPERATOR_META: Record<ConditionOperator, OperatorMeta> = {
  eq:             { symbol: '=',  label: '等于',             needsValue: true },
  neq:            { symbol: '≠', label: '不等于',           needsValue: true },
  contains:       { symbol: '⊃', label: '包含',             needsValue: true },
  not_contains:   { symbol: '⊅', label: '不包含',           needsValue: true },
  gt:             { symbol: '>',  label: '大于',             needsValue: true },
  gte:            { symbol: '≥', label: '大于等于',         needsValue: true },
  lt:             { symbol: '<',  label: '小于',             needsValue: true },
  lte:            { symbol: '≤', label: '小于等于',         needsValue: true },
  regex:          { symbol: '.*', label: '正则匹配',         needsValue: true },
  exists:         { symbol: '∃',  label: '存在 (非空)',     needsValue: false },
  empty:          { symbol: '∅',  label: '为空',             needsValue: false },
};

const OPERATORS: ConditionOperator[] = [
  'eq', 'neq', 'contains', 'not_contains', 'gt', 'gte', 'lt', 'lte', 'regex', 'exists', 'empty',
];

// ── Helpers ──────────────────────────────────────────────────────────

function newRule(): ConditionRule {
  return {
    id: `rule-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    left: '',
    operator: 'eq',
    right: '',
  };
}

function newBranch(label: string): BranchCondition {
  return {
    id: `branch-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    label,
    rules: [newRule()],
    logic: 'AND',
  };
}

// ── Component Props ──────────────────────────────────────────────────

interface Props {
  branches: BranchCondition[];
  onChange: (branches: BranchCondition[]) => void;
  availableVariables?: string[];     // e.g., ["codegen.output", "http.result"]
  className?: string;
}

// ── Sub-component: Single Rule Row ───────────────────────────────────

function RuleRow({
  rule,
  onChange,
  onRemove,
  availableVariables,
  canRemove,
}: {
  rule: ConditionRule;
  onChange: (r: ConditionRule) => void;
  onRemove: () => void;
  availableVariables: string[];
  canRemove: boolean;
}): JSX.Element {
  const opMeta = OPERATOR_META[rule.operator];

  return (
    <div className="flex items-center gap-1.5 py-1.5">
      {/* Left operand */}
      <div className="flex-1 min-w-0">
        <input
          className="input-field text-[11px] h-8 w-full"
          placeholder="变量或字面量"
          value={rule.left}
          onChange={(e) => onChange({ ...rule, left: e.target.value })}
          list="var-list"
        />
      </div>

      {/* Operator */}
      <div className="relative shrink-0">
        <select
          className="input-field text-[11px] h-8 w-[72px] appearance-none pr-4"
          value={rule.operator}
          onChange={(e) => {
            const op = e.target.value as ConditionOperator;
            const meta = OPERATOR_META[op];
            onChange({
              ...rule,
              operator: op,
              right: meta.needsValue ? rule.right : '',
            });
          }}
        >
          {OPERATORS.map((op) => (
            <option key={op} value={op}>
              {OPERATOR_META[op].symbol} {OPERATOR_META[op].label}
            </option>
          ))}
        </select>
      </div>

      {/* Right operand (hidden for exists/empty) */}
      {opMeta.needsValue && (
        <div className="flex-1 min-w-0">
          <input
            className="input-field text-[11px] h-8 w-full"
            placeholder="期望值"
            value={rule.right}
            onChange={(e) => onChange({ ...rule, right: e.target.value })}
          />
        </div>
      )}

      {/* Remove */}
      <button
        className="shrink-0 rounded p-1 text-warm-300 hover:text-danger-500 hover:bg-danger-50 transition-colors"
        onClick={onRemove}
        disabled={!canRemove}
        title="移除此条件"
      >
        <span className="material-symbols-outlined text-[14px]">close</span>
      </button>
    </div>
  );
}

// ── Sub-component: Single Branch ─────────────────────────────────────

function BranchPanel({
  branch,
  index,
  total,
  onChange,
  onRemove,
  availableVariables,
}: {
  branch: BranchCondition;
  index: number;
  total: number;
  onChange: (b: BranchCondition) => void;
  onRemove: () => void;
  availableVariables: string[];
}): JSX.Element {
  return (
    <div className="rounded-xl border border-warm-150 bg-warm-50/40 overflow-hidden">
      {/* Branch header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-warm-100 border-b border-warm-100">
        <div className="flex items-center gap-2">
          <span
            className="inline-flex h-5 w-5 items-center justify-center rounded text-[10px] font-bold text-white"
            style={{ background: index === 0 ? '#22A06B' : '#D97706' }}
          >
            {index === 0 ? 'IF' : 'ELSE IF'}
          </span>
          <input
            className="text-xs font-medium text-warm-700 bg-transparent border-none outline-none w-24"
            placeholder="分支标签"
            value={branch.label}
            onChange={(e) => onChange({ ...branch, label: e.target.value })}
          />
        </div>
        <div className="flex items-center gap-1.5">
          {/* Logic toggle */}
          <button
            className={`text-[10px] px-2 py-0.5 rounded-full font-medium transition-colors ${
              branch.logic === 'AND'
                ? 'bg-primary-100 text-primary-700'
                : 'bg-warm-100 text-warm-500 hover:text-primary-600'
            }`}
            onClick={() => onChange({ ...branch, logic: branch.logic === 'AND' ? 'OR' : 'AND' })}
            title="切换 AND/OR 逻辑"
          >
            {branch.logic}
          </button>
          {total > 1 && (
            <button
              className="rounded p-0.5 text-warm-300 hover:text-danger-500 transition-colors"
              onClick={onRemove}
              title="移除此分支"
            >
              <span className="material-symbols-outlined text-[14px]">delete</span>
            </button>
          )}
        </div>
      </div>

      {/* Rules */}
      <div className="px-4 py-2 space-y-0">
        {branch.rules.map((rule, ri) => (
          <RuleRow
            key={rule.id}
            rule={rule}
            onChange={(r) => {
              const rules = [...branch.rules];
              rules[ri] = r;
              onChange({ ...branch, rules });
            }}
            onRemove={() => {
              if (branch.rules.length <= 1) return;
              onChange({ ...branch, rules: branch.rules.filter((_, i) => i !== ri) });
            }}
            availableVariables={availableVariables}
            canRemove={branch.rules.length > 1}
          />
        ))}
      </div>

      {/* Add rule */}
      <div className="px-4 pb-2.5">
        <button
          className="text-[10px] text-primary-500 hover:text-primary-700 transition-colors font-medium"
          onClick={() => onChange({ ...branch, rules: [...branch.rules, newRule()] })}
        >
          + 添加条件
        </button>
      </div>
    </div>
  );
}

// ── Main Component ───────────────────────────────────────────────────

export default function WorkflowConditionEditor({
  branches,
  onChange,
  availableVariables = [],
  className = '',
}: Props): JSX.Element {
  const [collapsed, setCollapsed] = useState(false);

  function addBranch(): void {
    onChange([...branches, newBranch(`分支 ${branches.length + 1}`)]);
  }

  function updateBranch(index: number, updated: BranchCondition): void {
    const next = [...branches];
    next[index] = updated;
    onChange(next);
  }

  function removeBranch(index: number): void {
    if (branches.length <= 1) return;
    onChange(branches.filter((_, i) => i !== index));
  }

  // Show available variables as a helper
  const hasVars = availableVariables.length > 0;

  return (
    <div className={`space-y-3 ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-[18px] text-warm-500">account_tree</span>
          <span className="text-sm font-semibold text-warm-700">条件编辑器</span>
          {branches.length > 0 && (
            <span className="text-[10px] text-warm-400">
              {branches.length} 个分支 · {branches.reduce((s, b) => s + b.rules.length, 0)} 条规则
            </span>
          )}
        </div>
        <button
          className="text-[10px] text-warm-400 hover:text-warm-600"
          onClick={() => setCollapsed(!collapsed)}
        >
          {collapsed ? '展开' : '收起'}
        </button>
      </div>

      {!collapsed && (
        <>
          {/* Available variables hint */}
          {hasVars && (
            <div className="rounded-lg bg-primary-50/50 border border-primary-100 px-3 py-2">
              <div className="text-[10px] font-medium text-primary-600 mb-1">可用变量</div>
              <div className="flex flex-wrap gap-1">
                {availableVariables.slice(0, 12).map((v) => (
                  <code
                    key={v}
                    className="inline-block rounded bg-warm-100 border border-primary-100 px-1.5 py-0.5 text-[10px] font-mono text-primary-700 cursor-pointer hover:bg-primary-100 transition-colors"
                    title={`点击复制 {{${v}}}`}
                    onClick={() => {
                      navigator.clipboard?.writeText(`{{${v}}}`);
                    }}
                  >
                    {`{{${v}}}`}
                  </code>
                ))}
                {availableVariables.length > 12 && (
                  <span className="text-[10px] text-warm-400">
                    +{availableVariables.length - 12} more
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Branches */}
          <div className="space-y-2">
            {branches.map((branch, i) => (
              <BranchPanel
                key={branch.id}
                branch={branch}
                index={i}
                total={branches.length}
                onChange={(b) => updateBranch(i, b)}
                onRemove={() => removeBranch(i)}
                availableVariables={availableVariables}
              />
            ))}
          </div>

          {/* ELSE fallback */}
          <div className="flex items-center gap-2 rounded-lg border border-dashed border-warm-200 bg-warm-50/30 px-4 py-2.5">
            <span className="inline-flex h-5 w-5 items-center justify-center rounded text-[10px] font-bold text-white bg-warm-400">
              ELSE
            </span>
            <span className="text-xs text-warm-400">
              以上条件均不满足时执行默认路径
            </span>
          </div>

          {/* Add branch */}
          <button
            className="text-xs text-primary-500 hover:text-primary-700 transition-colors font-medium"
            onClick={addBranch}
          >
            + 添加 ELSE IF 分支
          </button>
        </>
      )}

      {/* Hidden datalist for autocomplete */}
      <datalist id="var-list">
        {availableVariables.map((v) => (
          <option key={v} value={`{{${v}}}`} />
        ))}
      </datalist>
    </div>
  );
}
