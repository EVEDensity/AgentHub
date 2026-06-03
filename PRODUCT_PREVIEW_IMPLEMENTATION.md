# 产物预览与编辑 功能实现文档

> 本文档详细阐述 cc-haha 项目中"产物预览与编辑"功能的实现原理，涵盖三大核心功能：文件预览、代码 Diff 视图、文档引用。

---

## 一、概述

### 1.1 三大功能

| 功能 | 描述 | 场景 |
|------|------|------|
| **支持网页/文档/PPT/代码预览** | 多种文件类型预览 | 选中文件后右侧预览面板 |
| **代码 Diff 视图与版本历史** | 文件变更前后对比 | 工具执行结果可视化 |
| **可引用文档段落给 Agent 处理** | 文本选中后加入对话 | 选中文档内容后发问 |

### 1.2 架构总览

```
┌────────────────────────────────────────────────────────────────────────┐
│                        产物预览与编辑 架构                              │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  ┌──────────────┐                                                      │
│  │  Workspace    │  主面板组件                                         │
│  │  Panel.tsx    │  ─────────────────┐                                 │
│  └──────┬───────┘                    │                                 │
│         │                            ▼                                 │
│         │                  ┌──────────────────┐                       │
│         │                  │  Preview Tabs    │                       │
│         │                  │  (多 Tab 切换)    │                       │
│         │                  └────────┬─────────┘                       │
│         │                           │                                  │
│         │         ┌─────────────────┼─────────────────┐               │
│         │         │                 │                 │                │
│         │         ▼                 ▼                 ▼                │
│         │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│         │  │ CodeSurface │  │ DiffSurface │  │ Markdown    │         │
│         │  │ (代码预览)  │  │ (Diff 视图) │  │ Renderer    │         │
│         │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘         │
│         │         │                │                │                 │
│         ▼         ▼                ▼                ▼                 │
│  ┌─────────────────────────────────────────────────────────┐         │
│  │              Selection Popover (选中菜单)                │         │
│  │            "Add selection to chat"                       │         │
│  └─────────────────────────┬───────────────────────────────┘         │
│                            │                                           │
│                            ▼                                           │
│                   ┌──────────────────┐                                 │
│                   │  ChatInput       │                                 │
│                   │  (添加附件)       │                                 │
│                   └──────────────────┘                                 │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 二、核心组件

### 1. WorkspacePanel — 主预览面板

**文件**: [desktop/src/components/workspace/WorkspacePanel.tsx](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/desktop/src/components/workspace/WorkspacePanel.tsx)

#### 1.1 组件职责

- 树形文件浏览（左侧）
- 多 Tab 文件预览（右侧）
- Diff/文件切换
- 选中文本菜单弹出
- 行内评论
- 文件状态标识（修改、新增、删除等）

#### 1.2 核心类型

```typescript
// workspacePanelStore.ts
export type WorkspacePreviewKind = 'file' | 'diff'

export type WorkspacePreviewTab = {
  id: string                    // Tab 唯一 ID
  path: string                  // 文件路径
  kind: WorkspacePreviewKind    // 文件预览 / Diff 预览
  language?: string             // 代码语言
  state?: 'loading' | 'ok' | 'binary' | 'too_large' | 'missing' | 'error'
  diff?: string                 // Diff 内容（仅 diff 类型）
  content?: string              // 文件内容（仅 file 类型）
}

export type WorkspaceFileStatus =
  | 'modified' | 'added' | 'deleted' | 'renamed'
  | 'untracked' | 'copied' | 'type_changed' | 'unknown'
```

#### 1.3 关键 Hook 使用

```typescript
const { previewTabs, openFile, openDiff, closeTab } = useWorkspacePanelStore(
  useShallow((state) => ({
    previewTabs: state.previewsBySession[sessionId] ?? [],
    openFile: state.openFile,
    openDiff: state.openDiff,
    closeTab: state.closeTab,
  })),
)

const references = useWorkspaceChatContextStore((s) => 
  activeTabId ? s.referencesBySession[activeTabId] ?? [] : []
)
```

---

### 2. WorkspaceCodeSurface — 代码预览核心

**文件**: [desktop/src/components/workspace/WorkspaceCodeSurface.tsx](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/desktop/src/components/workspace/WorkspaceCodeSurface.tsx)

#### 2.1 功能组成

| 组件 | 作用 |
|------|------|
| `InlineHighlightedCode` | 内联代码高亮（用于行内） |
| `WorkspaceDiffSurface` | Diff 视图 |
| `workspacePrismTheme` | Prism 语法高亮主题 |
| `getLanguageFromPath` | 根据文件扩展名推断语言 |

#### 2.2 语言识别映射

```typescript
const map: Record<string, string> = {
  text: 'text',
  typescript: 'typescript',
  ts: 'typescript',
  tsx: 'tsx',
  javascript: 'javascript',
  js: 'javascript',
  jsx: 'jsx',
  markdown: 'markdown',
  md: 'markdown',
  html: 'markup',
  xml: 'markup',
  shell: 'bash',
  sh: 'bash',
  zsh: 'bash',
  diff: 'diff',
}
```

#### 2.3 代码渲染（使用 Prism）

```tsx
import { Highlight } from 'prism-react-renderer'

<Highlight theme={workspacePrismTheme} code={value} language="typescript">
  {({ tokens, getLineProps, getTokenProps }) => (
    <pre>
      {tokens.map((line, i) => (
        <div key={i} {...getLineProps({ line })}>
          {line.map((token, j) => (
            <span key={j} {...getTokenProps({ token })} />
          ))}
        </div>
      ))}
    </pre>
  )}
</Highlight>
```

---

### 3. Diff 视图实现

**核心组件**: `WorkspaceDiffSurface`

#### 3.1 渲染逻辑

```tsx
export function WorkspaceDiffSurface({ value, path, lineLimit = 2000 }) {
  const lines = value.split('\n')
  const visibleLines = showAllLines ? lines : lines.slice(0, lineLimit)
  const language = getLanguageFromPath(path)

  return (
    <pre>
      {visibleLines.map((line, index) => {
        // 解析 diff 行
        const isFileHeader = line.startsWith('diff --') || 
                             line.startsWith('--- ') || 
                             line.startsWith('+++ ')
        const isHunk = line.startsWith('@@')
        const isAdded = line.startsWith('+') && !line.startsWith('+++')
        const isRemoved = line.startsWith('-') && !line.startsWith('---')
        const isCodeLine = isAdded || isRemoved || line.startsWith(' ')
        const code = isCodeLine ? line.slice(1) : line
        const prefix = isCodeLine ? line[0] : ' '

        return (
          <div
            key={index}
            className={`grid grid-cols-[48px_18px_max-content] gap-2 px-3 ${
              isAdded
                ? 'bg-[var(--color-diff-added-bg)]'
                : isRemoved
                  ? 'bg-[var(--color-diff-removed-bg)]'
                  : isHunk
                    ? 'bg-[var(--color-diff-highlight-bg)]'
                    : 'hover:bg-[var(--color-surface-hover)]'
            }`}
          >
            <span className="select-none text-right">{index + 1}</span>
            <span className={`select-none ${isAdded ? 'text-green' : isRemoved ? 'text-red' : ''}`}>
              {prefix}
            </span>
            <span className="whitespace-pre">
              <InlineHighlightedCode value={code} language={language} />
            </span>
          </div>
        )
      })}
    </pre>
  )
}
```

#### 3.2 Diff 颜色变量

```css
:root {
  --color-diff-added-bg: rgba(16, 185, 129, 0.15);
  --color-diff-added-text: rgb(16, 185, 129);
  --color-diff-removed-bg: rgba(239, 68, 68, 0.15);
  --color-diff-removed-text: rgb(239, 68, 68);
  --color-diff-highlight-bg: rgba(245, 158, 11, 0.12);
}
```

#### 3.3 支持的 Diff 格式

```
diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 unchanged line
-removed line
+added line
```

---

### 4. 文件预览类型识别

**文件**: `WorkspacePanel.tsx`

```typescript
function isMarkdownPreview(tab: WorkspacePreviewTab) {
  if (tab.kind !== 'file') return false
  const language = (tab.language ?? '').toLowerCase()
  const extension = getFileExtension(tab.path)
  return language === 'markdown' || language === 'md' 
      || extension === 'md' || extension === 'markdown'
}
```

#### 支持的预览类型

| 文件类型 | 渲染方式 |
|----------|----------|
| **代码文件** (.ts, .js, .py, .rs 等) | Prism 语法高亮 |
| **Markdown** (.md) | MarkdownRenderer |
| **图片** (.png, .jpg, .svg) | `<img>` 标签 |
| **HTML** (.html) | 沙箱渲染 |
| **PPT** (.pptx) | 转换为 HTML 预览 |
| **PDF** (.pdf) | PDF.js 渲染 |
| **大文件** (>2MB) | 仅预览前 2000 行 |

---

## 三、选中文本加入对话

### 1. 选区检测

**文件**: `WorkspacePanel.tsx`

```typescript
function getTextSelectionFromContainer(
  root: HTMLElement | null,
  pointer?: SelectionPointer,
): FloatingSelectionMenuState | null {
  if (!root) return null

  const selection = window.getSelection()
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return null

  const range = selection.getRangeAt(0)
  const startElement = getElementForNode(range.startContainer)
  const endElement = getElementForNode(range.endContainer)
  
  if (!startElement || !endElement || !root.contains(startElement)) {
    return null
  }

  const text = selection.toString().trim()
  if (!text) return null

  return {
    ...getSelectionPosition(range, root, pointer),
    text,
    startLine: getLineNumberFromNode(range.startContainer, root),
    endLine: getLineNumberFromNode(range.endContainer, root),
  }
}
```

### 2. 浮动菜单组件

```tsx
function FloatingSelectionMenu({ selection, onAdd }: {
  selection: FloatingSelectionMenuState | null
  onAdd: () => void
}) {
  if (!selection) return null

  return (
    <button
      type="button"
      onClick={onAdd}
      className="fixed z-50 inline-flex h-11 items-center gap-2 rounded-full 
                 border border-[var(--color-border)] bg-[var(--color-surface)] 
                 px-5 text-[15px] font-semibold shadow-lg"
      style={{ left: selection.x, top: selection.y }}
    >
      <MessageCircle size={21} />
      <span>添加到对话</span>
    </button>
  )
}
```

### 3. 添加到对话

```typescript
const addCurrentSelectionToChat = () => {
  if (!selectionMenu) return
  onAddSelection({
    text: selectionMenu.text,
    startLine: selectionMenu.startLine,
    endLine: selectionMenu.endLine,
  })
  setSelectionMenu(null)
  clearWindowSelection()
}
```

---

## 四、引用数据结构

### 1. WorkspaceChatReference

**文件**: `workspaceChatContextStore.ts`

```typescript
type WorkspaceChatReference = {
  id: string                       // 唯一 ID
  name: string                     // 显示名称
  path: string                     // 相对路径
  absolutePath?: string            // 绝对路径
  isDirectory: boolean             // 是否是目录
  kind: 'file' | 'folder' | 'chat-selection'
  lineStart?: number               // 起始行（可选）
  lineEnd?: number                 // 结束行（可选）
  note?: string                    // 用户添加的注释
  quote?: string                   // 引用内容
}
```

### 2. 转换逻辑

**文件**: `ChatInput.tsx`

```typescript
function workspaceReferenceToAttachment(reference: WorkspaceChatReference): Attachment {
  return {
    id: reference.id,
    name: reference.name,
    type: 'file',
    path: reference.kind === 'chat-selection' ? undefined : reference.path,
    isDirectory: reference.isDirectory,
    lineStart: reference.lineStart,
    lineEnd: reference.lineEnd,
    note: reference.note,
    quote: reference.quote,
  }
}
```

### 3. 发送消息时携带引用

```typescript
const uploadAttachmentPayload: AttachmentRef[] = [
  ...attachments.map((a) => ({
    type: a.type,
    name: a.name,
    path: a.path,
    data: a.data,
    mimeType: a.mimeType,
    lineStart: a.lineStart,
    lineEnd: a.lineEnd,
    note: a.note,
    quote: a.quote,
  })),
  ...workspaceReferences
    .filter((ref) => ref.kind !== 'chat-selection')
    .map((ref) => ({
      name: ref.name,
      path: ref.absolutePath ?? ref.path,
      isDirectory: ref.isDirectory,
      lineStart: ref.lineStart,
      lineEnd: ref.lineEnd,
      note: ref.note,
      quote: ref.quote,
    })),
]
```

---

## 五、依赖项

```json
{
  "dependencies": {
    "prism-react-renderer": "^2.4.0",
    "react-diff-viewer-continued": "^4.0.0",
    "lucide-react": "^0.300.0",
    "zustand": "^4.5.0"
  }
}
```

---

## 六、最小可复刻示例

### 1. 安装依赖

```bash
npm install prism-react-renderer react-diff-viewer-continued lucide-react zustand
```

### 2. 完整示例代码

```tsx
// FilePreviewPanel.tsx
import { useState, useRef, useCallback, useMemo } from 'react'
import { Highlight, type PrismTheme } from 'prism-react-renderer'
import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued'
import { MessageCircle, X, FileText, FilePlus, FileMinus } from 'lucide-react'

// ========== 类型定义 ==========
type FileStatus = 'modified' | 'added' | 'deleted' | 'untracked'

type PreviewTab = {
  id: string
  path: string
  kind: 'file' | 'diff'
  content?: string
  diff?: { old: string; new: string }
  language?: string
  status?: FileStatus
}

type FileReference = {
  id: string
  name: string
  path: string
  lineStart?: number
  lineEnd?: number
  quote?: string
}

// ========== 主题配置 ==========
const previewTheme: PrismTheme = {
  plain: { color: '#1f2937', backgroundColor: 'transparent' },
  styles: [
    { types: ['comment'], style: { color: '#6b7280', fontStyle: 'italic' } },
    { types: ['string'], style: { color: '#059669' } },
    { types: ['keyword'], style: { color: '#7c3aed' } },
    { types: ['function'], style: { color: '#2563eb' } },
    { types: ['number'], style: { color: '#ea580c' } },
  ],
}

// ========== 工具函数 ==========
function getLanguageFromPath(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() || ''
  const map: Record<string, string> = {
    ts: 'typescript', tsx: 'tsx', js: 'javascript', jsx: 'jsx',
    py: 'python', rs: 'rust', go: 'go', java: 'java',
    md: 'markdown', json: 'json', html: 'markup', css: 'css',
    sh: 'bash', bash: 'bash', yml: 'yaml', yaml: 'yaml',
  }
  return map[ext] || 'text'
}

// ========== 选区菜单组件 ==========
function SelectionPopover({ 
  position, 
  onAdd,
  onClose 
}: {
  position: { x: number; y: number } | null
  onAdd: () => void
  onClose: () => void
}) {
  if (!position) return null
  return (
    <div
      className="fixed z-50 inline-flex items-center gap-2 rounded-full 
                 border border-gray-200 bg-white px-4 py-2 text-sm font-semibold 
                 shadow-lg hover:bg-gray-50"
      style={{ left: position.x, top: position.y }}
    >
      <button onClick={onAdd} className="flex items-center gap-2">
        <MessageCircle size={16} />
        添加到对话
      </button>
      <button onClick={onClose} className="ml-2 text-gray-400">
        <X size={14} />
      </button>
    </div>
  )
}

// ========== 代码预览组件 ==========
function CodePreview({ content, language }: { content: string; language: string }) {
  return (
    <Highlight theme={previewTheme} code={content} language={language as any}>
      {({ tokens, getLineProps, getTokenProps }) => (
        <pre className="m-0 font-mono text-xs leading-relaxed">
          {tokens.map((line, i) => {
            const lineProps = getLineProps({ line })
            return (
              <div key={i} {...lineProps} className="grid grid-cols-[40px_1fr] gap-2 px-3 hover:bg-gray-50">
                <span className="select-none text-right text-gray-400">{i + 1}</span>
                <span>
                  {line.map((token, j) => {
                    const tokenProps = getTokenProps({ token })
                    return <span key={j} {...tokenProps} />
                  })}
                </span>
              </div>
            )
          })}
        </pre>
      )}
    </Highlight>
  )
}

// ========== Diff 预览组件 ==========
function DiffPreview({ oldStr, newStr, language }: { 
  oldStr: string; newStr: string; language: string 
}) {
  return (
    <div className="diff-container">
      <ReactDiffViewer
        oldValue={oldStr}
        newValue={newStr}
        splitView={false}
        compareMethod={DiffMethod.WORDS}
        useDarkTheme={false}
      />
    </div>
  )
}

// ========== 主面板组件 ==========
export function FilePreviewPanel({ 
  tabs, 
  onCloseTab, 
  onAddReference,
}: {
  tabs: PreviewTab[]
  onCloseTab: (id: string) => void
  onAddReference: (ref: FileReference) => void
}) {
  const [activeTabId, setActiveTabId] = useState<string | null>(tabs[0]?.id || null)
  const [popoverPos, setPopoverPos] = useState<{ x: number; y: number } | null>(null)
  const [popoverSelection, setPopoverSelection] = useState<{
    text: string
    startLine?: number
    endLine?: number
  } | null>(null)
  const contentRef = useRef<HTMLDivElement>(null)

  const activeTab = useMemo(
    () => tabs.find(t => t.id === activeTabId) || null,
    [tabs, activeTabId]
  )

  // 选区检测
  const handleMouseUp = useCallback(() => {
    setTimeout(() => {
      const selection = window.getSelection()
      if (!selection || selection.isCollapsed || !contentRef.current) {
        setPopoverPos(null)
        return
      }

      const range = selection.getRangeAt(0)
      if (!contentRef.current.contains(range.commonAncestorContainer)) {
        setPopoverPos(null)
        return
      }

      const text = selection.toString().trim()
      if (!text || text.length < 2) {
        setPopoverPos(null)
        return
      }

      const rect = range.getBoundingClientRect()
      setPopoverPos({ x: rect.left, y: rect.top - 50 })
      setPopoverSelection({ text, startLine: 1, endLine: text.split('\n').length })
    }, 10)
  }, [])

  // 添加到对话
  const handleAddToChat = useCallback(() => {
    if (!popoverSelection || !activeTab) return
    
    onAddReference({
      id: `ref-${Date.now()}`,
      name: activeTab.path.split('/').pop() || activeTab.path,
      path: activeTab.path,
      lineStart: popoverSelection.startLine,
      lineEnd: popoverSelection.endLine,
      quote: popoverSelection.text,
    })

    setPopoverPos(null)
    setPopoverSelection(null)
    window.getSelection()?.removeAllRanges()
  }, [popoverSelection, activeTab, onAddReference])

  return (
    <div className="flex h-full flex-col">
      {/* Tab 栏 */}
      <div className="flex border-b border-gray-200 bg-gray-50">
        {tabs.map(tab => (
          <div
            key={tab.id}
            className={`flex items-center gap-2 px-4 py-2 cursor-pointer border-r border-gray-200
                       ${activeTabId === tab.id ? 'bg-white' : 'hover:bg-gray-100'}`}
            onClick={() => setActiveTabId(tab.id)}
          >
            <span className="text-sm font-medium truncate max-w-[200px]">
              {tab.path.split('/').pop()}
            </span>
            {tab.status === 'added' && <FilePlus size={12} className="text-green-500" />}
            {tab.status === 'modified' && <FileText size={12} className="text-yellow-500" />}
            {tab.status === 'deleted' && <FileMinus size={12} className="text-red-500" />}
            <button onClick={(e) => { e.stopPropagation(); onCloseTab(tab.id) }}>
              <X size={12} className="text-gray-400 hover:text-gray-600" />
            </button>
          </div>
        ))}
      </div>

      {/* 内容区域 */}
      <div
        ref={contentRef}
        className="flex-1 overflow-auto bg-white"
        onMouseUp={handleMouseUp}
      >
        {activeTab && (
          <>
            {activeTab.kind === 'file' && (
              <CodePreview
                content={activeTab.content || ''}
                language={activeTab.language || getLanguageFromPath(activeTab.path)}
              />
            )}
            {activeTab.kind === 'diff' && activeTab.diff && (
              <DiffPreview
                oldStr={activeTab.diff.old}
                newStr={activeTab.diff.new}
                language={getLanguageFromPath(activeTab.path)}
              />
            )}
          </>
        )}
      </div>

      {/* 选区菜单 */}
      <SelectionPopover
        position={popoverPos}
        onAdd={handleAddToChat}
        onClose={() => setPopoverPos(null)}
      />
    </div>
  )
}

// ========== 使用示例 ==========
function App() {
  const [tabs, setTabs] = useState<PreviewTab[]>([
    {
      id: '1',
      path: 'src/example.ts',
      kind: 'file',
      content: `function hello() {\n  console.log('Hello, World!')\n}`,
      status: 'modified',
    },
    {
      id: '2',
      path: 'src/example2.ts',
      kind: 'diff',
      diff: {
        old: 'const a = 1\n',
        new: 'const a = 2\n',
      },
    },
  ])

  const [references, setReferences] = useState<FileReference[]>([])

  return (
    <div className="h-screen flex">
      <div className="flex-1">
        <FilePreviewPanel
          tabs={tabs}
          onCloseTab={(id) => setTabs(t => t.filter(tab => tab.id !== id))}
          onAddReference={(ref) => setReferences(r => [...r, ref])}
        />
      </div>
      <div className="w-80 border-l p-4">
        <h3 className="font-bold mb-2">对话引用</h3>
        {references.map(ref => (
          <div key={ref.id} className="p-2 mb-2 bg-gray-50 rounded">
            <div className="text-sm font-medium">{ref.name}</div>
            <div className="text-xs text-gray-500">
              Lines {ref.lineStart}-{ref.lineEnd}
            </div>
            {ref.quote && (
              <div className="mt-1 p-2 bg-white rounded text-xs font-mono">
                {ref.quote.slice(0, 100)}...
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
```

---

## 七、相关文件索引

| 文件 | 描述 |
|------|------|
| `desktop/src/components/workspace/WorkspacePanel.tsx` | 主预览面板组件 |
| `desktop/src/components/workspace/WorkspaceCodeSurface.tsx` | 代码/Diff 表面渲染 |
| `desktop/src/stores/workspacePanelStore.ts` | 预览面板状态管理 |
| `desktop/src/stores/workspaceChatContextStore.ts` | 引用上下文状态 |
| `desktop/src/components/chat/ChatInput.tsx` | 输入框（附件/引用处理） |
| `desktop/src/hooks/useSelectionPopoverDismiss.ts` | 选区菜单关闭逻辑 |

---

## 八、避坑要点

| 问题 | 解决方案 |
|------|----------|
| 选区菜单位置错位 | 使用 `getBoundingClientRect()` 获取精确位置 |
| 跨元素选区丢失行号 | 通过 `data-line-number` 属性追踪 |
| Diff 行误判 | 注意 `+++`/`---` 是文件头，不是代码行 |
| 大文件卡顿 | 限制预览行数（默认 2000 行），提供"显示全部"按钮 |
| 选中文本时菜单闪烁 | 使用 `setTimeout(10ms)` 延迟检查 |
| 引用发送丢失行号 | 用 `lineStart`/`lineEnd` 显式传递 |

---

*最后更新：2026-06-03*
