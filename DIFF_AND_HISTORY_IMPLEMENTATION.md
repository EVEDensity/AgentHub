# Diff 视图与版本历史 实现与复刻指南

> 本文档系统性梳理 cc-haha 项目中 **代码 Diff 视图** 与 **版本历史（Turn Checkpoint）** 的实现原理。
> 包含前端组件、后端服务、API 协议、存储机制、以及一份"AI 复刻提示词"。

---

## 一、总体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                      Diff & History 架构                              │
├──────────────────────────────────────────────────────────────────────┤
│                                                                        │
│   ┌──────────────────┐  React  ┌──────────────────┐                   │
│   │ DiffViewer.tsx   │ ◄─────► │  ToolCallBlock   │                   │
│   │ (单文件 Diff)    │         │  (工具结果展示)   │                   │
│   └────────┬─────────┘         └─────────┬────────┘                   │
│            │                             │                             │
│            │         ┌───────────────────┘                             │
│            ▼         ▼                                                  │
│   ┌────────────────────────────────────┐                              │
│   │   WorkspaceCodeSurface.tsx          │                              │
│   │   - WorkspaceDiffSurface (unified)  │                              │
│   │   - WorkspaceCodeSurface (代码)     │                              │
│   │   - useTurnCheckpoints (列表)       │                              │
│   └──────────────┬─────────────────────┘                              │
│                  │                                                     │
│                  │ REST/WebSocket                                      │
│                  ▼                                                     │
│   ┌──────────────────────────────────────┐                            │
│   │       Server: workspaceService.ts    │                            │
│   │  - getDiff()        (单文件 diff)    │                            │
│   │  - getStatus()      (变更文件列表)   │                            │
│   │  - getSessionFileChanges()           │                            │
│   └──────────────┬───────────────────────┘                            │
│                  │                                                     │
│   ┌──────────────┼───────────────────────────┐                       │
│   │              │                            │                       │
│   ▼              ▼                            ▼                       │
│  ┌──────────┐  ┌────────────┐         ┌──────────────────┐            │
│  │  Git CLI │  │ Transcript │         │ File History     │            │
│  │ (git     │  │ (从对话    │         │  Backups         │            │
│  │  diff)   │  │  历史推断) │         │  ~/.claude/      │            │
│  │          │  │            │         │  file-history/   │            │
│  └──────────┘  └────────────┘         └──────────────────┘            │
│                                                                        │
│   ┌──────────────────────────────────────┐                            │
│   │  Server: sessionRewindService.ts      │                            │
│   │  - listSessionTurnCheckpoints()       │                            │
│   │  - getSessionTurnCheckpointDiff()     │                            │
│   │  - executeSessionRewind()             │                            │
│   └──────────────────────────────────────┘                            │
│                                                                        │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 二、核心 Diff 数据结构

### 2.1 前端类型 (TypeScript)

**文件**: [desktop/src/api/sessions.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/desktop/src/api/sessions.ts)

```typescript
// 单文件 Diff 结果
export type WorkspaceDiffResult = {
  state: 'ok' | 'missing' | 'not_git_repo' | 'error'
  path: string
  diff?: string         // Unified Diff 格式文本
  error?: string
}

// 文件变更条目（出现在列表中）
export type WorkspaceChangedFile = {
  path: string
  oldPath?: string
  status: 'modified' | 'added' | 'deleted' | 'renamed' 
        | 'untracked' | 'copied' | 'type_changed' | 'unknown'
  additions: number
  deletions: number
}

// 工作区状态（包含所有变更文件）
export type WorkspaceStatusResult = {
  state: 'ok' | 'not_git_repo' | 'missing_workdir' | 'error'
  workDir: string
  repoName: string | null
  branch: string | null
  isGitRepo: boolean
  changedFiles: WorkspaceChangedFile[]
  error?: string
}

// Turn Checkpoint（版本快照）
export type SessionTurnCheckpoint = {
  target: {
    targetUserMessageId: string   // 对应用户消息 ID
    userMessageIndex: number      // 用户消息索引
    userMessageCount: number      // 用户消息总数
    messagesRemoved: number       // 此 checkpoint 之后还有多少消息
  }
  conversation?: {
    messagesRemoved: number
  }
  code: {
    available: boolean
    filesChanged: Array<{
      trackingPath: string
      backupFileName: string | null
      displayPath: string
    }>
    insertions: number
    deletions: number
  }
  workDir?: string
}

// Turn Diff 结果（绑定到 checkpoint 的单文件 diff）
export type TurnCheckpointDiffResult = WorkspaceDiffResult & {
  target?: SessionTurnCheckpoint['target']
  workDir?: string
}
```

### 2.2 后端类型 (TypeScript)

**文件**: [src/server/services/workspaceService.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/server/services/workspaceService.ts)

```typescript
// 后端 Diff 结果
type WorkspaceDiffResult = {
  state: 'ok' | 'missing' | 'not_git_repo' | 'error'
  path: string
  diff?: string
  error?: string
}

// 后端会话级文件变更
type SessionFileChange = {
  path: string
  status: 'added' | 'modified' | 'deleted'
  additions: number
  deletions: number
  diff?: string
}

// 文件历史快照
type FileHistorySnapshot = {
  trackedFileBackups: Record<string, {
    backupFileName: string | null   // null = 文件原本不存在
  }>
}
```

---

## 三、Diff 视图前端实现

### 3.1 DiffViewer 组件

**文件**: [desktop/src/components/chat/DiffViewer.tsx](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/desktop/src/components/chat/DiffViewer.tsx)

#### 3.1.1 核心实现

```tsx
import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued'
import { Highlight, type PrismTheme } from 'prism-react-renderer'

type Props = {
  filePath: string
  oldString: string
  newString: string
}

function inferLanguage(filePath: string): string {
  const ext = filePath.split('.').pop()?.toLowerCase()
  const langMap: Record<string, string> = {
    ts: 'typescript', tsx: 'tsx', js: 'javascript', jsx: 'jsx',
    py: 'python', rs: 'rust', go: 'go', rb: 'ruby',
    json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'toml',
    md: 'markdown', css: 'css', html: 'markup', xml: 'markup',
    sql: 'sql', sh: 'bash', bash: 'bash', zsh: 'bash',
  }
  return langMap[ext ?? ''] || 'text'
}

const warmSyntaxTheme: PrismTheme = {
  plain: { color: 'var(--color-code-fg)', backgroundColor: 'transparent' },
  styles: [
    { types: ['comment'], style: { color: 'var(--color-code-comment)', fontStyle: 'italic' } },
    { types: ['string'], style: { color: 'var(--color-code-string)' } },
    { types: ['keyword'], style: { color: 'var(--color-code-keyword)' } },
    { types: ['function'], style: { color: 'var(--color-code-function)' } },
    { types: ['number'], style: { color: 'var(--color-code-number)' } },
    { types: ['property'], style: { color: 'var(--color-code-property)' } },
  ],
}

function highlightSyntax(str: string, language: string) {
  return (
    <Highlight theme={warmSyntaxTheme} code={str} language={language}>
      {({ tokens, getTokenProps }) => (
        <>
          {tokens.map((line, i) => (
            <span key={i}>
              {line.map((token, key) => (
                <span key={key} {...getTokenProps({ token })} />
              ))}
            </span>
          ))}
        </>
      )}
    </Highlight>
  )
}

const diffStyles = {
  variables: {
    light: {
      diffViewerBackground: 'var(--color-code-bg)',
      addedBackground: 'var(--color-diff-added-bg)',
      removedBackground: 'var(--color-diff-removed-bg)',
      wordAddedBackground: 'var(--color-diff-added-word)',
      wordRemovedBackground: 'var(--color-diff-removed-word)',
      addedGutterBackground: 'var(--color-diff-added-gutter)',
      removedGutterBackground: 'var(--color-diff-removed-gutter)',
      gutterBackground: 'var(--color-surface-container-low)',
      highlightBackground: 'var(--color-diff-highlight-bg)',
    },
  },
  diffContainer: { fontSize: '12px', lineHeight: '1.45' },
  gutter: { padding: '1px 8px', minWidth: '40px', fontSize: '11px' },
  wordDiff: { padding: '1px 2px', borderRadius: '2px' },
}

export function DiffViewer({ filePath, oldString, newString }: Props) {
  const language = inferLanguage(filePath)

  // 统计 +/- 行数
  const oldLines = oldString.split('\n')
  const newLines = newString.split('\n')
  const additions = newLines.filter((l, i) => l !== (oldLines[i] ?? null)).length
  const deletions = oldLines.filter((l, i) => l !== (newLines[i] ?? null)).length

  return (
    <div className="overflow-hidden rounded-lg border bg-[var(--color-surface-container-low)]">
      {/* Header */}
      <div className="flex items-center justify-between border-b px-3 py-1.5">
        <div>
          <div className="font-mono text-[11px] text-[var(--color-text-tertiary)]">
            {filePath}
          </div>
          <div className="mt-1 flex items-center gap-2 text-[10px] uppercase">
            <span className="rounded-full bg-[var(--color-diff-added-bg)] px-2 py-0.5 text-[var(--color-diff-added-text)]">
              +{additions}
            </span>
            <span className="rounded-full bg-[var(--color-diff-removed-bg)] px-2 py-0.5 text-[var(--color-diff-removed-text)]">
              -{deletions}
            </span>
          </div>
        </div>
      </div>

      {/* Diff Body */}
      <div className="max-h-[400px] overflow-auto">
        <ReactDiffViewer
          oldValue={oldString}
          newValue={newString}
          splitView={false}                       // 统一视图（非左右分栏）
          compareMethod={DiffMethod.WORDS}       // 单词级 diff
          renderContent={(str) => highlightSyntax(str, language)}
          hideLineNumbers={false}
          styles={diffStyles}
          useDarkTheme={document.documentElement.getAttribute('data-theme') === 'dark'}
        />
      </div>
    </div>
  )
}
```

#### 3.1.2 关键设计点

| 设计点 | 说明 |
|--------|------|
| **`splitView={false}`** | 统一视图（unified），与 `git diff` 输出一致 |
| **`DiffMethod.WORDS`** | 单词级别 diff，行内高亮显示具体变化 |
| **`renderContent`** | 自定义渲染器，将每一行用 Prism 语法高亮 |
| **CSS Variables** | 主题颜色全部走 CSS 变量，天然支持亮/暗主题 |

### 3.2 WorkspaceDiffSurface（Unified 自渲染版本）

**文件**: [desktop/src/components/workspace/WorkspaceCodeSurface.tsx](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/desktop/src/components/workspace/WorkspaceCodeSurface.tsx)

```tsx
export function WorkspaceDiffSurface({
  value,    // Unified Diff 文本
  path,     // 文件路径
  className,
  lineLimit = 2000,
}) {
  const [showAllLines, setShowAllLines] = useState(false)
  const lines = value.split('\n')
  const visibleLines = showAllLines ? lines : lines.slice(0, lineLimit)
  const language = getLanguageFromPath(path)

  return (
    <div className={className}>
      <pre className="m-0 font-mono text-xs leading-[1.55]">
        {visibleLines.map((line, index) => {
          // 解析 diff 行
          const isFileHeader = line.startsWith('diff --') 
                            || line.startsWith('--- ') 
                            || line.startsWith('+++ ')
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
                isAdded   ? 'bg-[var(--color-diff-added-bg)]'
                : isRemoved ? 'bg-[var(--color-diff-removed-bg)]'
                : isHunk    ? 'bg-[var(--color-diff-highlight-bg)]'
                           : 'hover:bg-[var(--color-surface-hover)]'
              }`}
            >
              <span className="select-none text-right text-[11px]">
                {index + 1}
              </span>
              <span className={`select-none text-center ${
                isAdded   ? 'text-[var(--color-diff-added-text)]'
                : isRemoved ? 'text-[var(--color-diff-removed-text)]'
                           : 'text-[var(--color-text-tertiary)]'
              }`}>
                {prefix}
              </span>
              <span className="whitespace-pre pr-6">
                {isCodeLine 
                  ? <InlineHighlightedCode value={code} language={language} />
                  : code || ' '
                }
              </span>
            </div>
          )
        })}
      </pre>
      
      {lines.length > lineLimit && (
        <div className="sticky bottom-0 ...">
          <span>显示前 {visibleLines.length} 行 / 共 {lines.length} 行</span>
          <button onClick={() => setShowAllLines(!showAllLines)}>
            {showAllLines ? '折叠' : '显示全部'}
          </button>
        </div>
      )}
    </div>
  )
}
```

#### 3.2.1 Diff 行类型识别

| 标识 | 含义 | 颜色 |
|------|------|------|
| `diff --git a/... b/...` | 文件头 | text-secondary |
| `--- a/file` | 旧文件名 | text-secondary |
| `+++ b/file` | 新文件名 | text-secondary |
| `@@ -1,3 +1,3 @@` | Hunk 头 | warning |
| `+ 内容` | 新增行 | added (绿) |
| `- 内容` | 删除行 | removed (红) |
| `  内容` | 上下文行 | 默认 |

#### 3.2.2 行数限制

```typescript
export const WORKSPACE_PREVIEW_LINE_LIMIT = 2000
export const WORKSPACE_PLAIN_TEXT_LINE_THRESHOLD = 5000
```

超过 2000 行的 diff 会显示"显示全部"按钮，超过 5000 行时使用纯文本渲染（避免 Prism 卡顿）。

---

## 四、Diff 后端服务实现

### 4.1 getDiff 入口

**文件**: [src/server/services/workspaceService.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/server/services/workspaceService.ts)

```typescript
async getDiff(
  sessionId: string,
  filePath: string,
): Promise<WorkspaceDiffResult> {
  // 1. 解析路径（确保在工作区内）
  let resolvedPath: WorkspacePathResolution
  try {
    resolvedPath = await this.resolveWorkspacePath(sessionId, filePath)
  } catch (error) {
    return {
      state: 'error',
      path: this.normalizeRequestedPath(filePath),
      error: error instanceof Error ? error.message : String(error),
    }
  }

  // 2. 优先使用 Session 内的变更（来自工具调用历史）
  const sessionDiff = await this.getSessionDiff(sessionId, resolvedPath.relativePath)
  if (sessionDiff) {
    return { state: 'ok', path: resolvedPath.relativePath, diff: sessionDiff }
  }

  // 3. 次选：File History 备份（上一轮写入前的状态）
  const fileHistoryDiff = await this.getFileHistoryDiff(
    sessionId, resolvedPath.workspaceRoot, resolvedPath.relativePath,
  )
  if (fileHistoryDiff) {
    return { state: 'ok', path: resolvedPath.relativePath, diff: fileHistoryDiff }
  }

  // 4. 最后：调用 git diff HEAD
  const repoInfo = await this.getGitRepoInfo(resolvedPath.workspaceRoot)
  if (repoInfo.kind === 'not_git_repo') {
    return { state: 'not_git_repo', path: resolvedPath.relativePath }
  }
  // ... 检查 status、untracked、modified，最终调用 git diff
}
```

### 4.2 三级 Diff 来源

#### Level 1: Session Diff（对话历史中的工具调用）

**文件**: `src/server/services/workspaceService.ts`

```typescript
private async getSessionDiff(
  sessionId: string,
  relativePath: string,
): Promise<string | null> {
  const workDir = await this.requireWorkDir(sessionId)
  const changes = await this.getSessionFileChanges(sessionId, workDir)
  const change = changes.find((entry) => entry.path === relativePath)
  if (!change) return null
  if (change.diff?.trim()) return change.diff

  // 兜底：当前文件内容 - 空内容
  const file = await this.readFile(sessionId, relativePath)
  if (file.state !== 'ok' || file.previewType === 'image') return null
  return this.buildSyntheticDiff('/dev/null', relativePath, '', file.content)
}

private async getSessionFileChanges(
  sessionId: string, workspaceRoot: string,
): Promise<SessionFileChange[]> {
  const messages = await this.resolveSessionMessages(sessionId)
  const changes = new Map<string, SessionFileChange>()

  for (const message of messages) {
    if (message.type !== 'tool_use' || !Array.isArray(message.content)) continue

    for (const block of message.content) {
      const record = block as Record<string, unknown>
      if (record.type !== 'tool_use' || typeof record.name !== 'string') continue
      const input = record.input as Record<string, unknown>

      for (const change of this.extractSessionChangesFromTool(
        record.name, input, workspaceRoot,
      )) {
        // 合并同一文件的多次变更
        const existing = changes.get(change.path)
        if (!existing) {
          changes.set(change.path, change)
        } else {
          changes.set(change.path, {
            ...existing,
            additions: existing.additions + change.additions,
            deletions: existing.deletions + change.deletions,
            diff: [existing.diff, change.diff].filter(Boolean).join('\n'),
          })
        }
      }
    }
  }

  return [...changes.values()]
}
```

**支持的工具变更**：

| 工具 | 提取方式 |
|------|----------|
| `Write` | `file_path` + `content` |
| `Edit` | `file_path` + `old_string` + `new_string` |
| `MultiEdit` | `file_path` + `edits[]` |
| `NotebookEdit` | `notebook_path` + `old_source` + `new_source` |
| `apply_patch` | 解析 patch 格式 |

#### Level 2: File History Diff

```typescript
private async getFileHistoryDiff(
  sessionId: string, workspaceRoot: string, relativePath: string,
): Promise<string | null> {
  const changes = await this.getFileHistoryChanges(sessionId, workspaceRoot)
  return changes.find((c) => c.path === relativePath)?.diff ?? null
}

private async getFileHistoryChanges(
  sessionId: string, workspaceRoot: string,
): Promise<SessionFileChange[]> {
  const snapshots = await this.resolveSessionFileHistorySnapshots(sessionId)
  if (snapshots.length === 0) return []

  const changes: SessionFileChange[] = []
  const trackedPaths = this.collectFileHistoryTrackedPaths(snapshots)

  for (const trackingPath of trackedPaths) {
    const relativePath = this.resolveFileHistoryRelativePath(trackingPath, workspaceRoot)
    if (!relativePath) continue

    // 读取最早的备份（写入文件前的内容）
    const beforeContent = await this.readFileHistoryBackupContent(
      sessionId, this.getEarliestFileHistoryBackupName(trackingPath, snapshots),
    )
    if (beforeContent === undefined) continue

    const afterContent = await this.readTextFileOrNull(
      path.resolve(workspaceRoot, relativePath),
    )
    if (beforeContent === afterContent) continue

    const stats = this.countDiffStats(beforeContent ?? '', afterContent ?? '')
    changes.push({
      path: relativePath,
      status: beforeContent === null ? 'added'
            : afterContent === null ? 'deleted'
            : 'modified',
      additions: stats.additions,
      deletions: stats.deletions,
      diff: this.buildSyntheticDiff(
        beforeContent === null ? '/dev/null' : relativePath,
        afterContent === null ? '/dev/null' : relativePath,
        beforeContent ?? '', afterContent ?? '',
      ),
    })
  }

  return changes
}
```

**File History 存储位置**：

```
~/.claude/file-history/
  └── {sessionId}/
      ├── v1-{hash1}-{filename}.backup
      ├── v2-{hash2}-{filename}.backup
      └── ...
```

#### Level 3: Git Diff

```typescript
private async runGitDiff(
  workDir: string, relativePath: string,
): Promise<{ kind: 'ok'; diff: string } | { kind: 'error'; message: string }> {
  const result = await this.runGit(workDir, [
    'diff',
    '--no-ext-diff',     // 不使用外部 diff 工具
    '--binary',          // 支持二进制文件
    '--find-renames',    // 检测重命名
    '--find-copies',     // 检测复制
    'HEAD',              // 与最新提交对比
    '--',                // 路径分隔
    relativePath,
  ])

  if (result.code !== 0) {
    return { kind: 'error', message: this.formatGitError(...) }
  }
  return { kind: 'ok', diff: result.stdout }
}
```

### 4.3 合成 Diff（无 git 时）

```typescript
private buildSyntheticDiff(
  oldPath: string, newPath: string,
  oldContent: string, newContent: string,
): string {
  const oldLines = oldContent ? oldContent.split('\n') : []
  const newLines = newContent ? newContent.split('\n') : []
  if (oldLines.at(-1) === '') oldLines.pop()
  if (newLines.at(-1) === '') newLines.pop()

  return [
    `diff --session ${
      oldPath === '/dev/null' ? '/dev/null' : `a/${oldPath}`
    } ${
      newPath === '/dev/null' ? '/dev/null' : `b/${newPath}`
    }`,
    `--- ${oldPath === '/dev/null' ? '/dev/null' : `a/${oldPath}`}`,
    `+++ ${newPath === '/dev/null' ? '/dev/null' : `b/${newPath}`}`,
    `@@ -1,${oldLines.length} +1,${newLines.length} @@`,
    ...oldLines.map((line) => `-${line}`),
    ...newLines.map((line) => `+${line}`),
  ].join('\n')
}
```

### 4.4 Git 状态检测（列出所有变更文件）

```typescript
private async getStatusEntries(repoRoot: string): Promise<...> {
  const result = await this.runGit(repoRoot, [
    'status', '--porcelain', '-uall', '--no-renames', '-z',
  ])
  // 解析 porcelain v1 -z 输出
  // XY filename (XY = 2 char status)
  // X = index status, Y = worktree status
  // ?? = untracked
  // ... 
}
```

---

## 五、版本历史（Turn Checkpoint）实现

### 5.1 Checkpoint 数据结构

**文件**: [src/server/services/sessionRewindService.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/server/services/sessionRewindService.ts)

```typescript
type FileHistorySnapshot = {
  trackedFileBackups: Record<string, {
    backupFileName: string | null   // null = 文件原本不存在
  }>
}

type SessionTurnCheckpointPreview = {
  target: {
    targetUserMessageId: string
    userMessageIndex: number
    userMessageCount: number
  }
  conversation: { messagesRemoved: number }
  code: {
    available: boolean
    filesChanged: Array<{
      trackingPath: string
      backupFileName: string | null
      displayPath: string
    }>
    insertions: number
    deletions: number
  }
  workDir: string
}
```

### 5.2 Turn Checkpoint 列表 API

```typescript
export async function listSessionTurnCheckpoints(
  sessionId: string,
): Promise<SessionTurnCheckpointPreview[]> {
  const activeMessages = await sessionService.getSessionMessages(sessionId)
  const userMessages = activeMessages.filter((m) => m.type === 'user')
  if (userMessages.length === 0) return []

  const workDir = await resolveSessionWorkDir(sessionId)
  const snapshots = await loadFileHistorySnapshots(sessionId)
  const checkpoints: SessionTurnCheckpointPreview[] = []

  for (const [userMessageIndex, userMessage] of userMessages.entries()) {
    const activeMessageIndex = activeMessages.findIndex((m) => m.id === userMessage.id)
    if (activeMessageIndex < 0) continue
    if (!hasCompletedTurn(activeMessages, userMessage.id)) continue

    // 1. 构造 target
    const target: RewindTarget = {
      targetUserMessageId: userMessage.id,
      userMessageIndex,
      userMessageCount: userMessages.length,
      messagesRemoved: activeMessages.length - activeMessageIndex,
    }

    // 2. 解析该 turn 的工作目录
    const checkpointBaseDir = await resolveCheckpointBaseDir(
      sessionId, target.targetUserMessageId, workDir,
    )

    // 3. 找到该 turn 前后的快照
    const targetSnapshot = snapshots
      ? findTargetSnapshot(snapshots, target.targetUserMessageId) : null
    const nextUserMessageId = getNextUserMessageId(userMessages, userMessageIndex)
    const nextSnapshot = nextUserMessageId && snapshots
      ? findTargetSnapshot(snapshots, nextUserMessageId) : null

    // 4. 计算此 turn 的代码变更
    const checkpointPreview = targetSnapshot
      ? await buildTurnCodePreview(sessionId, checkpointBaseDir, targetSnapshot, nextSnapshot)
      : null
    const preview = checkpointPreview?.available && checkpointPreview.filesChanged.length > 0
      ? checkpointPreview
      : buildTranscriptTurnCodePreview(activeMessages, target.targetUserMessageId, checkpointBaseDir)

    if (!preview.available || preview.filesChanged.length === 0) continue
    checkpoints.push(buildTurnPreview(target, preview, checkpointBaseDir))
  }

  return checkpoints
}
```

### 5.3 Turn Checkpoint Diff API

```typescript
export async function getSessionTurnCheckpointDiff(
  sessionId: string,
  selector: RewindTargetSelector,
  requestedPath: string,
): Promise<SessionTurnCheckpointDiffResult> {
  const target = await resolveRewindTarget(sessionId, selector)
  const workDir = await resolveSessionWorkDir(sessionId)
  const checkpointBaseDir = await resolveCheckpointBaseDir(
    sessionId, target.targetUserMessageId, workDir,
  )

  const activeMessages = await sessionService.getSessionMessages(sessionId)
  const snapshots = await loadFileHistorySnapshots(sessionId)

  // 1. 尝试从 transcript 推断 diff
  const transcriptChange = findTranscriptTurnDiff(
    activeMessages, target.targetUserMessageId, checkpointBaseDir, requestedPath,
  )
  const transcriptResult = transcriptChange?.diff ? { ... } : null

  if (!snapshots) return transcriptResult ?? missingResult

  // 2. 加载此 turn 的目标快照和下一 turn 的快照
  const targetSnapshot = findTargetSnapshot(snapshots, target.targetUserMessageId)
  if (!targetSnapshot) return transcriptResult ?? missingResult

  const userMessages = activeMessages.filter((m) => m.type === 'user')
  const nextUserMessageId = getNextUserMessageId(userMessages, target.userMessageIndex)
  const nextSnapshot = nextUserMessageId
    ? findTargetSnapshot(snapshots, nextUserMessageId) : null

  // 3. 遍历该 turn 修改过的所有文件
  for (const trackingPath of new Set([
    ...Object.keys(targetSnapshot.trackedFileBackups),
    ...Object.keys(nextSnapshot?.trackedFileBackups ?? {}),
  ])) {
    if (!matchesCheckpointPath(requestedPath, trackingPath, checkpointBaseDir)) {
      continue
    }

    const displayPath = toCheckpointResponsePath(trackingPath, checkpointBaseDir)

    try {
      const { beforeContent, afterContent } = await getTurnBoundaryContents(
        sessionId, checkpointBaseDir, trackingPath, targetSnapshot, nextSnapshot,
      )

      if (beforeContent === afterContent) {
        return { ...missingResult, path: displayPath }
      }

      return {
        target: missingResult.target,
        workDir: checkpointBaseDir,
        path: displayPath,
        state: 'ok',
        diff: buildCheckpointDiff(
          displayPath,
          beforeContent ?? '',
          afterContent ?? '',
          beforeContent !== null,   // oldExists
          afterContent !== null,    // newExists
        ),
      }
    } catch (error) {
      return { ... }
    }
  }

  return transcriptResult ?? missingResult
}
```

### 5.4 构建标准 Unified Diff

```typescript
import { createTwoFilesPatch } from 'diff'

function buildCheckpointDiff(
  displayPath: string,
  oldContent: string,
  newContent: string,
  oldExists: boolean,
  newExists: boolean,
): string {
  const oldFileName = oldExists ? `a/${displayPath}` : '/dev/null'
  const newFileName = newExists ? `b/${displayPath}` : '/dev/null'

  return createTwoFilesPatch(
    oldFileName,
    newFileName,
    oldContent,
    newContent,
    '',  // oldHeader
    '',  // newHeader
    { context: 3 },  // 上下文行数
  )
}
```

### 5.5 Rewind（回滚）实现

```typescript
export async function executeSessionRewind(
  sessionId: string,
  selector: RewindTargetSelector,
): Promise<SessionRewindExecuteResult> {
  const target = await resolveRewindTarget(sessionId, selector)
  const workDir = await resolveSessionWorkDir(sessionId)
  const checkpointBaseDir = await resolveCheckpointBaseDir(
    sessionId, target.targetUserMessageId, workDir,
  )
  const { snapshots, preview } = await buildCodePreview(
    sessionId, checkpointBaseDir, target.targetUserMessageId,
  )

  // 1. 停止当前会话
  await conversationService.stopSessionAndWait(sessionId)

  if (preview.available && snapshots) {
    const targetSnapshot = findTargetSnapshot(snapshots, target.targetUserMessageId)
    if (!targetSnapshot) throw new ApiError(...)

    // 2. 恢复所有被修改的文件
    for (const trackingPath of collectTrackedPaths(snapshots)) {
      const backupFileName = getBackupFileNameForTarget(
        trackingPath, snapshots, targetSnapshot,
      )
      if (backupFileName === undefined) continue

      const absolutePath = expandTrackingPath(checkpointBaseDir, trackingPath)

      if (backupFileName === null) {
        // 原本不存在 → 删除当前文件
        try { await unlink(absolutePath) } 
        catch (e) { if (e.code !== 'ENOENT') throw e }
        continue
      }

      // 用备份覆盖当前文件
      await restoreBackupFile(
        absolutePath, resolveBackupPath(sessionId, backupFileName),
      )
    }
  }

  // 3. 裁剪消息历史
  const trimResult = await sessionService.trimSessionMessagesFrom(
    sessionId, target.targetUserMessageId,
  )

  return { target, messagesTrimmed: trimResult.removed }
}
```

---

## 六、API 路由定义

### 6.1 后端路由 (Bun.serve)

**文件**: [src/server/api/sessions.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/server/api/sessions.ts)

```typescript
// GET /api/sessions/:id/workspace/status
// GET /api/sessions/:id/workspace/tree?path=
// GET /api/sessions/:id/workspace/file?path=
// GET /api/sessions/:id/workspace/diff?path=
// GET /api/sessions/:id/turn-checkpoints
// GET /api/sessions/:id/turn-checkpoints/diff?targetUserMessageId=&path=
// POST /api/sessions/:id/rewind

case 'workspace':
  switch (workspaceResource) {
    case 'status': return Response.json(await workspaceService.getStatus(sessionId))
    case 'tree':   return await runWorkspaceRequest(() => workspaceService.readTree(...))
    case 'file':   return await runWorkspaceRequest(() => workspaceService.readFile(...))
    case 'diff':   return await runWorkspaceDiffRequest(() => workspaceService.getDiff(...))
  }
```

### 6.2 前端 API 客户端

**文件**: [desktop/src/api/sessions.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/desktop/src/api/sessions.ts)

```typescript
export const sessionsApi = {
  // 工作区状态
  getStatus(sessionId: string) {
    return api.get<WorkspaceStatusResult>(
      buildWorkspacePath(sessionId, 'status')
    )
  },

  // 文件树
  getTree(sessionId: string, path?: string) {
    return api.get<WorkspaceTreeResult>(
      buildWorkspacePath(sessionId, 'tree', path)
    )
  },

  // 文件内容
  getFile(sessionId: string, path: string) {
    return api.get<WorkspaceReadFileResult>(
      buildWorkspacePath(sessionId, 'file', path)
    )
  },

  // 单文件 diff
  getDiff(sessionId: string, path: string) {
    return api.get<WorkspaceDiffResult>(
      buildWorkspacePath(sessionId, 'diff', path)
    )
  },

  // Turn Checkpoint 列表
  getTurnCheckpoints(sessionId: string) {
    return api.get<SessionTurnCheckpointsResponse>(
      `/api/sessions/${sessionId}/turn-checkpoints`
    )
  },

  // 绑定到 checkpoint 的单文件 diff
  getTurnCheckpointDiff(
    sessionId: string, 
    params: { targetUserMessageId?: string; userMessageIndex?: number; path: string }
  ) {
    const query = new URLSearchParams()
    if (params.targetUserMessageId) query.set('targetUserMessageId', params.targetUserMessageId)
    if (params.userMessageIndex !== undefined) 
      query.set('userMessageIndex', String(params.userMessageIndex))
    query.set('path', params.path)
    return api.get<TurnCheckpointDiffResult>(
      `/api/sessions/${sessionId}/turn-checkpoints/diff?${query.toString()}`
    )
  },

  // 执行回滚
  rewind(sessionId: string, body: RewindRequest) {
    return api.post<SessionRewindResponse>(
      `/api/sessions/${sessionId}/rewind`, body,
    )
  },
}
```

---

## 七、颜色变量（CSS Theme）

```css
:root {
  /* Diff 行背景 */
  --color-diff-added-bg: rgba(16, 185, 129, 0.12);     /* 绿：新增 */
  --color-diff-removed-bg: rgba(239, 68, 68, 0.12);    /* 红：删除 */
  --color-diff-highlight-bg: rgba(245, 158, 11, 0.10); /* 黄：hunk 头 */
  
  /* Diff 文字颜色 */
  --color-diff-added-text: rgb(16, 185, 129);
  --color-diff-removed-text: rgb(239, 68, 68);
  
  /* 行内高亮（单词级 diff） */
  --color-diff-added-word: rgba(16, 185, 129, 0.35);
  --color-diff-removed-word: rgba(239, 68, 68, 0.35);
  
  /* Gutter（行号列） */
  --color-diff-added-gutter: rgba(16, 185, 129, 0.20);
  --color-diff-removed-gutter: rgba(239, 68, 68, 0.20);
  --color-diff-highlight-gutter: rgba(245, 158, 11, 0.18);
}

[data-theme='dark'] {
  --color-diff-added-bg: rgba(16, 185, 129, 0.18);
  --color-diff-removed-bg: rgba(239, 68, 68, 0.18);
  --color-diff-added-word: rgba(16, 185, 129, 0.40);
  --color-diff-removed-word: rgba(239, 68, 68, 0.40);
}
```

---

## 八、关键依赖

### 8.1 前端

```json
{
  "dependencies": {
    "react-diff-viewer-continued": "^4.0.0",
    "prism-react-renderer": "^2.4.0",
    "lucide-react": "^0.300.0"
  },
  "devDependencies": {
    "@types/diff": "^5.0.0"
  }
}
```

### 8.2 后端

```json
{
  "dependencies": {
    "diff": "^5.2.0"      // diffLines, createTwoFilesPatch
  }
}
```

---

## 九、复刻 AI 提示词（可直接复制使用）

以下是一份完整的提示词，可在新的项目中让 AI 复刻本项目的 Diff 视图与版本历史功能：

```text
你需要在一个新项目中复刻 cc-haha 项目的"代码 Diff 视图 + 版本历史"两大功能。

## 一、技术栈要求

- 前端：React + TypeScript + Tailwind CSS
- 后端：Node.js（Bun 优先）或 Express
- 必备依赖：
  - `react-diff-viewer-continued`（Diff 渲染）
  - `prism-react-renderer`（代码高亮）
  - `diff`（Node 端 diff 算法）
  - `zustand`（状态管理）

## 二、核心功能要求

### 功能 1：单文件 Diff 视图

#### 前端
1. 创建 `DiffViewer` 组件，接收 `{ filePath, oldString, newString }` 三个参数。
2. 头部显示：文件路径 + `+N` 新增统计 + `-N` 删除统计（用胶囊样式）。
3. 使用 `ReactDiffViewer`，设置 `splitView={false}`（统一视图），`compareMethod={DiffMethod.WORDS}`（单词级）。
4. 自定义 `renderContent`：用 `prism-react-renderer` 高亮每行代码。
5. 根据文件扩展名推断语言（ts/tsx/js/jsx/py/rs/go/md/json/yaml/html/css/sh）。

#### CSS 变量
```css
:root {
  --color-diff-added-bg: rgba(16, 185, 129, 0.12);
  --color-diff-removed-bg: rgba(239, 68, 68, 0.12);
  --color-diff-added-text: rgb(16, 185, 129);
  --color-diff-removed-text: rgb(239, 68, 68);
  --color-diff-added-word: rgba(16, 185, 129, 0.35);
  --color-diff-removed-word: rgba(239, 68, 68, 0.35);
}
```

### 功能 2：版本历史（Turn Checkpoint）

#### 数据模型
每个用户消息对应一个 checkpoint，记录此 turn 前后的文件状态：

```typescript
type FileHistorySnapshot = {
  trackedFileBackups: Record<string, {
    backupFileName: string | null   // null = 文件原本不存在
  }>
}

type TurnCheckpoint = {
  target: {
    targetUserMessageId: string
    userMessageIndex: number
    userMessageCount: number
  }
  code: {
    available: boolean
    filesChanged: Array<{
      trackingPath: string
      backupFileName: string | null
      displayPath: string
    }>
    insertions: number
    deletions: number
  }
  workDir: string
}
```

#### 存储
- 位置：`~/.{yourApp}/file-history/{sessionId}/{backupFileName}`
- 备份时机：每次工具调用（Write/Edit/MultiEdit）修改文件前，复制原文件到 `file-history` 目录。
- 快照时机：每个用户消息开始时，记录当前所有跟踪文件的 backup 引用。

#### 后端 API（必须实现 4 个）

1. `GET /api/sessions/:id/turn-checkpoints`
   - 返回所有 checkpoint 列表

2. `GET /api/sessions/:id/turn-checkpoints/diff?targetUserMessageId=&path=`
   - 返回该 checkpoint 中指定文件的 Unified Diff

3. `GET /api/sessions/:id/workspace/diff?path=`
   - 返回当前未提交变更的 diff

4. `POST /api/sessions/:id/rewind`
   - 回滚到指定 checkpoint（恢复所有文件 + 裁剪消息）

#### Diff 来源优先级

对单文件 diff 请求，按以下优先级返回：
1. **Session Diff**：从消息历史中提取该文件的 Write/Edit 工具调用，构建合成 diff
2. **File History Diff**：对比 `file-history` 中的备份内容与当前文件内容
3. **Git Diff**：调用 `git diff HEAD -- <path>`（仅当工作区是 git 仓库）

合成 diff 格式（无 git 时使用）：
```
diff --session a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1,{oldCount} +1,{newCount} @@
-{old line}
+{new line}
```

#### 构建标准 Unified Diff

```typescript
import { createTwoFilesPatch } from 'diff'

function buildCheckpointDiff(
  displayPath: string,
  oldContent: string,
  newContent: string,
  oldExists: boolean,
  newExists: boolean,
): string {
  const oldFileName = oldExists ? `a/${displayPath}` : '/dev/null'
  const newFileName = newExists ? `b/${displayPath}` : '/dev/null'
  return createTwoFilesPatch(
    oldFileName, newFileName,
    oldContent, newContent,
    '', '', { context: 3 }
  )
}
```

### 功能 3：工作区状态

#### API
`GET /api/sessions/:id/workspace/status`

#### 返回
```typescript
type WorkspaceStatusResult = {
  state: 'ok' | 'not_git_repo' | 'missing_workdir' | 'error'
  workDir: string
  repoName: string | null
  branch: string | null
  isGitRepo: boolean
  changedFiles: Array<{
    path: string
    oldPath?: string
    status: 'modified' | 'added' | 'deleted' | 'renamed' 
          | 'untracked' | 'copied' | 'type_changed' | 'unknown'
    additions: number
    deletions: number
  }>
}
```

#### 实现
1. 调用 `git rev-parse --is-inside-work-tree` 判断是否 git 仓库
2. 调用 `git rev-parse --abbrev-ref HEAD` 获取分支
3. 调用 `git status --porcelain -uall --no-renames -z` 获取所有变更文件
4. 对每个 modified 文件调用 `git diff --numstat HEAD -- <path>` 获取 +/- 行数
5. 对每个 untracked 文件读取实际内容并调用 `diffLines('', content)` 统计

## 三、UI 要求

1. **文件变更列表**：左侧栏显示所有变更文件，按字母排序，按状态显示徽章：
   - `M` (modified) 黄色
   - `A` (added) 绿色
   - `D` (deleted) 红色
   - `R` (renamed) 蓝色
   - `U` (untracked) 灰色

2. **多 Tab 预览**：右侧支持多 Tab 切换，Tab 类型分 file / diff 两种。

3. **Diff 视图头部**：
   - 文件路径（等宽字体）
   - `+N` / `-N` 计数胶囊
   - "Copy path" 按钮

4. **行号列**：左侧 40px，显示行号（仅在 WorkspaceDiffSurface 中显示）。

5. **大文件处理**：超过 2000 行时显示"显示全部"按钮，超过 5000 行使用纯文本渲染。

## 四、复刻步骤

1. 安装依赖：`npm install react-diff-viewer-continued prism-react-renderer lucide-react diff zustand`
2. 创建 `src/types/diff.ts` 定义所有类型
3. 创建 `src/services/workspaceService.ts`（后端）实现 `getDiff`, `getStatus`, `getFile`
4. 创建 `src/services/sessionRewindService.ts`（后端）实现 `listSessionTurnCheckpoints`, `getSessionTurnCheckpointDiff`, `executeSessionRewind`
5. 创建 `src/components/DiffViewer.tsx`（前端）实现单文件 diff 渲染
6. 创建 `src/components/WorkspaceDiffSurface.tsx`（前端）实现 unified diff 自渲染
7. 创建 `src/components/WorkspacePanel.tsx`（前端）组合文件树、变更列表、预览
8. 实现 file-history 备份机制：在工具执行前调用 `backupFile(sessionId, path)`
9. 实现消息快照机制：每个用户消息开始时调用 `createFileHistorySnapshot(sessionId)`
10. 添加 CSS 变量到 `globals.css`

## 五、避坑要点

1. **`+++ b/file` 误判**：判断新增行时必须 `!line.startsWith('+++')`，否则文件头会被当成代码行。
2. **Unified Diff 末尾空行**：`split('\n')` 后末尾会有空字符串，记得 `pop()` 掉。
3. **行号溢出**：超过 2000 行的 diff 渲染会卡顿，必须分页或纯文本渲染。
4. **大文件统计**：不要对 untracked 文件调用 `git diff`，要直接读取并用 `diffLines` 统计。
5. **跨平台路径**：在 `matchesCheckpointPath` 中要 `normalize` Windows 反斜杠为正斜杠。
6. **File History 损坏**：备份文件可能因用户清理而丢失，要 fallback 到 Git Diff。
7. **Diff 上下文行数**：使用 `{ context: 3 }` 而非默认 4，更接近标准 git diff 输出。

请按以上要求完整复刻该功能。
```

---

## 十、关键文件索引

| 文件 | 角色 |
|------|------|
| [desktop/src/components/chat/DiffViewer.tsx](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/desktop/src/components/chat/DiffViewer.tsx) | 单文件 diff 渲染（含语法高亮） |
| [desktop/src/components/workspace/WorkspaceCodeSurface.tsx](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/desktop/src/components/workspace/WorkspaceCodeSurface.tsx) | Unified diff 自渲染（不依赖第三方） |
| [desktop/src/components/workspace/WorkspacePanel.tsx](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/desktop/src/components/workspace/WorkspacePanel.tsx) | 主面板（文件树 + 变更列表 + 预览 Tabs） |
| [desktop/src/api/sessions.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/desktop/src/api/sessions.ts) | 前端 API 客户端（5 个 diff/checkpoint 端点） |
| [src/server/services/workspaceService.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/server/services/workspaceService.ts) | 后端工作区服务（getDiff, getStatus） |
| [src/server/services/sessionRewindService.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/server/services/sessionRewindService.ts) | 后端 Rewind 服务（Checkpoint 列表、Diff、回滚） |
| [src/server/api/sessions.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/server/api/sessions.ts) | API 路由注册 |

---

*最后更新：2026-06-03*
