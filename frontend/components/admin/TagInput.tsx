'use client';

import { useState, type JSX, type KeyboardEvent } from 'react';
import { X } from 'lucide-react';

interface TagInputProps {
  tags: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
  maxTags?: number;
}

export default function TagInput({ tags, onChange, placeholder = '输入标签后按 Enter 添加...', maxTags = 8 }: TagInputProps): JSX.Element {
  const [input, setInput] = useState('');

  function addTag() {
    const tag = input.trim();
    if (!tag || tags.includes(tag) || tags.length >= maxTags) return;
    onChange([...tags, tag]);
    setInput('');
  }

  function removeTag(index: number) {
    onChange(tags.filter((_, i) => i !== index));
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault();
      addTag();
    } else if (e.key === 'Backspace' && !input && tags.length > 0) {
      removeTag(tags.length - 1);
    }
  }

  return (
    <div className="w-full">
      <div className="flex flex-wrap gap-1.5 mb-1.5">
        {tags.map((tag, i) => (
          <span
            key={`${tag}-${i}`}
            className="inline-flex items-center gap-1 rounded-full bg-primary-50 px-2.5 py-0.5 text-xs font-medium text-primary-700"
          >
            {tag}
            <button
              type="button"
              onClick={() => removeTag(i)}
              className="ml-0.5 rounded-full p-0.5 hover:bg-primary-200 transition-colors"
              aria-label={`移除标签 ${tag}`}
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
      </div>
      {tags.length < maxTags && (
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          className="w-full rounded-lg border border-warm-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-400/60"
        />
      )}
      {tags.length >= maxTags && (
        <p className="text-xs text-warm-400">已达到最大标签数 ({maxTags})</p>
      )}
    </div>
  );
}
