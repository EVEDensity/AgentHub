# Function Calling 实现文档

> 本文档详细阐述 cc-haha 项目中 Function Calling（工具调用）系统的实现原理、架构设计与核心代码逻辑。

---

## 一、概述

Function Calling 是 Claude Code 与 AI 模型交互的核心机制，允许 AI 模型在对话过程中调用预定义的工具（如读写文件、执行命令、搜索等）来完成实际任务。

### 1.1 设计目标

- **流式执行**: 工具随 API 响应流实时执行，无需等待完整响应
- **并发控制**: 支持工具并发执行，同时保证非安全工具的独占性
- **权限管理**: 统一的权限校验机制，防止恶意操作
- **错误隔离**: 单个工具错误不会影响其他工具的执行

---

## 二、整体架构

### 2.1 架构图

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              Function Calling 架构                            │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│   ┌─────────────┐    ┌──────────────┐    ┌─────────────────┐                 │
│   │  用户输入   │ →  │ QueryEngine  │ →  │   API Request   │                 │
│   └─────────────┘    └──────────────┘    └─────────────────┘                 │
│                                                      │                        │
│                                     ┌─────────────────┘                        │
│                                     ↓                                          │
│                          ┌─────────────────────────┐                          │
│                          │      AI Response        │                          │
│                          │     (tool_use blocks)   │                          │
│                          └─────────────────────────┘                          │
│                                     │                                          │
│                                     ↓                                          │
│   ┌─────────────────────────────────────────────────────────────────────────┐ │
│   │                    StreamingToolExecutor                                 │ │
│   │  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐                       │ │
│   │  │ Tool 1 │  │ Tool 2 │  │ Tool 3 │  │ Tool 4 │  ...                    │ │
│   │  │  执行  │  │  等待  │  │  执行  │  │  等待  │                          │ │
│   │  └────────┘  └────────┘  └────────┘  └────────┘                          │ │
│   │                                                                       │ │
│   │  并发安全工具可并行 │ 非安全工具需独占 │ 错误自动取消同级工具      │ │
│   └─────────────────────────────────────────────────────────────────────────┘ │
│                                     │                                          │
│                        ┌────────────┴────────────┐                           │
│                        ↓                          ↓                           │
│              ┌─────────────────┐       ┌─────────────────┐                   │
│              │   tool_result   │       │   tool_result   │                   │
│              │     (成功)       │       │     (失败)       │                   │
│              └─────────────────┘       └─────────────────┘                   │
│                                     │                                          │
│                                     ↓                                          │
│                          ┌─────────────────────────┐                          │
│                          │  继续对话 / 结束回合    │                          │
│                          └─────────────────────────┘                          │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 核心模块关系

| 模块 | 文件位置 | 职责 |
|------|----------|------|
| `Tool` | `src/Tool.ts` | 工具定义接口与类型 |
| `QueryEngine` | `src/QueryEngine.ts` | 查询引擎，管理对话生命周期 |
| `StreamingToolExecutor` | `src/services/tools/StreamingToolExecutor.ts` | 流式工具执行器 |
| `toolExecution` | `src/services/tools/toolExecution.ts` | 工具执行核心逻辑 |
| `toolOrchestration` | `src/services/tools/toolOrchestration.ts` | 工具编排与编排策略 |
| `query.ts` | `src/query.ts` | 主查询循环，协调整个流程 |

---

## 三、核心组件详解

### 3.1 Tool.ts — 工具定义框架

**文件**: `src/Tool.ts`

```typescript
export type Tool = {
  // 基础属性
  name: string                           // 工具唯一名称
  description: string                    // 工具描述，供 AI 模型理解用途
  category?: string                      // 工具分类（read/write/edit/search/agent）

  // Schema 定义
  inputSchema: z.ZodSchema               // 输入参数 Zod 验证 schema
  outputSchema?: z.ZodSchema            // 输出参数 Zod 验证 schema

  // 核心方法
  prompt: (context: ToolPromptContext) => Promise<string>
  execute: (
    input: z.infer<z.ZodSchema>,
    context: ToolUseContext
  ) => Promise<ToolResult | AsyncGenerator<ToolResult | ToolProgressData>>

  // 执行控制
  isConcurrencySafe?: (input: unknown) => boolean  // 是否可并发执行
  isUserVisible?: boolean                          // 是否对用户可见
  isInternal?: boolean                             // 是否为内部工具
  isExperimental?: boolean                         // 是否为实验性工具
  requiresNetwork?: boolean                        // 是否需要网络

  // 中断行为
  interruptBehavior?: () => 'cancel' | 'block'

  // 示例与文档
  exampleUsage?: string
  hidden?: boolean
}
```

**ToolPromptContext**:
```typescript
type ToolPromptContext = {
  getToolPermissionContext: () => ToolPermissionContext
  tools: Tools
  agents: AgentDefinition[]
}
```

**ToolUseContext**:
```typescript
type ToolUseContext = {
  options: {
    commands: Command[]
    debug: boolean
    mainLoopModel: string
    tools: Tools
    verbose: boolean
    thinkingConfig: ThinkingConfig
    mcpClients: MCPServerConnection[]
    // ... 其他选项
  }
  abortController: AbortController
  readFileState: FileStateCache
  getAppState(): AppState
  setAppState(f: (prev: AppState) => AppState): void
  setInProgressToolUseIDs: (f: (prev: Set<string>) => Set<string>) => void
  setResponseLength: (f: (prev: number) => number) => void
  // ... 其他上下文
}
```

**ToolPermissionContext**:
```typescript
export type ToolPermissionContext = DeepImmutable<{
  mode: PermissionMode                    // 'default' | 'bypass' | 'auto'
  additionalWorkingDirectories: Map<string, AdditionalWorkingDirectory>
  alwaysAllowRules: ToolPermissionRulesBySource   // 始终允许的规则
  alwaysDenyRules: ToolPermissionRulesBySource    // 始终拒绝的规则
  alwaysAskRules: ToolPermissionRulesBySource     // 始终询问的规则
  isBypassPermissionsModeAvailable: boolean
  isAutoModeAvailable?: boolean
  prePlanMode?: PermissionMode
}>
```

---

### 3.2 StreamingToolExecutor — 流式工具执行器

**文件**: `src/services/tools/StreamingToolExecutor.ts`

#### 3.2.1 核心职责

- 管理工具执行队列
- 控制并发执行策略
- 实时处理 API 流式响应中的 tool_use 块
- 收集和缓冲工具执行结果

#### 3.2.2 状态机

```
┌─────────┐    addTool()     ┌─────────┐
│  null   │ ──────────────→  │ queued  │
└─────────┘                  └────┬────┘
                                  │
                                  │ canExecuteTool() → true
                                  ↓
                           ┌─────────────┐
                           │  executing  │
                           └──────┬──────┘
                                  │
                     ┌────────────┼────────────┐
                     ↓            ↓            ↓
              ┌───────────┐ ┌──────────┐ ┌──────────┐
              │ completed │ │  yielded  │ │ completed│ (错误)
              └───────────┘ └──────────┘ └──────────┘
```

#### 3.2.3 关键方法

```typescript
export class StreamingToolExecutor {
  // 添加工具到执行队列
  addTool(block: ToolUseBlock, assistantMessage: AssistantMessage): void {
    // 1. 查找工具定义
    const toolDefinition = findToolByName(this.toolDefinitions, block.name)

    // 2. 解析输入参数
    const parsedInput = toolDefinition.inputSchema.safeParse(block.input)

    // 3. 判断并发安全性
    const isConcurrencySafe = parsedInput?.success
      ? toolDefinition.isConcurrencySafe?.(parsedInput.data) ?? false
      : false

    // 4. 加入队列并触发执行
    this.tools.push({
      id: block.id,
      block,
      assistantMessage,
      status: 'queued',
      isConcurrencySafe,
      pendingProgress: [],
    })

    void this.processQueue()
  }

  // 检查是否可以执行
  private canExecuteTool(isConcurrencySafe: boolean): boolean {
    const executingTools = this.tools.filter(t => t.status === 'executing')
    return (
      executingTools.length === 0 ||
      (isConcurrencySafe && executingTools.every(t => t.isConcurrencySafe))
    )
  }

  // 处理队列，执行就绪的工具
  private async processQueue(): Promise<void> {
    for (const tool of this.tools) {
      if (tool.status !== 'queued') continue

      if (this.canExecuteTool(tool.isConcurrencySafe)) {
        await this.executeTool(tool)
      } else {
        // 非安全工具需等待，停止处理
        if (!tool.isConcurrencySafe) break
      }
    }
  }
}
```

#### 3.2.4 并发控制策略

| 工具类型 | 行为 | 说明 |
|----------|------|------|
| 并发安全工具 | 可并行执行 | 如文件读取、搜索等只读操作 |
| 非安全工具 | 独占执行 | 如文件写入、命令执行等可能产生副作用的操作 |

```typescript
// 示例：文件读取工具
const FileReadTool: Tool = {
  name: 'Read',
  isConcurrencySafe: () => true,  // 可并发执行
  execute: async (input, context) => { /* ... */ }
}

// 示例：文件写入工具
const FileWriteTool: Tool = {
  name: 'Write',
  isConcurrencySafe: () => false,  // 独占执行
  execute: async (input, context) => { /* ... */ }
}
```

---

### 3.3 toolExecution.ts — 工具执行核心

**文件**: `src/services/tools/toolExecution.ts`

#### 3.3.1 runToolUse 函数

```typescript
export async function runToolUse(
  toolDefinition: Tool,
  toolUseBlock: ToolUseBlock,
  assistantMessage: AssistantMessage,
  toolUseContext: ToolUseContext,
  canUseTool: CanUseToolFn,
): Promise<Message[]> {
  const { toolUseID } = toolUseBlock
  const startTime = Date.now()

  // 1. 输入验证
  const parsedInput = toolDefinition.inputSchema.safeParse(toolUseBlock.input)
  if (!parsedInput.success) {
    return [createErrorMessage(toolUseID, 'Invalid input parameters')]
  }

  // 2. 权限校验
  const permissionResult = await canUseTool(
    toolDefinition,
    parsedInput.data,
    toolUseContext,
    assistantMessage,
    toolUseID,
    false,
  )

  if (permissionResult.behavior === 'deny') {
    return [createDeniedMessage(toolUseID)]
  }

  if (permissionResult.behavior === 'ask') {
    // 显示权限提示给用户
    return [createAskMessage(toolUseID)]
  }

  // 3. 前置 Hook 执行
  const preHookResult = await runPreToolUseHooks(
    toolDefinition,
    parsedInput.data,
    toolUseContext,
  )

  // 4. 执行工具
  const result = await toolDefinition.execute(
    parsedInput.data,
    toolUseContext,
  )

  // 5. 后置 Hook 执行
  await runPostToolUseHooks(
    toolDefinition,
    parsedInput.data,
    result,
    toolUseContext,
  )

  // 6. 构建结果消息
  return [createResultMessage(toolUseID, result)]
}
```

#### 3.3.2 错误处理

```typescript
function classifyToolError(error: unknown): string {
  if (error instanceof TelemetrySafeError) {
    return error.telemetryMessage.slice(0, 200)
  }
  if (error instanceof Error) {
    const errnoCode = getErrnoCode(error)
    if (typeof errnoCode === 'string') {
      return `Error:${errnoCode}`  // ENOENT, EACCES, etc.
    }
    return error.name.slice(0, 60)
  }
  return 'UnknownError'
}
```

---

### 3.4 QueryEngine — 查询引擎

**文件**: `src/QueryEngine.ts`

#### 3.4.1 核心职责

- 管理对话生命周期和会话状态
- 处理用户输入，构建系统提示
- 协调工具调用流程
- 收集 API 使用统计

#### 3.4.2 消息流转

```typescript
async *submitMessage(
  prompt: string | ContentBlockParam[],
  options?: { uuid?: string; isMeta?: boolean },
): AsyncGenerator<SDKMessage, void, unknown> {
  // 1. 处理用户输入
  const {
    messages: messagesFromUserInput,
    shouldQuery,
    allowedTools,
    model: modelFromUserInput,
  } = await processUserInput({ input: prompt, ... })

  // 2. 更新消息历史
  this.mutableMessages.push(...messagesFromUserInput)

  // 3. 持久化消息
  await recordTranscript(this.mutableMessages)

  // 4. 进入查询循环
  for await (const message of query({
    messages: this.mutableMessages,
    model: mainLoopModel,
    tools: this.config.tools,
    // ...
  })) {
    yield message  // 流式输出消息
  }
}
```

---

## 四、Function Calling 完整流程

### 4.1 时序图

```
┌────────┐     ┌──────────────┐     ┌──────────┐     ┌─────────────┐
│  用户  │     │ QueryEngine │     │  API     │     │ Streaming   │
│        │     │              │     │          │     │ ToolExecutor│
└───┬────┘     └──────┬───────┘     └─────┬────┘     └──────┬──────┘
    │                 │                    │                 │
    │ submitMessage() │                    │                 │
    │ ────────────────│                    │                 │
    │                 │                    │                 │
    │                 │ POST /messages      │                 │
    │                 │ ───────────────────>                 │
    │                 │                    │                 │
    │                 │ 200 OK (streaming)  │                 │
    │                 │ <───────────────────                 │
    │                 │                    │                 │
    │                 │  tool_use block    │                 │
    │                 │ ───────────────────>                 │
    │                 │                    │ addTool(tool1)  │
    │                 │                    │ ────────────────>│
    │                 │                    │                 │
    │                 │  tool_use block    │                 │
    │                 │ ───────────────────>                 │
    │                 │                    │ addTool(tool2)  │
    │                 │                    │ ────────────────>│
    │                 │                    │                 │ processQueue()
    │                 │                    │                 │ canExecute(tool1)?
    │                 │                    │                 │ executeTool(tool1)
    │                 │                    │                 │
    │                 │                    │ tool_result    │
    │                 │ <─────────────────────────────────── │
    │                 │                    │                 │
    │                 │                    │ tool_result    │
    │                 │ <─────────────────────────────────── │
    │                 │                    │                 │
    │                 │  assistant message │                 │
    │                 │ <───────────────────                 │
    │                 │                    │                 │
```

### 4.2 详细步骤

| 步骤 | 执行者 | 操作 | 说明 |
|------|--------|------|------|
| 1 | QueryEngine | `submitMessage()` | 接收用户输入 |
| 2 | QueryEngine | `processUserInput()` | 处理用户输入和斜杠命令 |
| 3 | QueryEngine | `recordTranscript()` | 持久化用户消息 |
| 4 | QueryEngine | `query()` | 调用 API |
| 5 | API | `tool_use block` | 返回工具调用请求 |
| 6 | StreamingToolExecutor | `addTool()` | 添加工具到队列 |
| 7 | StreamingToolExecutor | `processQueue()` | 检查并发条件 |
| 8 | StreamingToolExecutor | `executeTool()` | 执行工具 |
| 9 | toolExecution | `canUseTool()` | 权限校验 |
| 10 | toolExecution | `runToolUse()` | 运行工具 |
| 11 | StreamingToolExecutor | `tool_result` | 返回结果 |
| 12 | QueryEngine | 继续循环 | 发送结果给 API |

---

## 五、权限系统

### 5.1 权限模式

```typescript
type PermissionMode =
  | 'default'      // 默认模式，需要用户确认
  | 'bypass'      // 绕过模式，自动允许所有操作
  | 'auto'         // 自动模式，基于规则自动决策
```

### 5.2 权限决策

```typescript
type PermissionResult = {
  behavior: 'allow' | 'deny' | 'ask'
  reason: string
  source: string
}
```

### 5.3 权限规则

```typescript
type ToolPermissionRulesBySource = {
  [source: string]: {
    tool: string | RegExp
    decisions: Array<{
      pattern: string
      behavior: 'allow' | 'deny'
    }>
  }[]
}
```

### 5.4 权限检查流程

```
┌─────────────┐
│   请求工具  │
└──────┬──────┘
       │
       ↓
┌──────────────────┐
│ alwaysAllowRules │ ──── Yes ───→ allow
│   匹配检查      │
└──────┬─────────┘
       │ No
       ↓
┌──────────────────┐
│  alwaysDenyRules │ ──── Yes ───→ deny
│    匹配检查      │
└──────┬─────────┘
       │ No
       ↓
┌──────────────────┐
│ alwaysAskRules   │ ──── Yes ───→ ask
│   匹配检查      │
└──────┬─────────┘
       │ No
       ↓
┌──────────────────┐
│     权限对话框    │ ──── 用户决定 ───→ allow/deny
└──────────────────┘
```

---

## 六、Hook 系统

### 6.1 Hook 类型

| 类型 | 时机 | 用途 |
|------|------|------|
| `pre_tool_use` | 工具执行前 | 修改参数、日志、权限增强 |
| `post_tool_use` | 工具执行后 | 结果处理、副作用清理 |
| `pre_compact` | 上下文压缩前 | 保存状态 |
| `post_compact` | 上下文压缩后 | 恢复状态 |

### 6.2 Hook 执行

```typescript
async function runPreToolUseHooks(
  tool: Tool,
  input: unknown,
  context: ToolUseContext,
): Promise<PreToolUseHookResult> {
  const hooks = getRegisteredPreToolUseHooks()

  for (const hook of hooks) {
    const result = await hook({ tool, input, context })
    if (result.modifiedInput) {
      input = result.modifiedInput
    }
    if (result.blocked) {
      return { blocked: true, reason: result.reason }
    }
  }

  return { blocked: false, modifiedInput: input }
}
```

---

## 七、工具示例

### 7.1 文件读取工具

```typescript
// src/tools/FileReadTool/FileReadTool.ts
export const FileReadTool: Tool = {
  name: 'Read',
  description: 'Read the complete contents of a file from the file system',
  inputSchema: z.object({
    file_path: z.string().describe('Path to file to read'),
    limit: z.number().optional().describe('Optional line limit'),
    offset: z.number().optional().describe('Optional line offset'),
  }),

  isConcurrencySafe: () => true,

  execute: async (input, context) => {
    const { file_path, limit, offset } = input

    // 检查文件是否存在且可读
    const stat = await checkFileAccess(file_path, 'read')
    if (!stat.exists) {
      return { data: `Error: File not found: ${file_path}` }
    }

    // 读取文件内容
    const content = await readFileContent(file_path, { limit, offset })

    // 检查文件大小限制
    const sizeLimit = context.options.maxReadBytes ?? MAX_FILE_SIZE
    if (content.length > sizeLimit) {
      return {
        data: `Error: File too large: ${file_path} (${content.length} bytes > ${sizeLimit} limit)`,
      }
    }

    return { data: content }
  },
}
```

### 7.2 文件写入工具

```typescript
// src/tools/FileWriteTool/FileWriteTool.ts
export const FileWriteTool: Tool = {
  name: 'Write',
  description: 'Write content to a file, replacing existing content if exists',
  inputSchema: z.object({
    file_path: z.string().describe('Path to file to write'),
    content: z.string().describe('Content to write to file'),
  }),

  isConcurrencySafe: () => false,  // 写入需要独占

  execute: async (input, context) => {
    const { file_path, content } = input

    // 检查写入权限
    const canWrite = await checkFileAccess(file_path, 'write')
    if (!canWrite) {
      return { data: `Error: Cannot write to: ${file_path}` }
    }

    // 确保目录存在
    await ensureDirectoryExists(dirname(file_path))

    // 写入文件
    await Bun.write(file_path, content)

    return {
      data: `File written successfully: ${file_path}`,
    }
  },
}
```

### 7.3 Bash 命令工具

```typescript
// src/tools/BashTool/BashTool.ts
export const BashTool: Tool = {
  name: 'Bash',
  description: 'Execute a bash command in the current working directory',
  inputSchema: z.object({
    command: z.string().describe('The bash command to execute'),
    timeout: z.number().optional().describe('Timeout in seconds'),
    working_directory: z.string().optional().describe('Working directory'),
  }),

  isConcurrencySafe: () => false,

  execute: async (input, context) => {
    const { command, timeout = 60, working_directory } = input

    // 创建子进程
    const proc = spawn('bash', ['-c', command], {
      cwd: working_directory ?? context.options.cwd,
      timeout: timeout * 1000,
      onExit: (code, signal) => { /* ... */ }
    })

    // 收集输出
    const stdout = await proc.stdout
    const stderr = await proc.stderr

    return {
      data: {
        stdout,
        stderr,
        exitCode: proc.exitCode,
      },
    }
  },
}
```

---

## 八、工具注册与发现

### 8.1 内置工具注册

```typescript
// src/tools.ts
import { FileReadTool } from './tools/FileReadTool/FileReadTool.ts'
import { FileWriteTool } from './tools/FileWriteTool/FileWriteTool.ts'
import { BashTool } from './tools/BashTool/BashTool.ts'
import { GrepTool } from './tools/GrepTool/GrepTool.ts'
import { GlobTool } from './tools/GlobTool/GlobTool.ts'
// ... 更多工具

export function getAllBaseTools(): Tools {
  return [
    FileReadTool,
    FileWriteTool,
    BashTool,
    GrepTool,
    GlobTool,
    // ...
  ]
}
```

### 8.2 MCP 工具集成

```typescript
// src/services/mcp/client.ts
export class MCPToolConnection {
  async listTools(): Promise<Tool[]> {
    const response = await this.request('tools/list')
    return response.tools.map(mcpTool => ({
      name: normalizeToolName(mcpTool.name),
      description: mcpTool.description,
      inputSchema: zodSchemaFromMCP(mcpTool.inputSchema),
      execute: async (input, context) => {
        const result = await this.request('tools/call', {
          name: mcpTool.name,
          arguments: input,
        })
        return { data: result }
      },
    }))
  }
}
```

### 8.3 斜杠命令转换为工具

```typescript
// src/commands.ts
export function getSlashCommandToolSkills(): Tool[] {
  return [
    {
      name: 'SlashCommand',
      description: 'Execute a slash command',
      inputSchema: z.object({
        command: z.string(),
        args: z.string().optional(),
      }),
      execute: async (input, context) => {
        const command = findCommand(input.command)
        if (!command) {
          return { data: `Unknown command: ${input.command}` }
        }
        return command.execute(input.args, context)
      },
    }
  ]
}
```

---

## 九、上下文管理

### 9.1 工具结果存储

```typescript
// src/utils/toolResultStorage.ts
export function processToolResultBlock(
  result: ToolResultBlockParam,
  contentReplacementState?: ContentReplacementState,
): ProcessedResult {
  // 1. 提取内容
  const content = extractContent(result.content)

  // 2. 应用内容替换（如 token 限制）
  if (contentReplacementState) {
    const { truncated, wasTruncated } = applyContentBudget(
      content,
      contentReplacementState,
    )
    return { content: truncated, wasTruncated }
  }

  return { content, wasTruncated: false }
}
```

### 9.2 进度更新

```typescript
// src/types/tools.ts
export type ToolProgressData =
  | { type: 'progress'; message: string; percentage?: number }
  | { type: 'read_progress'; file: string; bytes: number; total: number }
  | { type: 'bash_progress'; running: boolean; output: string }
```

---

## 十、调试与日志

### 10.1 调试日志

```typescript
// src/utils/debug.ts
export function logForDebugging(
  toolName: string,
  input: unknown,
  result: ToolResult,
  duration: number,
): void {
  if (process.env.DEBUG_TOOLS) {
    console.log(`[Tool] ${toolName}`)
    console.log(`  Input: ${JSON.stringify(input)}`)
    console.log(`  Duration: ${duration}ms`)
    console.log(`  Result: ${JSON.stringify(result.data).slice(0, 100)}...`)
  }
}
```

### 10.2 性能追踪

```typescript
// src/utils/telemetry/sessionTracing.ts
export async function withToolTracing<T>(
  toolName: string,
  fn: () => Promise<T>,
): Promise<T> {
  const span = startToolSpan(toolName)
  try {
    const result = await fn()
    endToolSpan(span, 'success')
    return result
  } catch (error) {
    endToolSpan(span, 'error', classifyToolError(error))
    throw error
  }
}
```

---

## 十一、相关文件索引

| 文件路径 | 描述 |
|----------|------|
| `src/Tool.ts` | 工具定义接口和类型 |
| `src/QueryEngine.ts` | 查询引擎核心 |
| `src/query.ts` | 主查询循环 |
| `src/services/tools/StreamingToolExecutor.ts` | 流式工具执行器 |
| `src/services/tools/toolExecution.ts` | 工具执行逻辑 |
| `src/services/tools/toolHooks.ts` | Hook 系统 |
| `src/services/tools/toolOrchestration.ts` | 工具编排 |
| `src/hooks/useCanUseTool.ts` | 权限检查钩子 |
| `src/types/tools.ts` | 工具相关类型定义 |
| `src/types/permissions.ts` | 权限相关类型定义 |
| `src/utils/toolResultStorage.ts` | 结果存储与压缩 |
| `src/utils/toolErrors.ts` | 错误处理 |
| `src/utils/messages.ts` | 消息创建工具 |
| `src/tools/*/ToolName.ts` | 各工具定义 |

---

*最后更新: 2026-05-29*
