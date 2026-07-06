import { useCallback, useEffect, useState, type JSX } from 'react';
import dynamic from 'next/dynamic';

const CodeReviewPanel = dynamic(() => import('../chat/CodeReviewPanel'), {
  ssr: false,
  loading: () => null,
});

interface GeneralSettingsModuleProps {
  authHeaders: () => Record<string, string>;
}

const DEMO_DIFF = `diff --git a/app/api/settings.py b/app/api/settings.py
index 0000000..a1b2c3d 100644
--- a/app/api/settings.py
+++ b/app/api/settings.py
@@ -1,0 +1,45 @@
+from __future__ import annotations
+
+import json
+from pathlib import Path
+from typing import Any
+
+from fastapi import APIRouter
+from pydantic import BaseModel
+
+from app.config import DATA_DIR
+
+router = APIRouter(prefix="/api", tags=["settings"])
+
+SETTINGS_PATH = DATA_DIR / "settings.json"
+
+DEFAULTS: dict[str, Any] = {
+    "theme": "warm",
+    "lang": "zh",
+    "reply_lang": "default",
+    "reasoning": 2,
+    "thinking": True,
+    "notify": True,
+    "zoom": 100,
+}
+
+
+def _read_settings() -> dict[str, Any]:
+    settings: dict[str, Any] = dict(DEFAULTS)
+    try:
+        if SETTINGS_PATH.exists():
+            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
+            if isinstance(raw, dict):
+                settings.update(raw)
+    except (json.JSONDecodeError, OSError):
+        pass
+    return {k: settings.get(k, DEFAULTS[k]) for k in DEFAULTS}
+
+
+@router.get("/settings")
+async def get_settings() -> dict[str, Any]:
+    return _read_settings()
diff --git a/frontend/components/chat/CodeReviewPanel.tsx b/frontend/components/chat/CodeReviewPanel.tsx
index 0000000..e4f5g6h 100644
--- a/frontend/components/chat/CodeReviewPanel.tsx
+++ b/frontend/components/chat/CodeReviewPanel.tsx
@@ -1,0 +1,30 @@
+import React, { useEffect, useMemo, useState, type JSX } from 'react';
+
+interface FileDiff {
+  path: string;
+  oldPath: string;
+  lang: string;
+  hunks: DiffHunk[];
+  added: number;
+  deleted: number;
+}
+
+export default function CodeReviewPanel({ content }: { content: string }): JSX.Element {
+  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());
+  const files = useMemo(() => parseDiff(content), [content]);
+
+  const totalAdded = useMemo(() => files.reduce((s, f) => s + f.added, 0), [files]);
+  const totalDeleted = useMemo(() => files.reduce((s, f) => s + f.deleted, 0), [files]);
+
+  useEffect(() => {
+    if (files.length > 0) {
+      setExpandedFiles(new Set([files[0].path]));
+    }
+  }, [files]);
+
+  return (
+    <div className="rounded-2xl border border-warm-200 bg-warm-100">
+      {/* Diff rendering logic */}
+    </div>
+  );
+}
diff --git a/frontend/styles/globals.css b/frontend/styles/globals.css
index a1b2c3d..e4f5g6h 100644
--- a/frontend/styles/globals.css
+++ b/frontend/styles/globals.css
@@ -145,7 +145,7 @@
 [data-theme="warm"] {
-  --bg-root: #FAF9F7;
-  --bg-surface: #FFFFFF;
-  --bg-elevated: #F5F4F0;
+  --warm-50: 250 249 247;
+  --warm-100: 245 244 240;
+  --warm-150: 238 237 232;
+  --warm-200: 230 229 223;
@@ -196,4 +196,3 @@
 [data-theme="dark"] body {
-  background: var(--bg-root);
-  color: var(--text-primary);
+  background: #1E1E1E;
+  color: #E8E8E8;
 }`;

export default function GeneralSettingsModule({ authHeaders }: GeneralSettingsModuleProps): JSX.Element {
  // ── General settings state ─────────────────────────────────────
  const [generalTheme, setGeneralTheme] = useState<string>(
    () => typeof window !== 'undefined' ? (localStorage.getItem('agenthub_theme') || 'warm') : 'warm'
  );
  const [generalLang, setGeneralLang] = useState<string>(
    () => typeof window !== 'undefined' ? (localStorage.getItem('agenthub_lang') || 'zh') : 'zh'
  );
  const [generalReplyLang, setGeneralReplyLang] = useState<string>(
    () => typeof window !== 'undefined' ? (localStorage.getItem('agenthub_reply_lang') || 'default') : 'default'
  );
  const [generalReasoning, setGeneralReasoning] = useState<number>(
    () => typeof window !== 'undefined' ? parseInt(localStorage.getItem('agenthub_reasoning') || '2', 10) : 2
  );
  const [generalThinking, setGeneralThinking] = useState<boolean>(
    () => typeof window !== 'undefined' ? localStorage.getItem('agenthub_thinking') !== 'false' : true
  );
  const [generalNotify, setGeneralNotify] = useState<boolean>(
    () => typeof window !== 'undefined' ? localStorage.getItem('agenthub_notify') !== 'false' : true
  );
  const [generalZoom, setGeneralZoom] = useState<number>(
    () => typeof window !== 'undefined' ? parseInt(localStorage.getItem('agenthub_zoom') || '100', 10) : 100
  );
  const [generalSettingsLoaded, setGeneralSettingsLoaded] = useState(false);

  // ── Load settings from backend on mount ──────────────────────────
  useEffect(() => {
    if (typeof window === 'undefined') return;
    fetch('/api/user/settings', { headers: authHeaders() })
      .then((r) => r.ok ? r.json() : null)
      .then((data: { settings?: Record<string, string> } | null) => {
        if (!data?.settings) return;
        const s = data.settings;
        if (typeof s.theme === 'string') { setGeneralTheme(s.theme); localStorage.setItem('agenthub_theme', s.theme); }
        if (typeof s.lang === 'string') { setGeneralLang(s.lang); localStorage.setItem('agenthub_lang', s.lang); }
        if (typeof s.reply_lang === 'string') { setGeneralReplyLang(s.reply_lang); localStorage.setItem('agenthub_reply_lang', s.reply_lang); }
        if (typeof s.reasoning === 'string') { const v = parseInt(s.reasoning, 10); if (!isNaN(v)) { setGeneralReasoning(v); localStorage.setItem('agenthub_reasoning', s.reasoning); } }
        if (typeof s.thinking === 'string') { const v = s.thinking !== 'false'; setGeneralThinking(v); localStorage.setItem('agenthub_thinking', s.thinking); }
        if (typeof s.notify === 'string') { const v = s.notify !== 'false'; setGeneralNotify(v); localStorage.setItem('agenthub_notify', s.notify); }
        if (typeof s.zoom === 'string') { const v = parseInt(s.zoom, 10); if (!isNaN(v)) { setGeneralZoom(v); localStorage.setItem('agenthub_zoom', s.zoom); } }
      })
      .catch(() => { /* backend may not be running — use localStorage defaults */ })
      .finally(() => setGeneralSettingsLoaded(true));
  }, []);

  // ── Sync helper: persist settings to per-user backend ──────────
  const syncSetting = useCallback((key: string, value: unknown): void => {
    if (typeof window === 'undefined') return;
    const token = localStorage.getItem('agenthub_token');
    if (!token) return;
    fetch('/api/user/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ key, value: String(value) }),
    }).catch(() => { /* backend off — saved in localStorage only */ });
  }, []);

  // Apply theme to document
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', generalTheme);
    localStorage.setItem('agenthub_theme', generalTheme);
    if (generalSettingsLoaded) syncSetting('theme', generalTheme);
  }, [generalTheme, generalSettingsLoaded, syncSetting]);

  // Apply language
  useEffect(() => {
    document.documentElement.lang = generalLang === 'en' ? 'en' : 'zh-CN';
    localStorage.setItem('agenthub_lang', generalLang);
    if (generalSettingsLoaded) syncSetting('lang', generalLang);
  }, [generalLang, generalSettingsLoaded, syncSetting]);

  // Apply zoom
  useEffect(() => {
    (document.body.style as any).zoom = `${generalZoom}%`;
    localStorage.setItem('agenthub_zoom', String(generalZoom));
    if (generalSettingsLoaded) syncSetting('zoom', generalZoom);
  }, [generalZoom, generalSettingsLoaded, syncSetting]);

  // Sync reply_lang
  useEffect(() => {
    localStorage.setItem('agenthub_reply_lang', generalReplyLang);
    if (generalSettingsLoaded) syncSetting('reply_lang', generalReplyLang);
  }, [generalReplyLang, generalSettingsLoaded, syncSetting]);

  // Sync reasoning
  useEffect(() => {
    localStorage.setItem('agenthub_reasoning', String(generalReasoning));
    if (generalSettingsLoaded) syncSetting('reasoning', generalReasoning);
  }, [generalReasoning, generalSettingsLoaded, syncSetting]);

  // Sync thinking
  useEffect(() => {
    localStorage.setItem('agenthub_thinking', String(generalThinking));
    if (generalSettingsLoaded) syncSetting('thinking', generalThinking);
  }, [generalThinking, generalSettingsLoaded, syncSetting]);

  // Sync notify
  useEffect(() => {
    localStorage.setItem('agenthub_notify', String(generalNotify));
    if (generalSettingsLoaded) syncSetting('notify', generalNotify);
  }, [generalNotify, generalSettingsLoaded, syncSetting]);

  // Keyboard zoom handler
  useEffect(() => {
    function handleZoomKey(e: KeyboardEvent): void {
      const mod = e.ctrlKey || e.metaKey;
      if (!mod) return;
      if (e.key === '=' || e.key === '+') {
        e.preventDefault();
        setGeneralZoom((prev) => Math.min(200, prev + 10));
      } else if (e.key === '-') {
        e.preventDefault();
        setGeneralZoom((prev) => Math.max(50, prev - 10));
      } else if (e.key === '0') {
        e.preventDefault();
        setGeneralZoom(100);
      }
    }
    window.addEventListener('keydown', handleZoomKey);
    return () => window.removeEventListener('keydown', handleZoomKey);
  }, []);

  // ── Render ────────────────────────────────────────────────────────
  const THEME_OPTIONS = [
    { value: 'light', label: '纯白', icon: 'light_mode', desc: '明亮清爽的工作区' },
    { value: 'warm', label: '经典暖色', icon: 'routine', desc: '柔和的暖色调，护眼舒适' },
    { value: 'dark', label: '暗色', icon: 'dark_mode', desc: '深色界面，适合昏暗环境' },
  ];
  const REASONING_LABELS = ['低', '中', '高', '最大'];

  return (
    <div className="space-y-4 max-w-4xl">
      {/* ── 配色主题 ─────────────────────────────────────────── */}
      <section className="rounded-2xl border border-warm-200 bg-warm-100 overflow-hidden">
        <div className="border-b border-warm-100 px-5 py-3">
          <h3 className="text-base font-semibold text-warm-900">配色主题</h3>
          <p className="text-xs text-warm-500 mt-0.5">在经典暖色、暗色与纯白工作区之间切换。</p>
        </div>
        <div className="px-5 py-4 grid grid-cols-3 gap-3">
          {THEME_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setGeneralTheme(opt.value)}
              className={`relative rounded-xl border-2 px-4 py-4 text-left transition-all ${
                generalTheme === opt.value
                  ? 'border-primary-400 bg-primary-50 ring-1 ring-primary-200'
                  : 'border-warm-150 bg-warm-100 hover:border-warm-300 hover:bg-warm-50'
              }`}
            >
              <div className="flex items-center gap-2 mb-2">
                <span className={`material-symbols-outlined text-[20px] ${
                  generalTheme === opt.value ? 'text-primary-600' : 'text-warm-400'
                }`}>
                  {opt.icon}
                </span>
              </div>
              <div className={`text-sm font-semibold ${
                generalTheme === opt.value ? 'text-primary-800' : 'text-warm-700'
              }`}>
                {opt.label}
              </div>
              <div className="text-[11px] text-warm-400 mt-0.5">{opt.desc}</div>
              {generalTheme === opt.value && (
                <span className="absolute top-3 right-3 material-symbols-outlined text-[18px] text-primary-500">
                  check_circle
                </span>
              )}
            </button>
          ))}
        </div>
      </section>

      {/* ── 语言 ─────────────────────────────────────────────── */}
      <section className="rounded-2xl border border-warm-200 bg-warm-100 overflow-hidden">
        <div className="border-b border-warm-100 px-5 py-3">
          <h3 className="text-base font-semibold text-warm-900">语言</h3>
          <p className="text-xs text-warm-500 mt-0.5">选择应用程序的显示语言。</p>
        </div>
        <div className="px-5 py-4">
          <div className="inline-flex rounded-lg border border-warm-200 bg-warm-50 p-1">
            {[
              { value: 'en', label: 'English' },
              { value: 'zh', label: '中文' },
            ].map((opt) => (
              <button
                key={opt.value}
                onClick={() => { setGeneralLang(opt.value); }}
                className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
                  generalLang === opt.value
                    ? 'bg-warm-100 text-warm-900 shadow-sm'
                    : 'text-warm-500 hover:text-warm-700'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* ── 回复语言 ─────────────────────────────────────────── */}
      <section className="rounded-2xl border border-warm-200 bg-warm-100 overflow-hidden">
        <div className="border-b border-warm-100 px-5 py-3">
          <h3 className="text-base font-semibold text-warm-900">回复语言</h3>
          <p className="text-xs text-warm-500 mt-0.5">指定 Claude 始终以某种语言回复。</p>
        </div>
        <div className="px-5 py-4">
          <select
            className="min-h-10 rounded-xl border border-warm-200 bg-warm-50 px-3 text-sm text-warm-700 outline-none min-w-[240px]"
            value={generalReplyLang}
            onChange={(e) => { setGeneralReplyLang(e.target.value); }}
          >
            <option value="default">默认（跟随提示词语言）</option>
            <option value="english">English</option>
            <option value="chinese">中文</option>
            <option value="japanese">日本語</option>
          </select>
        </div>
      </section>

      {/* ── 推理强度 ─────────────────────────────────────────── */}
      <section className="rounded-2xl border border-warm-200 bg-warm-100 overflow-hidden">
        <div className="border-b border-warm-100 px-5 py-3">
          <h3 className="text-base font-semibold text-warm-900">推理强度</h3>
          <p className="text-xs text-warm-500 mt-0.5">控制模型使用的计算量。更高强度带来更深入的推理，但响应速度会变慢。</p>
        </div>
        <div className="px-5 py-4">
          <div className="flex items-center gap-1 max-w-md">
            {[1, 2, 3, 4].map((level) => (
              <button
                key={level}
                onClick={() => { setGeneralReasoning(level); }}
                className={`flex-1 py-2.5 text-sm font-medium rounded-lg border transition-all ${
                  generalReasoning >= level
                    ? 'bg-primary-50 border-primary-300 text-primary-700'
                    : 'bg-warm-100 border-warm-200 text-warm-500 hover:border-warm-300'
                }`}
              >
                {REASONING_LABELS[level - 1]}
              </button>
            ))}
          </div>
          <div className="flex justify-between max-w-md mt-1.5 px-1">
            <span className="text-[10px] text-warm-400">快速响应</span>
            <span className="text-[10px] text-warm-400">深度推理</span>
          </div>
        </div>
      </section>

      {/* ── 思考模式 ─────────────────────────────────────────── */}
      <section className="rounded-2xl border border-warm-200 bg-warm-100 overflow-hidden">
        <div className="px-5 py-4 flex items-center justify-between gap-4">
          <div>
            <h3 className="text-base font-semibold text-warm-900">思考模式</h3>
            <p className="text-xs text-warm-500 mt-0.5">
              控制新会话是否启用模型思考。关闭后，DeepSeek 等兼容供应商会收到显式非思考模式参数。
            </p>
          </div>
          <button
            onClick={() => { setGeneralThinking(!generalThinking); }}
            className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors ${
              generalThinking ? 'bg-primary-500' : 'bg-warm-300'
            }`}
            role="switch"
            aria-checked={generalThinking}
          >
            <span className={`inline-block h-5 w-5 rounded-full bg-warm-100 shadow-sm transition-transform ${
              generalThinking ? 'translate-x-6' : 'translate-x-1'
            }`} />
          </button>
        </div>
      </section>

      {/* ── 系统通知 ─────────────────────────────────────────── */}
      <section className="rounded-2xl border border-warm-200 bg-warm-100 overflow-hidden">
        <div className="px-5 py-4 flex items-center justify-between gap-4">
          <div>
            <h3 className="text-base font-semibold text-warm-900">系统通知</h3>
            <p className="text-xs text-warm-500 mt-0.5">
              使用操作系统原生通知提醒授权确认、Agent 回复完成和定时任务结果。
            </p>
          </div>
          <button
            onClick={() => {
              const next = !generalNotify;
              setGeneralNotify(next);
              if (next && 'Notification' in window && Notification.permission === 'default') {
                Notification.requestPermission();
              }
            }}
            className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors ${
              generalNotify ? 'bg-primary-500' : 'bg-warm-300'
            }`}
            role="switch"
            aria-checked={generalNotify}
          >
            <span className={`inline-block h-5 w-5 rounded-full bg-warm-100 shadow-sm transition-transform ${
              generalNotify ? 'translate-x-6' : 'translate-x-1'
            }`} />
          </button>
        </div>
      </section>

      {/* ── 界面缩放 ─────────────────────────────────────────── */}
      <section className="rounded-2xl border border-warm-200 bg-warm-100 overflow-hidden">
        <div className="border-b border-warm-100 px-5 py-3">
          <h3 className="text-base font-semibold text-warm-900">界面缩放</h3>
          <p className="text-xs text-warm-500 mt-0.5">调整整个界面的显示大小。</p>
        </div>
        <div className="px-5 py-4">
          <div className="flex items-center gap-4 max-w-lg">
            <span className="text-xs text-warm-400 shrink-0">50%</span>
            <input
              type="range"
              min="50"
              max="200"
              step="10"
              value={generalZoom}
              onChange={(e) => setGeneralZoom(parseInt(e.target.value, 10))}
              className="flex-1 h-2 rounded-full bg-warm-200 appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-5 [&::-webkit-slider-thumb]:h-5 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-primary-500 [&::-webkit-slider-thumb]:shadow-sm [&::-webkit-slider-thumb]:cursor-pointer"
            />
            <span className="text-xs text-warm-400 shrink-0">200%</span>
            <div className="flex items-center gap-1 ml-2">
              <button
                onClick={() => setGeneralZoom(Math.max(50, generalZoom - 10))}
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-warm-200 bg-warm-100 text-warm-500 hover:bg-warm-50 transition-colors"
                title="缩小"
              >
                <span className="text-[18px] font-medium">−</span>
              </button>
              <span className="w-14 text-center text-sm font-semibold text-warm-700">{generalZoom}%</span>
              <button
                onClick={() => setGeneralZoom(Math.min(200, generalZoom + 10))}
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-warm-200 bg-warm-100 text-warm-500 hover:bg-warm-50 transition-colors"
                title="放大"
              >
                <span className="text-[18px] font-medium">+</span>
              </button>
              <button
                onClick={() => setGeneralZoom(100)}
                className="ml-1 px-2 py-1 text-xs text-warm-500 hover:text-warm-700 underline underline-offset-2"
              >
                重置
              </button>
            </div>
          </div>
          <div className="mt-3 flex items-center gap-4 text-[11px] text-warm-400">
            <span>快捷键：</span>
            <kbd className="rounded border border-warm-200 bg-warm-50 px-1.5 py-0.5 text-[10px] font-mono">Ctrl</kbd>
            <span>+</span>
            <kbd className="rounded border border-warm-200 bg-warm-50 px-1.5 py-0.5 text-[10px] font-mono">+</kbd>
            <span className="mx-2 text-warm-300">/</span>
            <kbd className="rounded border border-warm-200 bg-warm-50 px-1.5 py-0.5 text-[10px] font-mono">Ctrl</kbd>
            <span>+</span>
            <kbd className="rounded border border-warm-200 bg-warm-50 px-1.5 py-0.5 text-[10px] font-mono">-</kbd>
            <span className="mx-2 text-warm-300">/</span>
            <kbd className="rounded border border-warm-200 bg-warm-50 px-1.5 py-0.5 text-[10px] font-mono">Ctrl</kbd>
            <span>+</span>
            <kbd className="rounded border border-warm-200 bg-warm-50 px-1.5 py-0.5 text-[10px] font-mono">0</kbd>
            <span className="ml-1">恢复 100%</span>
          </div>
        </div>
      </section>

      {/* ── 代码审查 Demo ─────────────────────────────────────── */}
      <section className="rounded-2xl border border-primary-200 bg-warm-100 overflow-hidden">
        <div className="border-b border-primary-100 bg-primary-50/50 px-5 py-3 flex items-center justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold text-warm-900">🧪 代码审查演示</h3>
            <p className="text-xs text-warm-500 mt-0.5">下方展示 CodeReviewPanel 组件对多文件 git diff 的渲染效果。</p>
          </div>
          <span className="tag tag-blue shrink-0">实时预览</span>
        </div>
        <div className="px-5 py-4">
          <CodeReviewPanel content={DEMO_DIFF} />
        </div>
      </section>
    </div>
  );
}
