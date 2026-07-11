'use client';

import { useState, type JSX } from 'react';

interface BuiltinVar {
  key: string;
  description: string;
}

interface Props {
  onInsert: (key: string) => void;
  builtinVars: BuiltinVar[];
  customVars: Record<string, string>;
  onCustomVarAdd: (key: string, defaultValue: string) => void;
  onCustomVarRemove: (key: string) => void;
}

export function PromptVariablePicker({
  onInsert, builtinVars, customVars, onCustomVarAdd, onCustomVarRemove,
}: Props): JSX.Element {
  const [newVarKey, setNewVarKey] = useState('');
  const [newVarDefault, setNewVarDefault] = useState('');

  const handleAddCustom = () => {
    if (!newVarKey.trim()) return;
    const key = newVarKey.trim().replace(/[^a-zA-Z0-9_]/g, '_').toLowerCase();
    if (key && !builtinVars.find((bv) => bv.key === key) && !(key in customVars)) {
      onCustomVarAdd(key, newVarDefault);
      setNewVarKey('');
      setNewVarDefault('');
    }
  };

  return (
    <div className="space-y-3">
      <h4 className="text-[10px] font-semibold text-warm-500 uppercase tracking-wider">变量</h4>

      {/* Built-in variables */}
      <div>
        <div className="text-[10px] text-warm-400 mb-1">内置变量</div>
        <div className="space-y-0.5">
          {builtinVars.map((v) => (
            <button
              key={v.key}
              className="w-full text-left text-[10px] px-2 py-1.5 rounded hover:bg-primary-50 hover:text-primary-600 transition-colors group flex items-center gap-1"
              onClick={() => onInsert(v.key)}
              title={v.description}
            >
              <span className="shrink-0 w-4 h-4 rounded bg-primary-100 text-primary-600 flex items-center justify-center text-[8px] font-bold group-hover:bg-primary-200">
                {'{ }'}
              </span>
              <span className="truncate">{v.key}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Custom variables */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-warm-400">自定义变量</span>
        </div>
        {Object.keys(customVars).length === 0 ? (
          <div className="text-[10px] text-warm-300 italic">尚未定义</div>
        ) : (
          <div className="space-y-0.5">
            {Object.entries(customVars).map(([key, defaultVal]) => (
              <div key={key} className="flex items-center gap-1 group">
                <button
                  className="flex-1 text-left text-[10px] px-2 py-1.5 rounded hover:bg-warning-50 hover:text-warning-600 transition-colors flex items-center gap-1"
                  onClick={() => onInsert(key)}
                >
                  <span className="shrink-0 w-4 h-4 rounded bg-warning-100 text-warning-600 flex items-center justify-center text-[8px] font-bold">
                    {'{ }'}
                  </span>
                  <span className="truncate">{key}</span>
                  {defaultVal && <span className="text-warm-400 italic">= {defaultVal}</span>}
                </button>
                <button
                  className="shrink-0 text-warm-300 hover:text-danger-500 opacity-0 group-hover:opacity-100"
                  onClick={() => onCustomVarRemove(key)}
                >
                  <span className="material-symbols-outlined text-[12px]">close</span>
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Add custom var */}
        <div className="mt-2 space-y-1">
          <input
            className="input-field w-full text-[10px] py-1"
            placeholder="变量名 (如: project_name)"
            value={newVarKey}
            onChange={(e) => setNewVarKey(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleAddCustom(); }}
          />
          <div className="flex items-center gap-1">
            <input
              className="input-field flex-1 text-[10px] py-1"
              placeholder="默认值 (可选)"
              value={newVarDefault}
              onChange={(e) => setNewVarDefault(e.target.value)}
            />
            <button
              className="btn-primary text-[10px] px-2 py-1 shrink-0"
              onClick={handleAddCustom}
              disabled={!newVarKey.trim()}
            >
              +
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
