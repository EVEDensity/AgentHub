'use client';

import { useState, type JSX } from 'react';

// ── Types ─────────────────────────────────────────────────────────

export interface WorkflowVariable {
  name: string;
  value: string;
  source: 'input' | 'node_output' | 'constant' | 'env';
  nodeId?: string;
}

export type ConditionOperator = 'eq' | 'neq' | 'gt' | 'gte' | 'lt' | 'lte' | 'contains' | 'regex' | 'exists' | 'empty';

export interface ConditionRule {
  id: string;
  leftOperand: string;   // {{variable}} reference
  operator: ConditionOperator;
  rightOperand: string;  // value or {{variable}}
}

interface NodeConfig {
  // Code node
  code?: string;
  language?: 'python' | 'javascript';
  timeout?: number;
  // HTTP node
  url?: string;
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  headers?: Record<string, string>;
  body?: string;
  // Knowledge node
  collectionName?: string;
  queryTemplate?: string;
  topK?: number;
  minScore?: number;
  // Condition
  rules?: ConditionRule[];
  trueBranch?: string;
  falseBranch?: string;
}

interface Props {
  nodeType: 'code' | 'http' | 'knowledge' | 'condition';
  config: NodeConfig;
  onChange: (config: NodeConfig) => void;
  variables: WorkflowVariable[];
  onVariablesChange: (vars: WorkflowVariable[]) => void;
}

// ── Operators ─────────────────────────────────────────────────────

const OPERATORS: { value: ConditionOperator; label: string }[] = [
  { value: 'eq', label: '等于 ==' },
  { value: 'neq', label: '不等于 !=' },
  { value: 'gt', label: '大于 >' },
  { value: 'gte', label: '大于等于 >=' },
  { value: 'lt', label: '小于 <' },
  { value: 'lte', label: '小于等于 <=' },
  { value: 'contains', label: '包含 contains' },
  { value: 'regex', label: '正则匹配 regex' },
  { value: 'exists', label: '存在 exists' },
  { value: 'empty', label: '为空 empty' },
];

// ── Sub-editors ───────────────────────────────────────────────────

function CodeNodeEditor({ config, onChange }: { config: NodeConfig; onChange: (c: NodeConfig) => void }): JSX.Element {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <label className="text-xs font-medium text-warm-600 w-20 shrink-0">语言</label>
        <select
          className="input-field text-xs flex-1"
          value={config.language || 'python'}
          onChange={(e) => onChange({ ...config, language: e.target.value as 'python' | 'javascript' })}
        >
          <option value="python">Python 3</option>
          <option value="javascript">JavaScript</option>
        </select>
        <label className="text-xs font-medium text-warm-600 w-20 shrink-0">超时(秒)</label>
        <input
          className="input-field text-xs w-20"
          type="number"
          min={1}
          max={60}
          value={config.timeout || 30}
          onChange={(e) => onChange({ ...config, timeout: parseInt(e.target.value) || 30 })}
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-warm-600 mb-1">
          代码 <span className="text-warm-400 font-normal">（使用 <code className="text-[10px] bg-warm-100 px-1 rounded">print()</code> 或 <code className="text-[10px] bg-warm-100 px-1 rounded">return</code> 输出结果）</span>
        </label>
        <textarea
          className="input-field w-full text-xs font-mono leading-relaxed"
          rows={10}
          spellCheck={false}
          placeholder={config.language === 'python'
            ? '# 示例：处理输入数据\nimport json\ndata = json.loads(input_data)\nresult = [item["name"] for item in data if item["score"] > 80]\nprint(json.dumps(result))'
            : '// 示例：处理输入数据\nconst data = JSON.parse(inputData);\nconst result = data.filter(item => item.score > 80).map(item => item.name);\nconsole.log(JSON.stringify(result));'
          }
          value={config.code || ''}
          onChange={(e) => onChange({ ...config, code: e.target.value })}
        />
      </div>
      <div className="rounded-lg bg-blue-50 border border-blue-200 px-3 py-2 text-[10px] text-blue-700">
        <span className="font-medium">可用的上下文变量：</span>
        <code className="ml-2 bg-blue-100 px-1 rounded">input_data</code> 上游节点输出 ·
        <code className="ml-2 bg-blue-100 px-1 rounded">node_outputs</code> 所有节点输出字典 ·
        <code className="ml-2 bg-blue-100 px-1 rounded">context</code> 工作流上下文
      </div>
    </div>
  );
}

function HttpNodeEditor({ config, onChange }: { config: NodeConfig; onChange: (c: NodeConfig) => void }): JSX.Element {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <select
          className="input-field text-xs w-24 shrink-0"
          value={config.method || 'GET'}
          onChange={(e) => onChange({ ...config, method: e.target.value as NodeConfig['method'] })}
        >
          <option value="GET">GET</option>
          <option value="POST">POST</option>
          <option value="PUT">PUT</option>
          <option value="DELETE">DELETE</option>
          <option value="PATCH">PATCH</option>
        </select>
        <input
          className="input-field text-xs flex-1 font-mono"
          placeholder="https://api.example.com/endpoint"
          value={config.url || ''}
          onChange={(e) => onChange({ ...config, url: e.target.value })}
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-warm-600 mb-1">Headers（JSON 对象，每行一个键值对）</label>
        <textarea
          className="input-field w-full text-xs font-mono"
          rows={3}
          placeholder='{"Authorization": "Bearer {{token}}", "Content-Type": "application/json"}'
          value={config.body && config.headers ? JSON.stringify(config.headers, null, 2) : ''}
          onChange={(e) => {
            try {
              const headers = JSON.parse(e.target.value);
              onChange({ ...config, headers });
            } catch { /* allow partial JSON while typing */ }
          }}
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-warm-600 mb-1">Body（JSON，支持 `&#123;&#123;variable&#125;&#125;` 模板）</label>
        <textarea
          className="input-field w-full text-xs font-mono"
          rows={4}
          placeholder='{"query": "{{search_query}}", "limit": 10}'
          value={config.body || ''}
          onChange={(e) => onChange({ ...config, body: e.target.value })}
        />
      </div>
      <div className="flex gap-3 text-[10px] text-warm-400">
        <span>支持变量引用: <code className="bg-warm-100 px-1 rounded">{'{{node_id.result}}'}</code></span>
        <span>超时: 30s</span>
        <span>自动跟随重定向</span>
      </div>
    </div>
  );
}

function KnowledgeNodeEditor({ config, onChange }: { config: NodeConfig; onChange: (c: NodeConfig) => void }): JSX.Element {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <label className="text-xs font-medium text-warm-600 w-24 shrink-0">知识库集合</label>
        <input
          className="input-field text-xs flex-1"
          placeholder="my-collection"
          value={config.collectionName || ''}
          onChange={(e) => onChange({ ...config, collectionName: e.target.value })}
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-warm-600 mb-1">
          查询模板 <span className="text-warm-400 font-normal">（支持 `&#123;&#123;variable&#125;&#125;` 模板）</span>
        </label>
        <textarea
          className="input-field w-full text-xs font-mono"
          rows={3}
          placeholder='{{user_message}} 的相关知识'
          value={config.queryTemplate || ''}
          onChange={(e) => onChange({ ...config, queryTemplate: e.target.value })}
        />
      </div>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <label className="text-xs font-medium text-warm-600">Top-K</label>
          <input
            className="input-field text-xs w-20"
            type="number"
            min={1}
            max={20}
            value={config.topK || 5}
            onChange={(e) => onChange({ ...config, topK: parseInt(e.target.value) || 5 })}
          />
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs font-medium text-warm-600">最低相似度</label>
          <input
            className="input-field text-xs w-20"
            type="number"
            min={0}
            max={1}
            step={0.01}
            value={config.minScore || 0.5}
            onChange={(e) => onChange({ ...config, minScore: parseFloat(e.target.value) || 0.5 })}
          />
        </div>
      </div>
      <div className="rounded-lg bg-green-50 border border-green-200 px-3 py-2 text-[10px] text-green-700">
        <span className="font-medium">检索结果注入：</span> 匹配的文档片段将自动注入到下游 Agent 的上下文中，格式为带 citation 标签的结构化文本。
      </div>
    </div>
  );
}

function ConditionEditor({ config, onChange, variables }: { config: NodeConfig; onChange: (c: NodeConfig) => void; variables: WorkflowVariable[] }): JSX.Element {
  const rules = config.rules || [];

  const addRule = () => {
    const newRule: ConditionRule = {
      id: 'rule-' + Date.now(),
      leftOperand: '',
      operator: 'eq',
      rightOperand: '',
    };
    onChange({ ...config, rules: [...rules, newRule] });
  };

  const updateRule = (id: string, updates: Partial<ConditionRule>) => {
    onChange({
      ...config,
      rules: rules.map((r) => (r.id === id ? { ...r, ...updates } : r)),
    });
  };

  const removeRule = (id: string) => {
    onChange({ ...config, rules: rules.filter((r) => r.id !== id) });
  };

  return (
    <div className="space-y-3">
      {/* Branch targets */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 flex-1">
          <label className="text-xs font-medium text-green-600 w-16 shrink-0">True →</label>
          <input
            className="input-field text-xs flex-1"
            placeholder="节点ID（满足条件后执行）"
            value={config.trueBranch || ''}
            onChange={(e) => onChange({ ...config, trueBranch: e.target.value })}
          />
        </div>
        <div className="flex items-center gap-2 flex-1">
          <label className="text-xs font-medium text-red-500 w-16 shrink-0">False →</label>
          <input
            className="input-field text-xs flex-1"
            placeholder="节点ID（不满足时执行）"
            value={config.falseBranch || ''}
            onChange={(e) => onChange({ ...config, falseBranch: e.target.value })}
          />
        </div>
      </div>

      {/* Rules */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-xs font-semibold text-warm-700">条件规则</label>
          <button className="text-[10px] text-primary-500 hover:underline" onClick={addRule}>
            + 添加规则
          </button>
        </div>
        {rules.map((rule) => (
          <div key={rule.id} className="flex items-center gap-2 bg-warm-50 rounded-lg px-3 py-2">
            <input
              className="input-field text-xs flex-1 font-mono"
              placeholder="{{variable}}"
              value={rule.leftOperand}
              onChange={(e) => updateRule(rule.id, { leftOperand: e.target.value })}
            />
            <select
              className="input-field text-xs w-24 shrink-0"
              value={rule.operator}
              onChange={(e) => updateRule(rule.id, { operator: e.target.value as ConditionOperator })}
            >
              {OPERATORS.map((op) => (
                <option key={op.value} value={op.value}>{op.label}</option>
              ))}
            </select>
            <input
              className="input-field text-xs flex-1 font-mono"
              placeholder="值 或 {{variable}}"
              value={rule.rightOperand}
              onChange={(e) => updateRule(rule.id, { rightOperand: e.target.value })}
            />
            <button
              className="text-[10px] text-red-400 hover:text-red-600 shrink-0"
              onClick={() => removeRule(rule.id)}
            >
              ✕
            </button>
          </div>
        ))}
        {rules.length === 0 && (
          <div className="text-[10px] text-warm-400 text-center py-3 bg-warm-50 rounded-lg">
            暂无规则 — 点击"添加规则"开始
          </div>
        )}
      </div>

      {/* Available variables reference */}
      {variables.length > 0 && (
        <div className="rounded-lg border border-warm-150 px-3 py-2">
          <p className="text-[10px] font-medium text-warm-500 mb-1">可用变量（点击插入）：</p>
          <div className="flex flex-wrap gap-1">
            {variables.map((v) => (
              <button
                key={v.name}
                className="text-[10px] px-1.5 py-0.5 rounded bg-warm-100 text-warm-600 hover:bg-primary-100 hover:text-primary-700 transition-colors"
                onClick={() => {
                  // Insert into the last focused rule? Simplified: copy to clipboard hint
                }}
                title={`来源: ${v.source}${v.nodeId ? ' · 节点: ' + v.nodeId : ''}`}
              >
                {`{{${v.name}}}`}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Variable Context Editor ────────────────────────────────────────

function VariableContextEditor({
  variables,
  onChange,
}: {
  variables: WorkflowVariable[];
  onChange: (vars: WorkflowVariable[]) => void;
}): JSX.Element {
  const [newName, setNewName] = useState('');
  const [newValue, setNewValue] = useState('');
  const [newSource, setNewSource] = useState<'input' | 'constant' | 'env'>('constant');

  const addVariable = () => {
    if (!newName.trim()) return;
    onChange([
      ...variables,
      { name: newName.trim(), value: newValue, source: newSource, nodeId: newSource === 'input' ? 'input' : undefined },
    ]);
    setNewName('');
    setNewValue('');
  };

  const removeVariable = (name: string) => {
    onChange(variables.filter((v) => v.name !== name));
  };

  return (
    <div className="space-y-3">
      {/* Add form */}
      <div className="flex items-center gap-2">
        <input
          className="input-field text-xs flex-1"
          placeholder="变量名"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <input
          className="input-field text-xs flex-1"
          placeholder="默认值"
          value={newValue}
          onChange={(e) => setNewValue(e.target.value)}
        />
        <select
          className="input-field text-xs w-24 shrink-0"
          value={newSource}
          onChange={(e) => setNewSource(e.target.value as 'input' | 'constant' | 'env')}
        >
          <option value="constant">常量</option>
          <option value="input">输入</option>
          <option value="env">环境</option>
        </select>
        <button className="btn-primary text-[10px] px-3 py-1.5" onClick={addVariable}>
          添加
        </button>
      </div>

      {/* List */}
      <div className="space-y-1">
        {variables.map((v) => (
          <div key={v.name} className="flex items-center gap-3 bg-warm-50 rounded px-3 py-2 text-xs">
            <code className="text-primary-600 font-medium min-w-[80px]">{`{{${v.name}}}`}</code>
            <span className="text-warm-400 truncate flex-1">{v.value || '(空)'}</span>
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${
              v.source === 'input' ? 'bg-blue-50 text-blue-600' :
              v.source === 'env' ? 'bg-purple-50 text-purple-600' :
              'bg-warm-100 text-warm-500'
            }`}>
              {v.source}
            </span>
            <button className="text-[10px] text-red-400 hover:text-red-600" onClick={() => removeVariable(v.name)}>
              移除
            </button>
          </div>
        ))}
        {variables.length === 0 && (
          <div className="text-[10px] text-warm-400 text-center py-3">暂无工作流变量</div>
        )}
      </div>
    </div>
  );
}

// ── Main Export ────────────────────────────────────────────────────

export default function WorkflowNodeEditor(props: Props): JSX.Element {
  const [activeSubTab, setActiveSubTab] = useState<'config' | 'variables'>('config');

  return (
    <div className="space-y-4">
      {/* Sub-tabs */}
      <div className="flex items-center gap-1 border-b border-warm-100 pb-2">
        <button
          className={`text-xs px-3 py-1 rounded-t transition-colors ${
            activeSubTab === 'config'
              ? 'text-primary-600 font-medium border-b-2 border-primary-500 -mb-[6px] bg-transparent'
              : 'text-warm-400 hover:text-warm-600'
          }`}
          onClick={() => setActiveSubTab('config')}
        >
          节点配置
        </button>
        <button
          className={`text-xs px-3 py-1 rounded-t transition-colors ${
            activeSubTab === 'variables'
              ? 'text-primary-600 font-medium border-b-2 border-primary-500 -mb-[6px] bg-transparent'
              : 'text-warm-400 hover:text-warm-600'
          }`}
          onClick={() => setActiveSubTab('variables')}
        >
          变量上下文
        </button>
      </div>

      {activeSubTab === 'config' && (
        <>
          {props.nodeType === 'code' && <CodeNodeEditor config={props.config} onChange={props.onChange} />}
          {props.nodeType === 'http' && <HttpNodeEditor config={props.config} onChange={props.onChange} />}
          {props.nodeType === 'knowledge' && <KnowledgeNodeEditor config={props.config} onChange={props.onChange} />}
          {props.nodeType === 'condition' && (
            <ConditionEditor config={props.config} onChange={props.onChange} variables={props.variables} />
          )}
        </>
      )}

      {activeSubTab === 'variables' && (
        <VariableContextEditor variables={props.variables} onChange={props.onVariablesChange} />
      )}
    </div>
  );
}
