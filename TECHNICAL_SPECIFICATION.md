# Claude Code Haha — 跨项目复现完整技术规格文档

---

## 一、项目整体概述

### 1.1 项目整体技术架构

本项目是一个**多端 AI 编程助手**产品，提供 CLI 终端、桌面 GUI、IM 即时通讯适配器三种交互形态，核心架构如下：

```
┌─────────────────────────────────────────────────────────────────────┐
│                         交互层 (Frontend)                            │
│  ┌──────────────┐  ┌──────────────────┐  ┌───────────────────────┐ │
│  │  CLI (Ink/   │  │  Desktop (React/ │  │  IM Adapters         │ │
│  │  React TUI)  │  │  Tauri WebView)  │  │  (Telegram/Feishu/   │ │
│  │              │  │                  │  │   WeChat/DingTalk)    │ │
│  └──────┬───────┘  └────────┬─────────┘  └───────────┬───────────┘ │
│         │                   │ WS/REST                 │ WS          │
│         │                   ▼                         │             │
│  ┌──────┴─────────────────────────────────────────────┴──────────┐ │
│  │              本地服务层 (Bun HTTP + WebSocket Server)           │ │
│  │  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────────┐ │ │
│  │  │REST API   │ │WebSocket  │ │Proxy      │ │Static H5      │ │ │
│  │  │Router     │ │Handler    │ │Handler    │ │Server         │ │ │
│  │  └───────────┘ └───────────┘ └───────────┘ └───────────────┘ │ │
│  └──────────────────────────┬────────────────────────────────────┘ │
│                             │                                       │
│  ┌──────────────────────────┴────────────────────────────────────┐ │
│  │                    核心引擎层 (Core Engine)                     │ │
│  │  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────────┐ │ │
│  │  │QueryEngine│ │AgentTool  │ │Tool       │ │Compact/       │ │ │
│  │  │(对话管理)  │ │(子智能体)  │ │Execution  │ │Context Mgmt   │ │ │
│  │  └───────────┘ └───────────┘ └───────────┘ └───────────────┘ │ │
│  └──────────────────────────┬────────────────────────────────────┘ │
│                             │                                       │
│  ┌──────────────────────────┴────────────────────────────────────┐ │
│  │                    服务层 (Services)                            │ │
│  │  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────────┐ │ │
│  │  │MCP Client │ │OAuth      │ │Analytics  │ │LSP Manager   │ │ │
│  │  │Connection │ │Auth Flow  │ │Telemetry  │ │              │ │ │
│  │  │Manager    │ │           │ │           │ │              │ │ │
│  │  └───────────┘ └───────────┘ └───────────┘ └───────────────┘ │ │
│  └──────────────────────────┬────────────────────────────────────┘ │
│                             │                                       │
│  ┌──────────────────────────┴────────────────────────────────────┐ │
│  │                    持久化层 (Persistence)                       │ │
│  │  ~/.claude/           ~/.claude/cc-haha/        文件系统       │ │
│  │  settings.json         providers.json            JSONL日志     │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 全局技术栈

| 层级 | 技术 | 版本/说明 |
|------|------|-----------|
| **运行时** | Bun | Node.js 兼容的 JavaScript/TypeScript 运行时 |
| **CLI框架** | Commander.js (`@commander-js/extra-typings`) | CLI 参数解析 |
| **TUI框架** | Ink (`^6.8.0`) + React (`^19.2.4`) | 终端 UI 渲染 (自研 Ink 兼容层) |
| **桌面框架** | Tauri v2 + React 18 | 桌面应用 (Rust 原生壳 + WebView) |
| **桌面UI** | React 18 + TailwindCSS v4 + Zustand v5 | 组件化 UI + 状态管理 |
| **桌面构建** | Vite 8 | 前端构建工具 |
| **HTTP服务** | Bun.serve (原生) | 本地 HTTP + WebSocket 服务 |
| **AI SDK** | `@anthropic-ai/sdk` (`^0.80.0`) | Anthropic Messages API |
| **MCP协议** | `@modelcontextprotocol/sdk` (`^1.29.0`) | Model Context Protocol |
| **IM适配器** | grammy (Telegram), @larksuiteoapi/node-sdk (飞书), dingtalk-stream (钉钉) | 即时通讯桥接 |
| **可观测性** | OpenTelemetry (`@opentelemetry/*`) | 遥测 / 日志导出 |
| **代码高亮** | highlight.js, Shiki (v4) | 语法高亮 |
| **Markdown** | marked, turndown | MD 渲染/转换 |
| **图表** | Mermaid, asciichart | 流程图/ASCII图表 |
| **Schema校验** | Zod v4, AJV | 数据验证 |
| **代理** | https-proxy-agent, undici | HTTP/WS 代理 |
| **特性开关** | GrowthBook (`@growthbook/growthbook`) | 功能灰度发布 |
| **测试** | Vitest (桌面), Bun Test (CLI/适配器) | 测试框架 |
| **文档** | VitePress | 文档站 |

### 1.3 项目核心业务定位

**Claude Code Haha** 是一个基于 Anthropic Claude API 的 **AI 编程助手**，核心功能是：

1. **对话式编程**：用户通过自然语言描述需求，AI 直接读写文件、执行命令、搜索代码
2. **多工具编排**：AI 可调用 50+ 内置工具 (文件读写、Shell、Web搜索、Git、LSP 等)
3. **子智能体系统**：主智能体可派生子智能体并行处理子任务
4. **多端交互**：CLI 终端、桌面 GUI、IM 即时通讯 (Telegram/飞书/微信/钉钉)
5. **MCP 协议**：支持 Model Context Protocol 扩展外部工具/资源
6. **上下文管理**：自动压缩超长对话，维持有限上下文窗口

### 1.4 项目运行环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | macOS 12+, Windows 10+, Linux |
| 运行时 | Bun (最新稳定版) |
| 桌面构建 | Rust 工具链 (Tauri 编译) |
| 端口 | 3456 (服务端默认), 1420 (Vite 开发) |
| 环境变量 | 见 `.env.example` |

**关键环境变量：**

| 变量 | 说明 |
|------|------|
| `ANTHROPIC_AUTH_TOKEN` | API 认证令牌 |
| `ANTHROPIC_BASE_URL` | API 基础 URL (默认 Anthropic 官方) |
| `ANTHROPIC_MODEL` | 默认模型 |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | Sonnet 级别模型 |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | Haiku 级别模型 |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | Opus 级别模型 |
| `API_TIMEOUT_MS` | API 超时时间 (ms) |
| `SERVER_PORT` | 服务端端口 (默认 3456) |
| `SERVER_HOST` | 服务端绑定地址 (默认 127.0.0.1) |
| `DISABLE_TELEMETRY` | 禁用遥测 (=1) |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | 禁用非必要网络流量 |

---

## 二、分模块功能拆解梳理

---

### 模块 2.1：CLI 入口与启动流程

#### 2.1.1 功能介绍
命令行入口，负责启动参数解析、模式路由、子系统初始化，是整个项目的统一入口。

#### 2.1.2 所用技术栈
- **Bun** 运行时 `bun:bundle` 的 `feature()` 宏用于构建时死代码消除
- **Commander.js** CLI 参数解析框架
- **动态 import()** 实现按需加载，减少启动时间

#### 2.1.3 核心实现方式

文件：[src/entrypoints/cli.tsx](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/entrypoints/cli.tsx)

**快速路径路由** (零额外模块加载)：
1. `--version` / `-v` → 直接输出版本号
2. `--dump-system-prompt` → 输出系统提示词
3. `--claude-in-chrome-mcp` → Chrome MCP 服务端
4. `--chrome-native-host` → Chrome 原生消息主机
5. `--computer-use-mcp` → 计算机使用 MCP 服务端
6. `--daemon-worker=<kind>` → 守护进程工作线程
7. `remote-control` / `remote` / `sync` / `bridge` → 远程控制桥接模式
8. `daemon` → 守护进程主管
9. `ps` / `logs` / `attach` / `kill` / `--bg` → 后台会话管理
10. `new` / `list` / `reply` → 模板任务
11. `environment-runner` → BYOC 环境运行器
12. `self-hosted-runner` → 自托管运行器
13. `--tmux` + `--worktree` → tmux 工作树快速路径

兜底路径：加载完整 CLI (`src/main.tsx`)

#### 2.1.4 核心代码片段

```typescript
// 快速路径示例: --version 零模块加载
if (args[0] === '--version' || args[0] === '-v') {
  console.log(`${MACRO.VERSION} (Claude Code)`);
  return;
}

// 动态导入: 仅在需要时加载模块
const { enableConfigs } = await import('../utils/config.js');
const { getMainLoopModel } = await import('../utils/model/model.js');
```

#### 2.1.5 数据流转逻辑
```
CLI参数 → 快速路径匹配 → 特定功能模块加载 → 执行 → 退出
                       ↓ (无匹配)
                   完整CLI加载 → 配置初始化 → 交互式REPL
```

#### 2.1.6 依赖与关联
- 依赖：`src/entrypoints/init.ts` (系统初始化), `src/main.tsx` (主 CLI 逻辑)
- 关联：`src/cli/bg.ts` (后台会话), `src/bridge/` (桥接模式), `src/daemon/` (守护进程)

#### 2.1.7 异常处理
- 每个快速路径有独立的错误处理
- `--tmux` + `--worktree` 组合有 `exitWithError` 兜底
- 未知参数自动回退到完整 CLI 加载

---

### 模块 2.2：系统初始化 (init)

#### 2.2.1 功能介绍
全局系统初始化，负责配置加载、环境变量注入、遥测启动、安全策略、优雅关闭等。

#### 2.2.2 所用技术栈
- **memoize** (lodash-es) 确保初始化只执行一次
- **OpenTelemetry** 遥测初始化
- **GrowthBook** 特性开关初始化

#### 2.2.3 核心实现方式

文件：[src/entrypoints/init.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/entrypoints/init.ts)

初始化顺序：
1. `enableConfigs()` — 启用配置系统
2. `applySafeConfigEnvironmentVariables()` — 注入安全环境变量
3. `applyExtraCACertsFromConfig()` — 加载额外 CA 证书
4. `setupGracefulShutdown()` — 注册优雅关闭
5. 异步初始化 1P 事件日志 + GrowthBook
6. `detectCurrentRepository()` — 检测当前 Git 仓库
7. `setShellIfWindows()` — Windows Shell 适配
8. `configureGlobalMTLS()` — 全局 mTLS 配置
9. `configureGlobalAgents()` — 全局代理配置
10. `populateOAuthAccountInfoIfNeeded()` — OAuth 账户信息
11. `ensureScratchpadDir()` — 暂存目录
12. `initializeLspServerManager()` — LSP 服务管理
13. `initializePolicyLimitsLoadingPromise()` — 策略限制
14. `initializeRemoteManagedSettingsLoadingPromise()` — 远程管理设置

#### 2.2.4 核心代码片段

```typescript
export const init = memoize(async (): Promise<void> => {
  enableConfigs();
  applySafeConfigEnvironmentVariables();
  applyExtraCACertsFromConfig();
  setupGracefulShutdown();

  void Promise.all([
    import('../services/analytics/firstPartyEventLogger.js'),
    import('../services/analytics/growthbook.js'),
  ]).then(([fp, gb]) => {
    fp.initialize1PEventLogging();
  });
  // ...
});
```

#### 2.2.5 数据流转逻辑
```
进程启动 → enableConfigs → 安全环境变量 → CA证书 → 优雅关闭注册
→ 异步遥测初始化 → 仓库检测 → Shell适配 → 代理/mTLS配置
→ OAuth → LSP管理 → 策略限制 → 远程设置
```

#### 2.2.7 异常处理
- `ConfigParseError` 特殊处理，显示无效配置对话框
- 配置文件损坏时有 `recoverableJsonFile` 恢复机制
- 遥测初始化失败不影响主流程

---

### 模块 2.3：CLI 主入口 (main.tsx)

#### 2.3.1 功能介绍
完整的 CLI 交互式 REPL 入口，负责参数解析、工具初始化、MCP 连接、权限设置、会话管理。

#### 2.3.2 所用技术栈
- **Commander.js** + `@commander-js/extra-typings` CLI 参数解析
- **React** + **Ink** (自研终端 UI 框架) 渲染 TUI
- **Zustand-like** 状态管理 (`src/state/AppStateStore.js`)

#### 2.3.3 核心实现方式

文件：[src/main.tsx](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/main.tsx)

`main()` 函数流程：
1. 解析 CLI 参数 (Commander)
2. 调用 `init()` 初始化系统
3. 加载 Agent 定义 (`getAgentDefinitionsWithOverrides`)
4. 加载 MCP 配置 (`getClaudeCodeMcpConfigs`)
5. 连接 MCP 服务器 (`getMcpToolsCommandsAndResources`)
6. 初始化权限模式 (`initialPermissionModeFromCLI`)
7. 处理会话恢复 (`--resume`, `--continue`)
8. 加载插件 (`initializeVersionedPlugins`)
9. 启动 Ink REPL (`launchRepl`)

#### 2.3.4 核心代码片段

```typescript
export async function main(): Promise<void> {
  await init();
  // 解析 CLI 参数
  const program = new CommanderCommand();
  // ... 配置 Commander 选项
  program.parse();
  // 初始化工具权限上下文
  const toolPermissionContext = initializeToolPermissionContext(options);
  // 启动 REPL
  await launchRepl({ ... });
}
```

#### 2.3.5 数据流转逻辑
```
CLI参数 → Commander解析 → init()系统初始化 → Agent加载
→ MCP连接 → 权限初始化 → 会话恢复 → 插件加载 → Ink REPL启动
```

---

### 模块 2.4：QueryEngine (对话引擎)

#### 2.4.1 功能介绍
核心对话引擎，管理单次会话的消息生命周期、API 调用、上下文压缩、工具执行。

#### 2.4.2 所用技术栈
- **Anthropic SDK** API 调用
- **文件状态缓存** (FileStateCache) 跟踪文件变更
- **上下文分析** (contextAnalysis) 智能压缩

#### 2.4.3 核心实现方式

文件：[src/QueryEngine.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/QueryEngine.ts)

`QueryEngine` 类：
- `submitMessage()` 发起新对话轮次
- `processUserInput()` 处理用户输入
- 自动调用 `compact()` 当上下文超过阈值
- 管理 `mutableMessages` 消息数组
- 追踪 `totalUsage` 累计用量
- 支持 `snipReplay` 历史消息截断重放

**关键属性：**
- `mutableMessages: Message[]` — 当前会话的所有消息
- `readFileState: FileStateCache` — 文件读取状态缓存
- `permissionDenials: SDKPermissionDenial[]` — 权限拒绝记录
- `totalUsage: NonNullableUsage` — 累计 Token 用量
- `discoveredSkillNames: Set<string>` — 已发现的技能

#### 2.4.5 数据流转逻辑
```
用户输入 → processUserInput → 构建消息列表 → Anthropic API 调用
→ 解析响应 (text/tool_use) → 执行工具调用 → 收集结果
→ 追加到消息列表 → 检查上下文长度 → 必要时压缩 → 返回响应
```

#### 2.4.6 依赖与关联
- 依赖：`src/services/api/claude.ts` (API 调用), `src/services/compact/` (上下文压缩)
- 关联：`src/Task.ts` (任务管理), `src/Tool.ts` (工具抽象)

---

### 模块 2.5：工具系统 (Tool)

#### 2.5.1 功能介绍
统一的工具抽象层，定义工具接口、权限检查、执行上下文、进度跟踪。

#### 2.5.2 所用技术栈
- **Zod v4** 输入/输出 Schema 验证
- **React** 工具 UI 渲染 (Ink 兼容层)

#### 2.5.3 核心实现方式

文件：[src/Tool.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/Tool.ts)

`Tool` 接口定义：
```typescript
type Tool = {
  name: string
  description: string
  inputSchema: z.ZodType
  outputSchema?: z.ZodType
  prompt: (ctx) => Promise<string>
  // 执行方法
  // 权限检查
  // UI 渲染
}
```

**核心类型：**
- `ToolUseContext` — 工具执行上下文，包含：命令列表、MCP 客户端、Agent 定义、模型配置、预算限制等
- `ToolPermissionContext` — 权限上下文，包含：权限模式、允许/拒绝规则、额外工作目录
- `CompactProgressEvent` — 压缩进度事件
- `ValidationResult` — 工具参数验证结果

#### 2.5.5 数据流转逻辑
```
AI 工具调用请求 → 查找工具定义 → 参数校验 (Zod)
→ 权限检查 → 工具执行 → 结果收集 → 返回 AI 响应
```

#### 2.5.6 依赖与关联
- 所有工具模块 (`src/tools/*/`) 实现此接口
- 权限系统 (`src/utils/permissions/`) 实现权限检查
- MCP 工具 (`MCPTool`) 桥接外部工具

---

### 模块 2.6：所有内置工具清单

| 工具名称 | 文件 | 功能 |
|----------|------|------|
| **AgentTool** | `src/tools/AgentTool/` | 创建/管理子智能体 |
| **AskUserQuestionTool** | `src/tools/AskUserQuestionTool/` | 向用户提问 |
| **BashTool** | `src/tools/BashTool/` | 执行 Shell 命令 |
| **PowerShellTool** | `src/tools/PowerShellTool/` | Windows PowerShell 执行 |
| **BriefTool** | `src/tools/BriefTool/` | 生成会话摘要 |
| **ConfigTool** | `src/tools/ConfigTool/` | 读写配置 |
| **CtxInspectTool** | `src/tools/CtxInspectTool/` | 检查上下文窗口 |
| **DiscoverSkillsTool** | `src/tools/DiscoverSkillsTool/` | 发现可用技能 |
| **EnterPlanModeTool** | `src/tools/EnterPlanModeTool/` | 进入计划模式 |
| **ExitPlanModeTool** | `src/tools/ExitPlanModeTool/` | 退出计划模式 |
| **EnterWorktreeTool** | `src/tools/EnterWorktreeTool/` | 进入 Git 工作树 |
| **ExitWorktreeTool** | `src/tools/ExitWorktreeTool/` | 退出 Git 工作树 |
| **FileEditTool** | `src/tools/FileEditTool/` | 编辑文件 (Search/Replace) |
| **FileReadTool** | `src/tools/FileReadTool/` | 读取文件 |
| **FileWriteTool** | `src/tools/FileWriteTool/` | 写入文件 |
| **GlobTool** | `src/tools/GlobTool/` | 文件匹配 |
| **GrepTool** | `src/tools/GrepTool/` | 内容搜索 (ripgrep) |
| **LSPTool** | `src/tools/LSPTool/` | 语言服务器协议操作 |
| **ListMcpResourcesTool** | `src/tools/ListMcpResourcesTool/` | 列出 MCP 资源 |
| **ListPeersTool** | `src/tools/ListPeersTool/` | 列出对等节点 |
| **MCPTool** | `src/tools/MCPTool/` | 调用 MCP 工具 |
| **McpAuthTool** | `src/tools/McpAuthTool/` | MCP 认证 |
| **MonitorTool** | `src/tools/MonitorTool/` | 监控进程 |
| **NotebookEditTool** | `src/tools/NotebookEditTool/` | 编辑 Jupyter Notebook |
| **PushNotificationTool** | `src/tools/PushNotificationTool/` | 推送通知 |
| **REPLTool** | `src/tools/REPLTool/` | 交互式 REPL |
| **ReadMcpResourceTool** | `src/tools/ReadMcpResourceTool/` | 读取 MCP 资源 |
| **RemoteTriggerTool** | `src/tools/RemoteTriggerTool/` | 远程触发 |
| **ReviewArtifactTool** | `src/tools/ReviewArtifactTool/` | 审查产物 |
| **ScheduleCronTool** | `src/tools/ScheduleCronTool/` | 定时任务 CRUD |
| **SendMessageTool** | `src/tools/SendMessageTool/` | 发送消息 |
| **SendUserFileTool** | `src/tools/SendUserFileTool/` | 发送文件给用户 |
| **SkillTool** | `src/tools/SkillTool/` | 调用技能 |
| **SleepTool** | `src/tools/SleepTool/` | 暂停执行 |
| **SnipTool** | `src/tools/SnipTool/` | 历史截断 |
| **SyntheticOutputTool** | `src/tools/SyntheticOutputTool/` | 合成输出 |
| **TaskCreateTool** | `src/tools/TaskCreateTool/` | 创建后台任务 |
| **TaskGetTool** | `src/tools/TaskGetTool/` | 获取任务状态 |
| **TaskListTool** | `src/tools/TaskListTool/` | 列出任务 |
| **TaskOutputTool** | `src/tools/TaskOutputTool/` | 获取任务输出 |
| **TaskStopTool** | `src/tools/TaskStopTool/` | 停止任务 |
| **TaskUpdateTool** | `src/tools/TaskUpdateTool/` | 更新任务 |
| **TeamCreateTool** | `src/tools/TeamCreateTool/` | 创建团队 |
| **TeamDeleteTool** | `src/tools/TeamDeleteTool/` | 删除团队 |
| **TerminalCaptureTool** | `src/tools/TerminalCaptureTool/` | 终端截图 |
| **TodoWriteTool** | `src/tools/TodoWriteTool/` | 待办事项管理 |
| **ToolSearchTool** | `src/tools/ToolSearchTool/` | 工具搜索 |
| **TungstenTool** | `src/tools/TungstenTool/` | Tungsten 集成 |
| **VerifyPlanExecutionTool** | `src/tools/VerifyPlanExecutionTool/` | 验证计划执行 |
| **WebBrowserTool** | `src/tools/WebBrowserTool/` | 内置浏览器 |
| **WebFetchTool** | `src/tools/WebFetchTool/` | HTTP 抓取 |
| **WebSearchTool** | `src/tools/WebSearchTool/` | 网络搜索 |
| **WorkflowTool** | `src/tools/WorkflowTool/` | 工作流执行 |

---

### 模块 2.7：本地服务端 (Server)

#### 2.7.1 功能介绍
Bun 原生 HTTP + WebSocket 服务，为桌面端和 IM 适配器提供 REST API 和实时通信。

#### 2.7.2 所用技术栈
- **Bun.serve** 原生 HTTP 服务
- **WebSocket** 升级处理
- **CORS** 中间件 (自定义)
- **mTLS** 支持 (`getWebSocketTLSOptions`)

#### 2.7.3 核心实现方式

文件：[src/server/index.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/server/index.ts)

**启动流程：**
```typescript
export function startServer(port = PORT, host = HOST) {
  enableConfigs();
  diagnosticsService.installConsoleCapture();
  diagnosticsService.installProcessCapture();
  ProviderService.setServerPort(port);

  const server = Bun.serve<WebSocketData>({
    port, hostname: host,
    idleTimeout: 60,
    async fetch(req, server) {
      // 1. H5 访问控制检查
      // 2. CORS 预检处理
      // 3. WebSocket 升级 (/ws/, /sdk/)
      // 4. OAuth 回调 (/callback, /callback/openai)
      // 5. REST API 路由 (/api/)
      // 6. 代理路由 (/proxy/)
      // 7. 健康检查 (/health)
      // 8. 静态 H5 资源
    },
    websocket: handleWebSocket,
  });
}
```

**API 路由表** (文件：[src/server/router.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/server/router.ts))：

| 路由 | 处理器 | 功能 |
|------|--------|------|
| `/api/sessions` | `handleSessionsApi` | 会话管理 |
| `/api/sessions/:id/chat` | `handleConversationsApi` | 对话消息 |
| `/api/conversations` | `handleConversationsApi` | 对话管理 |
| `/api/settings` | `handleSettingsApi` | 设置读写 |
| `/api/models` | `handleModelsApi` | 模型管理 |
| `/api/scheduled-tasks` | `handleScheduledTasksApi` | 定时任务 |
| `/api/search` | `handleSearchApi` | 搜索 |
| `/api/agents` | `handleAgentsApi` | 智能体管理 |
| `/api/tasks` | `handleAgentsApi` | 任务管理 |
| `/api/status` | `handleStatusApi` | 服务状态 |
| `/api/teams` | `handleTeamsApi` | 团队管理 |
| `/api/providers` | `handleProvidersApi` | 提供商配置 |
| `/api/haha-oauth` | `handleHahaOAuthApi` | Anthropic OAuth |
| `/api/haha-openai-oauth` | `handleHahaOpenAIOAuthApi` | OpenAI OAuth |
| `/api/adapters` | `handleAdaptersApi` | IM 适配器 |
| `/api/skills` | `handleSkillsApi` | 技能管理 |
| `/api/mcp` | `handleMcpApi` | MCP 管理 |
| `/api/plugins` | `handlePluginsApi` | 插件管理 |
| `/api/computer-use` | `handleComputerUseApi` | 计算机使用 |
| `/api/diagnostics` | `handleDiagnosticsApi` | 诊断信息 |
| `/api/doctor` | `handleDoctorApi` | 系统修复 |
| `/api/h5-access` | `handleH5AccessApi` | H5 访问控制 |
| `/api/activity-stats` | `handleActivityStatsApi` | 活动统计 |
| `/api/open-targets` | `handleOpenTargetsApi` | 打开目标 |
| `/api/memory` | `handleMemoryApi` | 记忆管理 |
| `/api/desktop-ui` | `handleDesktopUiApi` | 桌面 UI 偏好 |
| `/api/filesystem` | `handleFilesystemRoute` | 文件系统访问 |

#### 2.7.4 核心代码片段

**WebSocket 处理** ([src/server/ws/handler.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/server/ws/handler.ts))：
```typescript
export const handleWebSocket = {
  open(ws) {
    // SDK 通道: 授权验证
    // 客户端通道: 绑定会话输出
  },
  message(ws, rawMessage) {
    switch (message.type) {
      case 'user_message': handleUserMessage(ws, message); break;
      case 'permission_response': handlePermissionResponse(ws, message); break;
      case 'set_permission_mode': handleSetPermissionMode(ws, message); break;
      case 'set_runtime_config': handleSetRuntimeConfig(ws, message); break;
    }
  },
  close(ws) {
    // 清理会话资源, 延迟清理定时器
  },
};
```

#### 2.7.5 数据流转逻辑
```
桌面端/适配器 → WebSocket/REST → Server → ConversationService → CLI子进程
                                                ↓
                                         SDK WebSocket 桥接
                                                ↓
                                        CLI进程 (stream-json)
                                                ↓
                                        消息流回 WebSocket → 客户端
```

#### 2.7.6 依赖与关联
- `src/server/services/conversationService.ts` — CLI 子进程管理
- `src/server/services/sessionService.ts` — 会话持久化
- `src/server/proxy/handler.ts` — API 代理转发
- `src/server/services/teamWatcher.ts` — 团队文件监听
- `src/server/services/cronScheduler.ts` — 定时任务调度

#### 2.7.7 异常处理
- 持久化升级失败时 `ensurePersistentStorageUpgraded()` 抛出错误
- 无效 Session ID 返回 400
- WebSocket 升级失败返回 400
- 支持优雅关闭：kill 所有 CLI 子进程

---

### 模块 2.8：API 代理 (Proxy)

#### 2.8.1 功能介绍
协议转换反向代理，将 Anthropic Messages API 格式转换为 OpenAI Chat/Responses API 格式。

#### 2.8.2 所用技术栈
- **Fetch API** 原生 HTTP
- **流式处理** (SSE / NDJSON 解析)
- **AbortController** 超时控制

#### 2.8.3 核心实现方式

文件：[src/server/proxy/handler.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/server/proxy/handler.ts)

**支持的转换路径：**
1. `POST /proxy/v1/messages` → 使用当前活跃 Provider
2. `POST /proxy/providers/:providerId/v1/messages` → 使用指定 Provider

**转换矩阵：**

| 方向 | 格式 | 转换器 |
|------|------|--------|
| 请求 | Anthropic → OpenAI Chat | `anthropicToOpenaiChat.ts` |
| 请求 | Anthropic → OpenAI Responses | `anthropicToOpenaiResponses.ts` |
| 响应 (流式) | OpenAI Chat SSE → Anthropic SSE | `openaiChatStreamToAnthropic.ts` |
| 响应 (流式) | OpenAI Responses SSE → Anthropic SSE | `openaiResponsesStreamToAnthropic.ts` |
| 响应 (非流式) | OpenAI Chat JSON → Anthropic JSON | `openaiChatToAnthropic.ts` |
| 响应 (非流式) | OpenAI Responses JSON → Anthropic JSON | `openaiResponsesToAnthropic.ts` |

#### 2.8.5 数据流转逻辑
```
CLI发送 Anthropic 请求 → Proxy 接收 → 查询 Provider 配置
→ 格式转换 (Anthropic→OpenAI) → 发送至上游 → 解析响应
→ 格式转换 (OpenAI→Anthropic) → 返回 CLI
```

---

### 模块 2.9：MCP 连接管理

#### 2.9.1 功能介绍
Model Context Protocol 客户端，管理外部 MCP 服务器的连接、工具发现、资源访问。

#### 2.9.2 所用技术栈
- **@modelcontextprotocol/sdk** (`^1.29.0`) MCP 协议实现
- **SSE / Streamable HTTP / stdio** 多种传输方式
- **OAuth** MCP 认证

#### 2.9.3 核心实现方式

文件：[src/services/mcp/client.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/services/mcp/client.ts)

**支持的传输方式：**
1. **Stdio** (`StdioClientTransport`) — 子进程通信
2. **SSE** (`SSEClientTransport`) — Server-Sent Events
3. **Streamable HTTP** (`StreamableHTTPClientTransport`) — 流式 HTTP
4. **WebSocket** (`WebSocketTransport`) — 自研 WebSocket 传输
5. **SDK Control** (`SdkControlClientTransport`) — 桌面端 SDK 桥接

**核心功能：**
- `connectMcpServer()` — 连接单个 MCP 服务器
- `ensureConnectedClient()` — 确保连接可用（含重连）
- `getMcpToolsCommandsAndResources()` — 获取所有 MCP 工具/命令/资源
- `callTool()` — 调用 MCP 工具
- `readResource()` — 读取 MCP 资源
- OAuth 认证流程 (`checkAndRefreshOAuthTokenIfNeeded`)

#### 2.9.5 数据流转逻辑
```
配置加载 → 创建传输层 → 连接 MCP 服务器 → 工具/资源发现
→ 注册到工具系统 → AI 可调用 → 结果返回
```

#### 2.9.6 依赖与关联
- `src/services/mcp/config.ts` — MCP 配置管理
- `src/services/mcp/auth.ts` — MCP OAuth 认证
- `src/services/mcp/types.ts` — 类型定义
- `src/tools/MCPTool/` — MCP 工具包装器
- `src/services/mcp/officialRegistry.ts` — 官方 MCP 注册表

---

### 模块 2.10：上下文压缩 (Compact)

#### 2.10.1 功能介绍
当对话历史超出模型上下文窗口时，自动压缩历史消息，保持对话连贯性。

#### 2.10.2 所用技术栈
- **Anthropic API** 用于生成压缩摘要
- **文件状态缓存** 跟踪文件变更
- **Token 计数** 精确计算上下文大小

#### 2.10.3 核心实现方式

文件：[src/services/compact/compact.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/services/compact/compact.ts)

**压缩策略：**
1. **自动压缩** (`autoCompact.ts`) — 上下文超阈值时触发
2. **微压缩** (`microCompact.ts`) — 轻量级消息合并
3. **API 微压缩** (`apiMicrocompact.ts`) — API 调用前压缩
4. **会话记忆压缩** (`sessionMemoryCompact.ts`) — 提取长期记忆
5. **截断压缩** (`snipCompact.ts`) — 历史消息截断
6. **响应式压缩** (`reactiveCompact.ts`) — 基于响应动态调整

**关键函数：**
- `compact()` — 主压缩入口
- `preCompactHooks` / `postCompactHooks` — 压缩前后钩子
- `grouping.ts` — 消息分组逻辑
- `prompt.ts` — 压缩提示词

#### 2.10.5 数据流转逻辑
```
检测上下文超限 → 选择压缩策略 → 执行 pre_compact 钩子
→ 生成压缩摘要 → 替换历史消息 → 执行 post_compact 钩子
→ 继续对话
```

---

### 模块 2.11：桌面端 (Desktop)

#### 2.11.1 功能介绍
基于 Tauri 的桌面 GUI 应用，提供可视化会话管理、设置、IM 绑定等功能。

#### 2.11.2 所用技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 框架 | Tauri v2 | Rust 原生壳 + WebView |
| 前端 | React 18 + TypeScript | 组件化 UI |
| 样式 | TailwindCSS v4 | 原子化 CSS |
| 状态管理 | Zustand v5 | 轻量状态管理 |
| 构建 | Vite 8 | 前端构建 |
| 代码高亮 | Shiki v4 | 语法高亮 |
| 终端模拟 | xterm.js v6 | 终端面板 |
| 图表 | Mermaid | 流程图渲染 |
| 公式 | KaTeX | 数学公式渲染 |
| 图标 | Lucide React | 图标库 |
| 通知 | `@tauri-apps/plugin-notification` | 桌面通知 |
| 更新 | `@tauri-apps/plugin-updater` | 自动更新 |
| 对话框 | `@tauri-apps/plugin-dialog` | 原生对话框 |
| 进程 | `@tauri-apps/plugin-process` | 进程管理 |
| Shell | `@tauri-apps/plugin-shell` | Shell 命令 |

#### 2.11.3 核心实现方式

**入口文件：** [desktop/src/main.tsx](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/desktop/src/main.tsx)
```tsx
import { App } from './App'
import { createRoot } from 'react-dom/client'
createRoot(document.getElementById('root')!).render(<App />)
```

**根组件：** [desktop/src/App.tsx](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/desktop/src/App.tsx)
```tsx
export function App() {
  useScheduledTaskDesktopNotifications();
  useEffect(() => {
    installDesktopNotificationNavigation();
  }, []);
  return <AppShell />;
}
```

**应用壳：** [desktop/src/components/layout/AppShell.tsx](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/desktop/src/components/layout/AppShell.tsx)
- 初始化服务器 URL (`initializeDesktopServerUrl`)
- 拉取设置 (`fetchSettings`)
- 恢复标签页 (`restoreTabs`)
- 连接活跃会话 (`connectToSession`)

**桌面端状态管理 (Zustand Stores)：**

| Store | 文件 | 职责 |
|-------|------|------|
| `chatStore` | `stores/chatStore.ts` | WebSocket 连接、消息收发 |
| `sessionStore` | `stores/sessionStore.ts` | 会话列表/CRUD |
| `tabStore` | `stores/tabStore.ts` | 多标签页管理 |
| `settingsStore` | `stores/settingsStore.ts` | 全局设置 |
| `uiStore` | `stores/uiStore.ts` | UI 状态 (侧边栏等) |
| `providerStore` | `stores/providerStore.ts` | AI 提供商管理 |
| `mcpStore` | `stores/mcpStore.ts` | MCP 服务器管理 |
| `skillStore` | `stores/skillStore.ts` | 技能管理 |
| `pluginStore` | `stores/pluginStore.ts` | 插件管理 |
| `taskStore` | `stores/taskStore.ts` | 任务管理 |
| `teamStore` | `stores/teamStore.ts` | 团队管理 |
| `agentStore` | `stores/agentStore.ts` | 智能体管理 |
| `adapterStore` | `stores/adapterStore.ts` | IM 适配器 |
| `memoryStore` | `stores/memoryStore.ts` | 记忆管理 |
| `hahaOAuthStore` | `stores/hahaOAuthStore.ts` | Anthropic OAuth |
| `hahaOpenAIOAuthStore` | `stores/hahaOpenAIOAuthStore.ts` | OpenAI OAuth |
| `updateStore` | `stores/updateStore.ts` | 应用更新 |
| `terminalPanelStore` | `stores/terminalPanelStore.ts` | 终端面板 |
| `workspacePanelStore` | `stores/workspacePanelStore.ts` | 工作区面板 |
| `workspaceChatContextStore` | `stores/workspaceChatContextStore.ts` | 聊天上下文 |
| `openTargetStore` | `stores/openTargetStore.ts` | 打开目标 |
| `cliTaskStore` | `stores/cliTaskStore.ts` | CLI 任务 |
| `sessionRuntimeStore` | `stores/sessionRuntimeStore.ts` | 会话运行时 |

**桌面端页面：**

| 页面 | 文件 | 功能 |
|------|------|------|
| `ActiveSession` | `pages/ActiveSession.tsx` | 活跃会话聊天界面 |
| `EmptySession` | `pages/EmptySession.tsx` | 空白会话页面 |
| `Settings` | `pages/Settings.tsx` | 设置主页 |
| `SessionControls` | `pages/SessionControls.tsx` | 会话控制 |
| `TerminalSettings` | `pages/TerminalSettings.tsx` | 终端设置 |
| `ScheduledTasks` | `pages/ScheduledTasks.tsx` | 定时任务管理 |
| `NewTaskModal` | `pages/NewTaskModal.tsx` | 新建任务弹窗 |
| `AgentTeams` | `pages/AgentTeams.tsx` | 智能体团队 |
| `AdapterSettings` | `pages/AdapterSettings.tsx` | IM 适配器设置 |
| `McpSettings` | `pages/McpSettings.tsx` | MCP 设置 |
| `MemorySettings` | `pages/MemorySettings.tsx` | 记忆设置 |
| `ComputerUseSettings` | `pages/ComputerUseSettings.tsx` | 计算机使用设置 |
| `DiagnosticsSettings` | `pages/DiagnosticsSettings.tsx` | 诊断设置 |
| `ActivitySettings` | `pages/ActivitySettings.tsx` | 活动设置 |
| `ToolInspection` | `pages/ToolInspection.tsx` | 工具检查 |

#### 2.11.5 数据流转逻辑
```
桌面端 React UI → HTTP API 调用 → 本地服务端 (3456端口)
→ WebSocket 连接 → ConversationService 管理 CLI 子进程
→ SDK WebSocket 桥接 → CLI 进程 (stream-json 模式)
→ 消息流返回 WebSocket → 桌面端实时更新
```

#### 2.11.6 依赖与关联
- 依赖本地服务端 (`src/server/`) 运行
- API 客户端 (`desktop/src/api/client.ts`) 封装 HTTP 调用
- Tauri 插件提供原生能力 (通知、更新、对话框等)

---

### 模块 2.12：IM 适配器 (Adapters)

#### 2.12.1 功能介绍
将 Claude Code 能力桥接到即时通讯平台，用户可通过 IM 与 AI 对话。

#### 2.12.2 所用技术栈

| 平台 | 库 | 传输方式 |
|------|-----|----------|
| Telegram | grammy (`^1.42.0`) | Webhook / Long Polling |
| 飞书 | @larksuiteoapi/node-sdk | WebSocket 长连接 |
| 微信 | 自研协议 | HTTP 轮询 |
| 钉钉 | dingtalk-stream (`2.1.4`) | Stream 模式 |

#### 2.12.3 核心实现方式

**公共模块** (`adapters/common/`)：

| 模块 | 功能 |
|------|------|
| `WsBridge` | WebSocket 桥接客户端，连接服务端 |
| `MessageBuffer` | 消息缓冲/合并 |
| `MessageDedup` | 消息去重 |
| `SessionStore` | 会话绑定存储 |
| `AdapterHttpClient` | HTTP API 客户端 |
| `config.ts` | 统一配置加载 |
| `format.ts` | 消息格式化 |
| `pairing.ts` | 用户配对/授权 |
| `permission.ts` | 权限请求交互 |
| `session-recovery.ts` | 会话恢复 |
| `chat-queue.ts` | 聊天队列 |
| `attachment/*` | 附件处理 |

**各平台适配器架构：**

```
IM 消息 → 适配器解析 → WebSocket 发送到服务端 → CLI 处理
→ 响应流回 WebSocket → 适配器格式化 → 发送到 IM 平台
```

**Telegram 适配器** ([adapters/telegram/index.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/adapters/telegram/index.ts))：
- 使用 grammy Bot 框架
- 支持流式更新 (占位消息编辑)
- 支持媒体文件 (图片/文件)
- 支持权限请求交互 (Inline Keyboard)

**飞书适配器** ([adapters/feishu/index.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/adapters/feishu/index.ts))：
- 使用飞书 WebSocket 长连接
- 支持流式卡片 (CardKit)
- 支持 Markdown 优化
- 支持媒体文件

**微信适配器** ([adapters/wechat/index.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/adapters/wechat/index.ts))：
- 自研 HTTP 轮询协议
- 支持打字指示器
- 支持上下文 Token 传递

**钉钉适配器** ([adapters/dingtalk/index.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/adapters/dingtalk/index.ts))：
- 使用钉钉 Stream 模式
- 支持 AI 卡片 (DingTalkAiCard)
- 支持权限请求卡片

#### 2.12.5 数据流转逻辑
```
IM平台 → 适配器接收 → 项目选择 → 会话创建/恢复
→ WebSocket 发送用户消息 → 服务端处理 → CLI 生成回复
→ 流式消息返回 → 适配器格式化 → IM平台发送
```

#### 2.12.6 依赖与关联
- 依赖服务端 WebSocket (`/ws/:sessionId`)
- 依赖服务端 REST API (会话管理)
- 依赖 `adapters.json` 配置文件

---

### 模块 2.13：命令系统 (Commands)

#### 2.13.1 功能介绍
斜杠命令系统，用户在 REPL 中输入 `/command` 触发特定功能。

#### 2.13.2 所用技术栈
- **React** + **Ink** 渲染命令输出
- 动态 `import()` 按需加载命令模块

#### 2.13.3 核心实现方式

文件：[src/commands.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/commands.ts)

**命令类型：**
- `type: 'prompt'` — 发送提示词给 AI
- `type: 'local'` — 本地执行
- `type: 'local-jsx'` — 本地 JSX 渲染

**核心命令列表：**

| 命令 | 类型 | 功能 |
|------|------|------|
| `/add-dir` | local | 添加额外目录 |
| `/agents` | local-jsx | 管理智能体 |
| `/bridge` | local | 桥接模式 |
| `/btw` | local-jsx | 后台工作 |
| `/buddy` | local-jsx | 伙伴模式 |
| `/clear` | local | 清除缓存 |
| `/color` | local | 颜色主题 |
| `/commit` | prompt | 生成提交信息 |
| `/compact` | local | 手动压缩上下文 |
| `/config` | local-jsx | 配置管理 |
| `/context` | local | 查看上下文 |
| `/copy` | local-jsx | 复制内容 |
| `/cost` | local | 费用统计 |
| `/diff` | local-jsx | 查看差异 |
| `/doctor` | local | 系统诊断 |
| `/effort` | local | 努力程度设置 |
| `/env` | local | 环境变量 |
| `/exit` | local | 退出 |
| `/export` | local | 导出会话 |
| `/fast` | local-jsx | 快速模式 |
| `/files` | local | 文件管理 |
| `/goal` | local-jsx | 目标管理 |
| `/help` | prompt | 帮助 |
| `/hooks` | local-jsx | 钩子管理 |
| `/ide` | local-jsx | IDE 集成 |
| `/init` | prompt | 项目初始化 |
| `/insights` | prompt | 会话分析 |
| `/login` | local | 登录 |
| `/logout` | local | 登出 |
| `/mcp` | local-jsx | MCP 管理 |
| `/memory` | local | 记忆管理 |
| `/model` | local-jsx | 模型选择 |
| `/permissions` | local | 权限管理 |
| `/plan` | local-jsx | 计划模式 |
| `/plugin` | local | 插件管理 |
| `/release-notes` | local | 版本说明 |
| `/rename` | local | 重命名会话 |
| `/resume` | local | 恢复会话 |
| `/review` | prompt | 代码审查 |
| `/rewind` | local | 回退会话 |
| `/skills` | local | 技能管理 |
| `/stats` | local-jsx | 统计信息 |
| `/status` | local | 状态查看 |
| `/tag` | local-jsx | 标签 |
| `/tasks` | local-jsx | 任务管理 |
| `/theme` | local-jsx | 主题设置 |
| `/usage` | local-jsx | 用量统计 |
| `/vim` | local | Vim 模式 |
| `/voice` | local | 语音模式 |
| `/workflows` | local | 工作流 |

**命令来源：**
1. 内置命令 (`src/commands/`)
2. 技能命令 (`src/skills/loadSkillsDir.js`)
3. 插件命令 (`src/utils/plugins/loadPluginCommands.js`)
4. 动态技能 (`getDynamicSkills`)
5. 内置插件技能 (`getBuiltinPluginSkillCommands`)

---

### 模块 2.14：任务系统 (Task)

#### 2.14.1 功能介绍
后台任务管理，支持本地 Shell、本地 Agent、远程 Agent、工作流等任务类型。

#### 2.14.2 所用技术栈
- **crypto.randomBytes** 生成任务 ID
- **文件系统** 存储任务输出
- **AbortController** 任务取消

#### 2.14.3 核心实现方式

文件：[src/Task.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/Task.ts)

**任务类型：**
| 类型 | 描述 |
|------|------|
| `local_bash` | 本地 Shell 命令 |
| `local_agent` | 本地子智能体 |
| `remote_agent` | 远程智能体 |
| `in_process_teammate` | 进程内队友 |
| `local_workflow` | 本地工作流 |
| `monitor_mcp` | MCP 监控 |
| `dream` | 后台思考 |

**任务状态：**
- `pending` → `running` → `completed` / `failed` / `killed`

**任务 ID 生成：**
```typescript
// 前缀 + 8位随机字符 (36进制)
// b=local_bash, a=local_agent, r=remote_agent, t=teammate, w=workflow, m=monitor, d=dream
export function generateTaskId(type: TaskType): string {
  const prefix = getTaskIdPrefix(type);
  const bytes = randomBytes(8);
  let id = prefix;
  for (let i = 0; i < 8; i++) {
    id += TASK_ID_ALPHABET[bytes[i]! % TASK_ID_ALPHABET.length];
  }
  return id;
}
```

---

### 模块 2.15：Provider 服务 (提供商管理)

#### 2.15.1 功能介绍
管理 AI 模型提供商配置，支持多提供商切换、预设模板、环境变量注入。

#### 2.15.2 所用技术栈
- **文件系统** JSON 持久化 (`~/.claude/cc-haha/providers.json`)
- **环境变量** 注入到 CLI 子进程

#### 2.15.3 核心实现方式

文件：[src/server/services/providerService.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/server/services/providerService.ts)

**数据结构：**
```typescript
type ProvidersIndex = {
  schemaVersion: number
  activeId: string | null
  providers: SavedProvider[]
}

type SavedProvider = {
  id: string
  name: string
  apiFormat: 'anthropic' | 'openai_chat' | 'openai_responses'
  baseUrl: string
  apiKey: string
  authStrategy: ProviderAuthStrategy
  models: ModelMapping[]
}
```

**支持的 API 格式：**
- `anthropic` — 原生 Anthropic Messages API
- `openai_chat` — OpenAI Chat Completions API (通过代理转换)
- `openai_responses` — OpenAI Responses API (通过代理转换)

**预设模板：** [src/server/config/providerPresets.json](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/server/config/providerPresets.json)

---

### 模块 2.16：OAuth 认证

#### 2.16.1 功能介绍
支持 Anthropic 和 OpenAI 的 OAuth 认证流程，管理 Token 的获取和刷新。

#### 2.16.2 所用技术栈
- **PKCE** 流程
- **本地 HTTP 回调** 服务器
- **Keychain** (macOS) / **文件系统** 存储 Token

#### 2.16.3 核心实现方式

文件：[src/services/oauth/](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/services/oauth/)

**Anthropic OAuth：**
- `src/services/oauth/client.ts` — OAuth 客户端
- `src/server/api/haha-oauth.ts` — 回调处理
- `src/services/oauth/auth-code-listener.ts` — 授权码监听

**OpenAI OAuth：**
- `src/services/openaiAuth/` — OpenAI 认证
- `src/server/api/haha-openai-oauth.ts` — 回调处理
- `src/server/services/openaiOfficialProvider.ts` — OpenAI 官方 Provider

**认证流程：**
```
用户点击登录 → 打开浏览器 → 完成平台授权 → 回调本地服务器
→ 交换授权码获取 Token → 加密存储 → 刷新 Token 定期更新
```

---

### 模块 2.17：LSP 集成

#### 2.17.1 功能介绍
语言服务器协议集成，提供代码诊断、补全、符号跳转等 IDE 功能。

#### 2.17.2 所用技术栈
- **vscode-jsonrpc** / **vscode-languageserver-types** 协议实现
- **子进程** 管理 LSP 服务器

#### 2.17.3 核心实现方式

文件：[src/services/lsp/](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/services/lsp/)

- `LSPClient.ts` — LSP 客户端
- `LSPServerInstance.ts` — 单个 LSP 服务器实例
- `LSPServerManager.ts` — 多服务器管理
- `LSPDiagnosticRegistry.ts` — 诊断信息注册表
- `config.ts` — LSP 配置
- `passiveFeedback.ts` — 被动反馈

---

### 模块 2.18：技能系统 (Skills)

#### 2.18.1 功能介绍
可扩展的技能系统，支持本地技能和远程技能搜索。

#### 2.18.2 所用技术栈
- **文件系统** 技能目录 (`~/.claude/skills/`, `.claude/skills/`)
- **Fuse.js** 模糊搜索
- **远程加载** 技能市场

#### 2.18.3 核心实现方式

文件：[src/services/skillSearch/](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/services/skillSearch/)

- `localSearch.ts` — 本地技能索引和搜索
- `remoteSkillLoader.ts` — 远程技能加载
- `remoteSkillState.ts` — 远程技能状态
- `prefetch.ts` — 预取技能数据
- `featureCheck.ts` — 特性检查
- `signals.ts` / `telemetry.ts` — 信号和遥测

---

### 模块 2.19：插件系统 (Plugins)

#### 2.19.1 功能介绍
可扩展的插件系统，支持安装、更新、卸载第三方插件。

#### 2.19.2 所用技术栈
- **文件系统** 插件目录管理
- **版本化** 插件缓存

#### 2.19.3 核心实现方式

文件：
- `src/services/plugins/PluginInstallationManager.ts` — 插件安装管理
- `src/services/plugins/pluginCliCommands.ts` — 插件 CLI 命令
- `src/services/plugins/pluginOperations.ts` — 插件操作
- `src/utils/plugins/` — 插件工具函数

---

### 模块 2.20：守护进程 (Daemon)

#### 2.20.1 功能介绍
长时间运行的后台进程，管理定时任务、工作线程、远程控制等服务。

#### 2.20.2 所用技术栈
- **Bun** 子进程管理
- 工作线程注册表 (`workerRegistry.ts`)

#### 2.20.3 核心实现方式

文件：
- `src/daemon/main.ts` — 守护进程主入口
- `src/daemon/workerRegistry.ts` — 工作线程注册

---

### 模块 2.21：桥接模式 (Bridge)

#### 2.21.1 功能介绍
远程控制功能，允许本地机器作为桥接环境，服务远程 CLI 会话。

#### 2.21.2 所用技术栈
- **WebSocket** 双向通信
- **JWT** 认证
- **GrowthBook** 特性开关

#### 2.21.3 核心实现方式

文件：[src/bridge/](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/bridge/)

- `bridgeMain.ts` — 桥接主入口
- `bridgeApi.ts` — 桥接 API
- `bridgeConfig.ts` — 桥接配置
- `bridgeUI.ts` — 桥接 UI
- `bridgeDebug.ts` — 桥接调试
- `replBridge.ts` / `initReplBridge.ts` — REPL 桥接
- `peerSessions.ts` — 对等会话
- `jwtUtils.ts` — JWT 工具
- `workSecret.ts` — 工作密钥
- `trustedDevice.ts` — 可信设备
- `pollConfig.ts` — 配置轮询
- `flushGate.ts` — 刷新门控

---

### 模块 2.22：计划模式 (Plan Mode)

#### 2.22.1 功能介绍
在代码修改前生成计划，经用户确认后执行，降低误操作风险。

#### 2.22.2 所用技术栈
- AI 生成计划
- 文件系统持久化计划文件

#### 2.22.3 核心实现方式

工具：
- `EnterPlanModeTool` — 进入计划模式
- `ExitPlanModeTool` (V2) — 退出计划模式
- `VerifyPlanExecutionTool` — 验证计划执行

计划文件存储：`~/.claude/plans/`

---

### 模块 2.23：计算机使用 (Computer Use)

#### 2.23.1 功能介绍
允许 AI 控制计算机桌面，执行鼠标点击、键盘输入、截屏等操作。

#### 2.23.2 所用技术栈
- **Python** 桥接 (macOS/Windows)
- **Swift** (macOS 辅助功能)
- **macOS Accessibility API**

#### 2.23.3 核心实现方式

文件：[src/utils/computerUse/](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/utils/computerUse/)

- `executor.ts` — 操作执行器
- `mcpServer.ts` — MCP 服务端
- `pythonBridge.ts` — Python 桥接
- `swiftLoader.ts` — Swift 加载器
- `hostAdapter.ts` — 平台适配
- `permissions.ts` — 权限检查
- `gates.ts` — 功能门控

---

### 模块 2.24：Chrome 集成

#### 2.24.1 功能介绍
允许 AI 与 Chrome 浏览器交互，控制网页操作。

#### 2.24.2 所用技术栈
- **Chrome Extension** Native Messaging
- **MCP** 协议

#### 2.24.3 核心实现方式

文件：[src/utils/claudeInChrome/](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/utils/claudeInChrome/)

- `mcpServer.ts` — MCP 服务端
- `chromeNativeHost.ts` — Chrome 原生消息主机
- `setup.ts` / `setupPortable.ts` — 安装/便携安装
- `toolRendering.tsx` — 工具渲染
- `prompt.ts` — 提示词

---

### 模块 2.25：自研 Ink 终端框架

#### 2.25.1 功能介绍
类 Ink 的 React 终端 UI 渲染框架，包含完整的布局引擎、事件系统、终端 I/O 处理。

#### 2.25.2 所用技术栈
- **React** + **react-reconciler** 自定义渲染器
- **Yoga** 布局引擎 (Flexbox)
- **ANSI 转义序列** 解析/生成
- **Bidi (bidi-js)** 双向文本支持

#### 2.25.3 核心实现方式

文件：[src/ink/](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/ink/)

**核心组件：**
- `ink.tsx` — 主渲染入口
- `reconciler.ts` — React 自定义渲染器
- `renderer.ts` — 终端渲染器
- `render-to-screen.ts` — 屏幕渲染
- `dom.ts` — 虚拟 DOM

**布局引擎：**
- `layout/engine.ts` — 布局引擎
- `layout/yoga.ts` — Yoga 集成
- `layout/node.ts` — 布局节点
- `layout/geometry.ts` — 几何计算

**终端 I/O：**
- `termio.ts` — 终端 I/O 主模块
- `termio/parser.ts` — 终端序列解析器
- `termio/ansi.ts` / `csi.ts` / `esc.ts` / `osc.ts` / `sgr.ts` — ANSI 序列处理
- `terminal.ts` — 终端抽象
- `terminal-querier.ts` — 终端查询
- `screen.ts` — 屏幕缓冲区

**事件系统：**
- `events/emitter.ts` — 事件发射器
- `events/dispatcher.ts` — 事件分发器
- `events/event.ts` — 事件类型

**输入处理：**
- `parse-keypress.ts` — 按键解析
- `hooks/use-input.ts` — 输入 Hook
- `hooks/use-stdin.ts` — 标准输入 Hook

**其他：**
- `selection.ts` — 文本选择
- `searchHighlight.ts` — 搜索高亮
- `focus.ts` — 焦点管理
- `cursor.ts` — 光标管理
- `measure-text.ts` / `measure-element.ts` — 文本/元素测量
- `stringWidth.ts` — 字符串宽度 (含 CJK)
- `bidi.ts` — 双向文本
- `colorize.ts` — 颜色处理
- `styles.ts` — 样式系统
- `optimizer.ts` — 渲染优化
- `frame.ts` — 帧管理
- `log-update.ts` — 日志更新
- `devtools.ts` — 开发工具

---

## 三、全局公共能力梳理

### 3.1 配置管理

**文件：** [src/utils/config.ts](file:///d:/Users/xyn/Desktop/cc-haha-main/cc-haha-main/src/utils/config.ts)

**存储位置：**
- `~/.claude/settings.json` — 全局设置
- `.claude/settings.json` — 项目设置
- `~/.claude/cc-haha/settings.json` — 桌面端管理设置
- `~/.claude/cc-haha/providers.json` — 提供商配置

**配置类型：**
- `GlobalConfig` — 全局配置 (主题、安装方式、启动次数等)
- `ProjectConfig` — 项目配置 (允许工具、MCP 服务器、工作树等)
- `AccountInfo` — 账户信息

**配置变更检测：** `src/utils/settings/changeDetector.ts`

### 3.2 认证与授权

**模块：** `src/utils/auth.ts`

- `getClaudeAIOAuthTokens()` — 获取 OAuth Token
- `isClaudeAISubscriber()` — 检查订阅状态
- `getSubscriptionType()` — 获取订阅类型
- `prefetchAwsCredentialsAndBedRockInfoIfSafe()` — AWS Bedrock 凭据
- `prefetchGcpCredentialsIfSafe()` — GCP 凭据

### 3.3 文件状态缓存

**模块：** `src/utils/fileStateCache.ts`

**功能：**
- LRU 缓存文件读取状态
- 支持大小限制 (防止内存溢出)
- 提供 `createFileStateCacheWithSizeLimit()` 工厂函数

### 3.4 优雅关闭

**模块：** `src/utils/gracefulShutdown.ts`

- `setupGracefulShutdown()` — 注册信号处理
- `gracefulShutdown()` — 执行关闭流程
- `gracefulShutdownSync()` — 同步关闭

### 3.5 日志系统

**模块：**
- `src/utils/log.ts` — 日志工具 (`logError`, `logMCPDebug`, `logMCPError`)
- `src/utils/debug.ts` — 调试日志 (`logForDebugging`)
- `src/utils/diagLogs.ts` — 诊断日志 (`logForDiagnosticsNoPII`)

### 3.6 错误处理

**模块：** `src/utils/errors.ts`

- `ConfigParseError` — 配置解析错误
- `TeleportOperationError` — 传送操作错误
- `TelemetrySafeError` — 遥测安全错误
- `errorMessage()` — 提取错误消息
- `toError()` — 转换为 Error 对象

### 3.7 代理系统

**模块：** `src/utils/proxy.ts`

- `getProxyFetchOptions()` — 获取代理配置
- `getWebSocketProxyAgent()` — WebSocket 代理
- `getWebSocketProxyUrl()` — WebSocket 代理 URL
- `configureGlobalAgents()` — 全局代理配置

### 3.8 网络设置

**模块：** `src/server/services/networkSettings.ts`

- `loadNetworkSettings()` — 加载网络设置
- `getManualNetworkProxyUrl()` — 获取手动代理 URL
- `buildNetworkEnvironment()` — 构建网络环境变量

### 3.9 环境变量管理

**模块：** `src/utils/envUtils.ts`

- `isEnvTruthy()` — 判断环境变量为真
- `isBareMode()` — 判断简单模式
- `getClaudeConfigHomeDir()` — 配置目录
- `isInsideContainer()` — 容器检测

### 3.10 Token 管理

- `countTokens()` — 原始 Token 计数
- `tokenCountFromLastAPIResponse()` — 从响应提取用量
- `tokenCountWithEstimation()` — 估算 Token 数
- `getTokenUsage()` — 累计用量

### 3.11 消息处理工具

- `createUserMessage()` — 创建用户消息
- `createCompactBoundaryMessage()` — 创建压缩边界
- `normalizeMessagesForAPI()` — API 格式规范化
- `getAccessibilityAnnouncementText()` — 无障碍文本

### 3.12 钩子系统

- `executePreCompactHooks()` / `executePostCompactHooks()` — 压缩钩子
- `processSessionStartHooks()` — 会话开始钩子
- `executeStopHookWhenApplicable()` — 停止钩子
- `executeSubagentStopHookWhenApplicable()` — 子智能体停止钩子
- `executeNotificationHooks()` — 通知钩子

### 3.13 国际化 (i18n)

- `src/utils/i18n.ts` — 国际化工具函数
- `desktop/src/i18n/` — 桌面端翻译文件 (中英文)

### 3.14 桌面端公共组件

| 组件 | 文件 | 功能 |
|------|------|------|
| `AppShell` | `components/layout/AppShell.tsx` | 应用根布局 |
| `Sidebar` | `components/layout/Sidebar.tsx` | 侧边栏 |
| `ContentRouter` | `components/layout/ContentRouter.tsx` | 内容路由 |
| `TabBar` | `components/layout/TabBar.tsx` | 标签栏 |
| `ToastContainer` | `components/shared/Toast.tsx` | 全局 Toast |
| `UpdateChecker` | `components/shared/UpdateChecker.tsx` | 更新检查 |
| `KeyboardShortcuts` | `components/shared/KeyboardShortcuts.tsx` | 快捷键弹窗 |
| `SearchDialog` | `components/shared/SearchDialog.tsx` | 全局搜索 |
| `Icons` | `components/shared/Icons.tsx` | 统一图标组件 |
| `MarkdownRenderer` | `components/shared/MarkdownRenderer.tsx` | Markdown 渲染 |
| `CodeRenderer` | `components/shared/CodeRenderer.tsx` | 代码高亮渲染 |
| `MermaidRenderer` | `components/shared/MermaidRenderer.tsx` | 流程图渲染 |
| `KaTeXRenderer` | `components/shared/KaTeXRenderer.tsx` | 数学公式渲染 |
| `ConversationView` | `components/chat/ConversationView.tsx` | 对话视图 |
| `ChatInput` | `components/chat/ChatInput.tsx` | 聊天输入框 |
| `MessageItem` | `components/chat/MessageItem.tsx` | 单条消息 |
| `ToolResultCard` | `components/chat/ToolResultCard.tsx` | 工具结果卡片 |
| `PermissionRequestCard` | `components/chat/PermissionRequestCard.tsx` | 权限请求卡片 |
| `DiagnosticInfoCard` | `components/chat/DiagnosticInfoCard.tsx` | 诊断信息卡片 |
| `SessionList` | `components/session/SessionList.tsx` | 会话列表 |

### 3.15 CLI 表单组件 (Ink React)

| 组件 | 功能 |
|------|------|
| `SelectInput` | 选择输入 |
| `TextInput` | 文本输入 |
| `ConfirmationDialog` | 确认对话框 |
| `FuzzySelectInput` | 模糊选择 |
| `MultiOptionSelect` | 多选项选择 |
| `CheckInput` | 复选框 |
| `MultiSelectInput` | 多选输入 |
| `InputMultiSelect` | 输入多选 |

### 3.16 遥测与分析

- `src/services/analytics/` — 统一分析模块
- `src/services/analytics/growthbook.ts` — 特性开关 (GrowthBook)
- `src/services/analytics/firstPartyEventLogger.ts` — 事件日志
- `src/services/analytics/index.ts` — 分析入口
- `scripts/quality-gate/` — 质量门控体系

### 3.17 守护进程工具

- `src/utils/cliTools.ts` — CLI 工具函数
- `src/utils/daemon.ts` — 守护进程配置
- `src/utils/daemonIPC.ts` — 守护进程 IPC 通信

### 3.18 Memoize 工具

- `src/utils/memoize.ts` — 通用记忆化工具
- 使用 WeakRef 避免内存泄漏

### 3.19 会话存储

- `src/utils/sessionStorage.ts` — JSONL 文件持久化
- `~/.claude/projects/<project>/` — 按项目存储
- `src/utils/sessionTranscript/` — 会话转录

### 3.20 策略限制

- `src/utils/policy.ts` — 策略限制检查
- `src/utils/remoteManagedSettings.ts` — 远程管理设置

### 3.21 桌面端路由系统

- `components/layout/ContentRouter.tsx` — 基于 Zustand store 的路由
- 路由状态存储在 `uiStore.selectedPage`
- 支持页面：`sessions`, `settings`, `terminal`, `scheduled-tasks`, `new-task`, `agent-teams`, `adapter-settings`, `mcp-settings`, `memory-settings`, `computer-use`, `diagnostics`, `activity-stats`, `tool-inspection`

### 3.22 WebSocket 桥接协议

桌面端和适配器与服务端之间的 WebSocket 消息协议：
- `user_message` — 用户输入
- `assistant_message` — AI 响应
- `tool_use` / `tool_result` — 工具调用/结果
- `permission_request` / `permission_response` — 权限交互
- `system_message` — 系统通知
- `status_update` — 状态更新
- `stream_chunk` — 流式响应片段
- `set_permission_mode` / `set_runtime_config` — 设置变更
- `ping` / `pong` — 心跳

---

## 四、数据库与资源依赖梳理

### 4.1 持久化存储架构

本项目不使用传统数据库，所有持久化基于**文件系统**：

```
~/.claude/
├── settings.json                    # 全局用户设置
├── adapters.json                    # IM 适配器配置
├── adapter-sessions.json            # 适配器会话绑定
│
├── projects/                        # 按项目 (MD5 路径) 组织
│   └── <project-hash>/
│       └── <session-id>.jsonl       # 会话消息 (JSONL 格式，每行一条)
│
├── cc-haha/
│   ├── settings.json                # 桌面端管理设置
│   ├── providers.json               # AI 提供商配置
│   ├── plugins/                     # 安装的插件
│   ├── *oauth*.json                 # OAuth Token 存储
│   └── sessions/                    # 桌面端会话元数据
│
├── skills/                          # 用户级别技能
│   └── <skill-name>/
│       └── SKILL.md
│
├── plans/                           # 计划文件
│   └── <plan-name>.md
│
├── tasks/                           # 任务输出
│   └── <task-id>/
│       └── output
│
└── memories/                        # 记忆文件
```

### 4.2 项目级配置

```
<project-root>/.claude/
├── settings.json                    # 项目级别设置
└── skills/                          # 项目级别技能
    └── <skill-name>/
        └── SKILL.md
```

### 4.3 会话 JSONL 文件格式

每行一条消息 JSON 对象：

```json
{
  "id": "msg_xxx",
  "type": "user" | "assistant" | "system" | "attachment",
  "content": [{"type": "text", "text": "..."} | {"type": "tool_use", ...} | {"type": "tool_result", ...}],
  "timestamp": 1234567890,
  "usage": {"input_tokens": 100, "output_tokens": 50},
  "model": "claude-sonnet-4-20250514"
}
```

### 4.4 Settings JSON 结构

```json
{
  "theme": "dark" | "light" | "system",
  "preferredNotifChannel": "iterm2" | "terminal_bell" | "desktop",
  "hasSeenV4ReleaseNotes": false,
  "installMethod": "bun" | "npm",
  "numStartups": 42,
  "userID": "hash_value",
  "firstStartTime": 1700000000000,
  "oAuthAccount": {
    "email": "...",
    "accountUuid": "...",
    "organizationUuid": "..."
  }
}
```

### 4.5 Provider 配置结构

```json
{
  "schemaVersion": 1,
  "activeId": "provider_xxx",
  "providers": [
    {
      "id": "provider_xxx",
      "name": "My Provider",
      "apiFormat": "anthropic" | "openai_chat" | "openai_responses",
      "baseUrl": "https://api.example.com",
      "apiKey": "sk-xxx",
      "authStrategy": "api_key" | "bearer",
      "skipMcpAuth": false,
      "models": [
        {
          "id": "model-id",
          "name": "Model Display Name",
          "apiFormat": "anthropic",
          "reasoning": false,
          "inputContextLimit": 200000,
          "maxOutput": 8192
        }
      ]
    }
  ]
}
```

### 4.6 Adapter 配置结构

```json
{
  "serverUrl": "http://127.0.0.1:3456",
  "telegram": {
    "botToken": "...",
    "allowedUsers": [],
    "workDir": "/path/to/project"
  },
  "feishu": {
    "appId": "...",
    "appSecret": "...",
    "workDir": "/path/to/project"
  },
  "wechat": {
    "botToken": "...",
    "accountId": "...",
    "baseUrl": "http://127.0.0.1:8099",
    "workDir": "/path/to/project"
  },
  "dingtalk": {
    "clientId": "...",
    "clientSecret": "...",
    "workDir": "/path/to/project"
  }
}
```

### 4.7 桌面端 IndexedDB (Tauri WebView)

桌面端浏览器 `localStorage` 存储以下临时状态：
- `cc-haha-tabs` — 标签页状态
- `cc-haha-sidebar-open` — 侧边栏状态
- `cc-haha-terminal-panel` — 终端面板状态
- `cc-haha-workspace-panel` — 工作区面板状态

### 4.8 静态资源

```
desktop/public/
├── fonts/
│   ├── inter-latin.woff2
│   ├── manrope-latin.woff2
│   ├── jetbrains-mono-latin.woff2
│   └── material-symbols-outlined.woff2
├── favicon.ico
├── logo.svg
└── icons/
```

### 4.9 第三方服务依赖

| 服务 | 用途 | 配置 |
|------|------|------|
| Anthropic API | AI 模型调用 | `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` |
| OpenAI API | AI 模型调用 (通过代理) | `OPENAI_API_KEY` |
| GitHub | 更新检查 / 发布 | `cc-haha/releases` |
| Google Analytics | 遥测 (可选) | `GA_TRACKING_ID` |
| GrowthBook | 特性开关 | `GROWTHBOOK_CLIENT_KEY` |

---

## 五、跨项目复现步骤说明

### 5.1 环境搭建

#### 5.1.1 基础环境

```bash
# 1. 安装 Bun 运行时
# macOS/Linux:
curl -fsSL https://bun.sh/install | bash
# Windows:
powershell -c "irm bun.sh/install.ps1 | iex"

# 2. 安装 Rust 工具链 (桌面端需要)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# 3. 安装 Node.js 22+ (文档构建需要)
# 推荐使用 nvm 或 fnm 管理

# 4. 安装平台特定依赖
# macOS: Xcode Command Line Tools
# Windows: Visual Studio Build Tools (带 C++ 工作负载), WebView2 Runtime
# Linux: libwebkit2gtk, libgtk-3, libappindicator 等
```

#### 5.1.2 项目克隆与依赖安装

```bash
git clone <repository-url> cc-haha
cd cc-haha

# 根目录依赖
bun install

# 桌面端依赖
cd desktop && bun install && cd ..

# 适配器依赖
cd adapters && bun install && cd ..
```

### 5.2 配置文件修改

#### 5.2.1 环境变量

复制 `.env.example` 为 `.env`：

```bash
cp .env.example .env
```

编辑 `.env`，填写必需配置：

```env
# === 必需: API 配置 ===
ANTHROPIC_AUTH_TOKEN=your_api_key_here
ANTHROPIC_BASE_URL=https://api.anthropic.com
ANTHROPIC_MODEL=claude-sonnet-4-20250514

# === 推荐: 性能优化 ===
NODE_OPTIONS=--max-old-space-size=8192

# === 可选: 遥测控制 ===
DISABLE_TELEMETRY=1
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
```

#### 5.2.2 全局设置

创建 `~/.claude/settings.json`：

```json
{
  "theme": "dark",
  "preferredNotifChannel": "iterm2",
  "hasSeenV4ReleaseNotes": true,
  "installMethod": "bun"
}
```

#### 5.2.3 提供商配置

在不使用 Anthropic 官方 API 时，通过桌面端 Settings → Providers 配置，或手动编辑：

```bash
# 文件路径: ~/.claude/cc-haha/providers.json
```

### 5.3 启动服务

```bash
# 1. 启动本地服务端 (3456 端口)
SERVER_PORT=3456 bun run src/server/index.ts

# 2. 新开终端，启动 CLI
bun run start

# 3. (可选) 启动桌面端开发模式
cd desktop && bun run dev

# 4. (可选) 启动 IM 适配器
cd adapters
FEISHU_APP_ID=xxx FEISHU_APP_SECRET=xxx bun run feishu/index.ts
```

### 5.4 验证清单

| 验证项 | 命令 | 预期 |
|--------|------|------|
| CLI 启动 | `bun run start` | 进入 REPL |
| 服务端启动 | `SERVER_PORT=3456 bun run src/server/index.ts` | 监听 3456 |
| 桌面端开发 | `cd desktop && bun run dev` | Vite Dev Server 1420 |
| 桌面端构建 | `cd desktop && bun run build` | `dist/` 产物 |
| 服务端测试 | `bun run check:server` | 测试全部通过 |
| 桌面端测试 | `cd desktop && bun run test -- --run` | 测试全部通过 |
| 适配器测试 | `cd adapters && bun run test` | 测试全部通过 |
| 代码审查 | `bun run verify` | 质量门全部通过 |
| 剪枝测试 | `bun run quality:smoke --provider-model claude:sonnet` | 对话正常 |

### 5.5 代码迁移要点

1. **目录结构映射：**

| 源目录 | 目标目录 | 说明 |
|--------|----------|------|
| `src/` | `src/` | CLI 核心逻辑 |
| `bin/` | `bin/` | 可执行入口 |
| `desktop/src/` | `desktop/src/` | 桌面 UI |
| `desktop/src-tauri/` | `desktop/src-tauri/` | Tauri 原生代码 |
| `adapters/` | `adapters/` | IM 适配器 |
| `scripts/` | `scripts/` | 构建/质量脚本 |
| `.github/workflows/` | `.github/workflows/` | CI/CD |

2. **核心文件不能缺失：**
   - `src/entrypoints/cli.tsx` — CLI 入口
   - `src/entrypoints/init.ts` — 系统初始化
   - `src/main.tsx` — 主 CLI 逻辑
   - `src/server/index.ts` — 本地服务端
   - `src/Tool.ts` — 工具抽象
   - `src/QueryEngine.ts` — 对话引擎
   - `src/Task.ts` — 任务管理
   - `desktop/src/main.tsx` — 桌面端入口
   - `desktop/src/App.tsx` — 桌面端根组件

3. **路径依赖修改：**
   - `tsconfig.json` → `paths` 别名映射
   - 所有 `import` 路径基于 tsconfig paths
   - `~/.claude/` 配置目录路径 (硬编码在 `envUtils.ts`)
   - `package.json` → `name` 字段影响路径解析

4. **包名修改清单：**
   - `package.json` → `name`
   - `bin/claude-haha` → 可执行文件名
   - `desktop/src-tauri/tauri.conf.json` → `productName`, `identifier`
   - `desktop/src-tauri/Cargo.toml` → `name`
   - 所有 `import.meta.env.VITE_*` 前缀

### 5.6 数据库适配

此项目不使用传统数据库，需适配的文件系统路径：

1. **配置目录：** `getClaudeConfigHomeDir()` → 默认 `~/.claude/`
2. **项目缓存：** `~/.claude/projects/<md5(project-path)>/`
3. **桌面存储：** Tauri WebView `localStorage`
4. **oompact :** 通用记忆化工具 (WeakRef)

### 5.7 功能调试要点

1. **CLI 调试：**
   ```bash
   DEBUG=1 bun run start
   # 启用详细日志
   ```

2. **服务端调试：**
   ```bash
   SERVER_LOG_LEVEL=debug SERVER_PORT=3456 bun run src/server/index.ts
   ```

3. **桌面端调试：**
   - Chrome DevTools: Vite Dev Server 自动开启
   - Tauri DevTools: `cd desktop && bun run tauri dev`

4. **WebSocket 调试：**
   ```bash
   # 服务端日志会显示所有 WS 连接/断开/消息
   ```

5. **MCP 调试：**
   ```bash
   MCP_DEBUG=1 bun run start
   ```

### 5.8 避坑要点

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| `bun:bundle` feature 宏报错 | 仅在 Bun 运行时可用 | 使用 Bun 而不是 Node.js |
| Yoga 布局引擎构建失败 | 原生模块编译 | 确保 `@types/react` 安装正确 |
| Tauri 构建缺少 WebView2 | Windows 缺少运行时 | 安装 WebView2 Runtime |
| `.env` 未加载 | Bun 不自动加载 | 手动 `source` 或使用 `dotenv` |
| `OPENAI_API_KEY` 未设置 | 代理模式需要 | 配置文件设置 Provider |
| 代理连接失败 | 网络环境受限 | 设置 `HTTP_PROXY`/`HTTPS_PROXY` |
| 文件权限错误 | Unix 权限问题 | `chmod +x bin/claude-haha` |
| MCP 连接超时 | 子进程未启动 | 检查 MCP 命令路径 |
| 国际化文本缺失 | 翻译文件不完整 | 补充 `desktop/src/i18n/` 翻译 |

### 5.9 最小可运行版本构建

如需制作最小可运行版本 (stripped-down)：

```bash
# 1. 仅保留 CLI 核心 (不需要桌面端)
bun run scripts/create-minimal-stub.ts

# 2. 手动清理
# 删除 desktop/ (不需要 GUI)
# 删除 adapters/ (不需要 IM)
# 删除遥测相关 (不需要分析)
# 保留 src/ 核心逻辑
```

### 5.10 发布构建

```bash
# 桌面端生产构建
cd desktop
bun run build              # 前端构建
bun run build:sidecars      # 旁路二进制
bun run tauri build         # Tauri 打包

# 产物位置
desktop/src-tauri/target/release/bundle/
```

---

## 附录 A：完整文件清单

### A.1 核心入口
- `bin/claude-haha` — 可执行脚本
- `src/entrypoints/cli.tsx` — CLI 参数路由
- `src/entrypoints/init.ts` — 系统初始化
- `src/main.tsx` — 主 CLI 逻辑
- `src/entrypoints/integrity.ts` — 完整性校验

### A.2 服务端核心
- `src/server/index.ts` — HTTP/WS 服务入口
- `src/server/router.ts` — API 路由
- `src/server/ws/handler.ts` — WebSocket 处理
- `src/server/proxy/handler.ts` — API 代理
- `src/server/services/conversationService.ts` — CLI 子进程管理
- `src/server/services/sessionService.ts` — 会话持久化
- `src/server/services/providerService.ts` — 提供商管理
- `src/server/services/teamWatcher.ts` — 团队监听
- `src/server/services/cronScheduler.ts` — 定时任务
- `src/server/config/providerPresets.json` — 预设模板

### A.3 核心引擎
- `src/QueryEngine.ts` — 对话引擎
- `src/Tool.ts` — 工具抽象
- `src/Task.ts` — 任务管理
- `src/commands.ts` — 命令系统
- `src/commands/` — 命令实现目录
- `src/hooks/` — React Hooks

### A.4 MCP 系统
- `src/services/mcp/client.ts` — MCP 客户端
- `src/services/mcp/config.ts` — MCP 配置
- `src/services/mcp/auth.ts` — MCP OAuth
- `src/tools/MCPTool/` — MCP 工具包装
- `src/tools/ReadMcpResourceTool/` — MCP 资源读取

### A.5 代理转换
- `src/server/proxy/anthropicToOpenaiChat.ts`
- `src/server/proxy/anthropicToOpenaiResponses.ts`
- `src/server/proxy/openaiChatToAnthropic.ts`
- `src/server/proxy/openaiResponsesToAnthropic.ts`
- `src/server/proxy/openaiChatStreamToAnthropic.ts`
- `src/server/proxy/openaiResponsesStreamToAnthropic.ts`

### A.6 桌面端核心
- `desktop/src/main.tsx` — 桌面端入口
- `desktop/src/App.tsx` — 根组件
- `desktop/src/api/client.ts` — API 客户端
- `desktop/src/components/layout/AppShell.tsx` — 应用壳
- `desktop/src/stores/` — 所有 Zustand Store
- `desktop/src/pages/` — 所有页面
- `desktop/src-tauri/Cargo.toml` — Rust 依赖
- `desktop/src-tauri/tauri.conf.json` — Tauri 配置

### A.7 适配器核心
- `adapters/common/ws-bridge.ts` — WebSocket 桥接
- `adapters/common/config.ts` — 配置加载
- `adapters/common/format.ts` — 消息格式化
- `adapters/common/permission.ts` — 权限处理
- `adapters/common/http-client.ts` — HTTP 客户端
- `adapters/telegram/index.ts` — Telegram 适配器
- `adapters/feishu/index.ts` — 飞书适配器
- `adapters/wechat/index.ts` — 微信适配器
- `adapters/dingtalk/index.ts` — 钉钉适配器

### A.8 自研 Ink 框架
- `src/ink/ink.tsx` — 渲染入口
- `src/ink/reconciler.ts` — 自定义渲染器
- `src/ink/renderer.ts` — 终端渲染器
- `src/ink/dom.ts` — 虚拟 DOM
- `src/ink/layout/` — 布局引擎
- `src/ink/termio/` — 终端 I/O
- `src/ink/events/` — 事件系统
- `src/ink/hooks/` — React Hooks

---

## 附录 B：构建命令速查表

| 命令 | 说明 | 耗时 |
|------|------|------|
| `bun run start` | 启动 CLI | < 1s |
| `SERVER_PORT=3456 bun run src/server/index.ts` | 启动服务端 | < 2s |
| `cd desktop && bun run dev` | 桌面端开发模式 | < 3s |
| `cd desktop && bun run build` | 桌面端生产构建 | ~30s |
| `cd desktop && bun run test -- --run` | 桌面端单测 | ~10s |
| `bun run check:server` | 服务端测试 | ~20s |
| `cd adapters && bun run test` | 适配器测试 | ~10s |
| `bun run verify` | 完整质量门 | ~5min |
| `bun run quality:push` | 预推送质量门 | ~2min |
| `bun run docs:dev` | 文档预览 | < 5s |
| `bun run docs:build` | 文档构建 | ~30s |

---

*文档版本：1.0 — 基于项目 v0.3.1 代码快照生成*
*最后更新：2026-05-29*