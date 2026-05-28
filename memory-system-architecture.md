# Claude Code Haha — 记忆系统架构文档

## 概述

Claude Code Haha 实现了一套 **跨会话、基于文件、AI 自主管理** 的持久化记忆系统。系统分为 **五大核心模块** 与 **五大辅助模块**，覆盖记忆的存储、读取、写入、整合与同步全链路。

整个系统遵循一个核心设计原则：**只保存无法从当前项目状态推导出的信息**。代码结构、Git 历史、文件路径等内容被视为"可推导信息"，不应写入记忆。

---

## 一、核心模块（`src/memdir/`）

### 1.1 记忆类型系统 — `memoryTypes.ts`

记忆被严格限定为四种类型：

| 类型 | 含义 | 示例 |
|------|------|------|
| `user` | 用户的角色、目标、技能与偏好 | "用户是资深 Go 开发者，React 新手" |
| `feedback` | 用户对工作方式的纠正或肯定 | "不要 mock 数据库——之前出过生产事故" |
| `project` | 无法从代码推导的项目上下文 | "3月5日后冻结合并，移动团队发版" |
| `reference` | 指向外部系统的指针 | "Pipeline 工单在 Linear 的 INGEST 项目" |

每个记忆文件使用 **YAML frontmatter** 格式：

```markdown
---
name: memory-name
description: 一行描述，用于后续判断相关性
type: user | feedback | project | reference
---

记忆内容正文...
```

系统提供两套提示词模板：
- **`TYPES_SECTION_COMBINED`** — 个人+团队双目录模式，包含 `<scope>` 标签
- **`TYPES_SECTION_INDIVIDUAL`** — 单目录模式，不含 scope

以及配套的 `WHAT_NOT_TO_SAVE_SECTION`（什么不该存）、`WHEN_TO_ACCESS_SECTION`（何时读取）和 `TRUSTING_RECALL_SECTION`（如何验证回忆）指令段。

### 1.2 路径解析 — `paths.ts`

记忆存储路径的解析优先级（从高到低）：

1. **`CLAUDE_COWORK_MEMORY_PATH_OVERRIDE`** 环境变量（Cowork 用，完整路径覆盖）
2. **`autoMemoryDirectory`** 设置项（来自 `settings.json`，支持 `~/` 展开）
3. **默认路径**：`~/.claude/projects/<sanitized-git-root>/memory/`

关键函数：
- `getAutoMemPath()` — 获取记忆目录路径（memoized，按 projectRoot 缓存）
- `getAutoMemEntrypoint()` — 获取 `MEMORY.md` 索引文件路径
- `getAutoMemDailyLogPath()` — 获取每日日志路径（`logs/YYYY/MM/YYYY-MM-DD.md`）
- `isAutoMemPath()` — 判断路径是否属于记忆目录（安全校验用）
- `isAutoMemoryEnabled()` — 判断自动记忆是否启用

**启用条件**：默认启用，以下情况禁用：
- `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`
- `CLAUDE_CODE_SIMPLE`（bare 模式）
- 远程模式且无 `CLAUDE_CODE_REMOTE_MEMORY_DIR`
- `autoMemoryEnabled: false` 设置项

**安全校验**（`validateMemoryPath`）：
- 拒绝相对路径、根目录、Windows 驱动器根、UNC 路径、含 null 字节的路径
- `projectSettings`（项目级 `.claude/settings.json`）被有意排除在设置覆盖源之外，防止恶意仓库重定向记忆目录

### 1.3 系统提示词注入 — `memdir.ts`

`loadMemoryPrompt()` 是记忆系统与系统提示词的接口。在启动时调用一次，根据启用的功能分发：

```
┌─────────────────────────────────────────────┐
│ loadMemoryPrompt()                          │
│                                             │
│  ├─ KAIROS + autoEnabled + kairosActive     │
│  │   → buildAssistantDailyLogPrompt()       │
│  │     (追加日志模式)                        │
│  │                                           │
│  ├─ TEAMMEM + teamEnabled                   │
│  │   → buildCombinedMemoryPrompt()          │
│  │     (个人+团队双目录)                     │
│  │                                           │
│  ├─ autoEnabled                             │
│  │   → buildMemoryLines()                   │
│  │     (单目录模式)                          │
│  │                                           │
│  └─ 全部禁用 → 返回 null                     │
└─────────────────────────────────────────────┘
```

**MEMORY.md 索引双重截断保护**（`truncateEntrypointContent`）：
- 最多 **200 行**
- 最多 **25,000 字节**
- 超出任一限制时，在内容末尾追加警告，说明触发了哪个上限

**目录自动创建**：`ensureMemoryDirExists()` 在提示词加载时自动创建记忆目录，确保模型可以直接写入而无需先检查存在性。

### 1.4 记忆扫描 — `memoryScan.ts`

`scanMemoryFiles()` 扫描记忆目录中的所有 `.md` 文件（排除 `MEMORY.md`）：

- 最大扫描 **200 个文件**
- 读取每个文件前 **30 行** 以提取 frontmatter
- 返回 `MemoryHeader[]`：包含文件名、路径、mtime、description、type
- 按 mtime **最新优先排序**
- 使用 `readFileInRange` 单遍完成读取+stat，避免双倍系统调用

`formatMemoryManifest()` 将扫描结果格式化为文本清单，供提示词使用。

### 1.5 智能检索 — `findRelevantMemories.ts`

当需要检索相关记忆时，系统不是简单关键词匹配，而是：

1. 调用 `scanMemoryFiles()` 获取所有记忆文件头部信息
2. 将 `(文件名 + description + 类型)` 清单发送给 **Sonnet 模型**
3. Sonnet 根据当前用户查询选择最相关的记忆（最多 **5 个**）
4. 返回匹配文件的绝对路径和 mtime

额外特性：
- **`alreadySurfaced`** 参数：过滤之前已展示过的路径，避免重复
- **工具感知**：如果近期使用了某些工具，则过滤掉对应工具的使用参考/API 文档类记忆
- **遥测**：受 `MEMORY_SHAPE_TELEMETRY` 构建标志控制，记录召回形状

选择提示词的关键指令：
> "如果你不确定某个记忆是否有用，就不要包含它。要挑剔和有辨别力。"

### 1.6 记忆新鲜度 — `memoryAge.ts`

| 年龄 | 行为 |
|------|------|
| 当天 | 无警告 |
| 昨天 | 无警告 |
| 超过 1 天 | 附加"此记忆已有 X 天"警告，提示验证 |

`memoryFreshnessText()` 返回纯文本警告，`memoryFreshnessNote()` 将其包装在 `<system-reminder>` 标签中。

---

## 二、辅助模块

### 2.1 自动记忆提取 — `extractMemories.ts`

**触发时机**：每次对话循环结束时（模型输出无工具调用的最终响应时），通过 `handleStopHooks` 触发。

**运作机制**：
1. 在启动时通过 `initExtractMemories()` 初始化（闭包作用域状态）
2. 使用 **forked agent 模式** — 主对话的完美分支，共享父进程的 prompt cache
3. 光标追踪（`lastMemoryMessageUuid`）确保每次只处理新增消息
4. **互斥逻辑**：如果主 agent 已经写入了记忆文件，跳过本次提取并推进光标
5. 支持 **节流**（`tengu_bramble_lintel`，默认每 1 轮运行一次）
6. **后进追赶**：正在运行时如果新请求到达，暂存 context，当前运行结束后自动追赶

**工具权限**（`createAutoMemCanUseTool`）：
| 工具 | 权限 |
|------|------|
| Read / Grep / Glob | ✅ 无限制 |
| Bash | ✅ 仅只读命令 |
| Edit / Write | ✅ 仅在记忆目录路径内 |
| 其他所有工具 | ❌ 拒绝 |

最大 5 轮对话限制，防止验证循环浪费 token。

### 2.2 AutoDream 记忆整合 — `autoDream.ts`

AutoDream（"做梦"）是一个后台记忆整合机制，定期回顾多个会话，整合、去重和修剪记忆。

**五重门控**（从快到慢依次判断）：

```
请求到达
  │
  ├─ ① KAIROS/远程/记忆禁用？ → 跳过
  ├─ ② 距上次整合 < 24 小时？ → 跳过（tengu_onyx_plover 配置）
  ├─ ③ 距上次扫描 < 10 分钟？ → 跳过（扫描节流）
  ├─ ④ 新会话数 < 5 个？ → 跳过
  └─ ⑤ PID 锁文件有其他进程在跑？ → 跳过
       │
       ↓ 执行整合
```

**四阶段流程**（通过 forked agent 执行 `/dream` 提示词）：

1. **方向（Orient）** — `ls` 目录，阅读现有文件
2. **收集近期信号（Gather）** — 每日日志、漂移的记忆、会话搜索
3. **整合（Consolidate）** — 合并到现有主题文件，更新日期，删除过时内容
4. **修剪和索引（Prune & Index）** — 保持 MEMORY.md 在限制内，删除过期指针

**UI 集成**（`DreamTask.ts`）：
- 底部显示 "dreaming" 标签
- Shift+Down 可查看详情对话框
- 完成后显示完成通知

### 2.3 会话记忆 — `sessionMemory.ts`

**触发时机**：在每次采样后（post-sampling hook）运行。

**功能**：自动维护当前对话的结构化笔记，保存到 `~/.claude/session-memory/session.md`。

**章节模板**：
Session Title、Current State、Task specification、Files and Functions、Workflow、Errors & Corrections、Codebase and System Documentation、Learnings、Key results、Worklog

**触发阈值**：
- 初始化：10,000 tokens
- 更新间隔：5,000 tokens + 3 次工具调用
- 最后一次助手轮次无工具调用时也会触发

**与压缩的集成**：当 `tengu_session_memory` + `tengu_sm_compact` 功能开关启用时，会话记忆替代传统 API 压缩。

### 2.4 团队记忆同步 — `teamMemorySync/`

基于 OAuth 的团队记忆文件远程同步服务。

**API 合约**：
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/claude_code/team_memory?repo={owner/repo}` | 获取条目 + 校验和 |
| GET | `/api/claude_code/team_memory?repo=...&view=hashes` | 仅获取元数据 |
| PUT | `/api/claude_code/team_memory?repo=...` | 上传（upsert 语义） |

**同步语义**：
- **拉取**：服务器内容覆盖本地文件
- **推送**：增量上传（仅哈希不同的键），ETag 乐观锁冲突解决
- 删除不会传播

**安全措施**：
- 基于 gitleaks 的秘密扫描（PSR M22174）
- 路径遍历防护（`validateTeamMemKey`, `validateTeamMemWritePath`）
- 符号链接遍历检测（PSR M22186）
- 上传大小限制：单文件 250KB，单次请求 200KB

### 2.5 代理记忆 — `agentMemory.ts`

子代理拥有自己的隔离记忆，支持三级作用域：

| 作用域 | 路径 | 范围 |
|--------|------|------|
| `user` | `~/.claude/agent-memory/<agentType>/` | 跨项目 |
| `project` | `.claude/agent-memory/<agentType>/` | 项目特定，可共享 |
| `local` | `.claude/agent-memory-local/<agentType>/` | 项目 + 机器特定 |

代理创建向导中的 `MemoryStep` 组件让用户选择作用域。

---

## 三、存储结构

```
~/.claude/
├── projects/
│   └── <sanitized-git-root>/
│       └── memory/
│           ├── MEMORY.md              # 索引文件（始终加载到上下文）
│           ├── user_role.md           # 用户类型记忆
│           ├── feedback_testing.md    # 反馈类型记忆
│           ├── project_deadlines.md   # 项目类型记忆
│           ├── reference_dashboards.md # 引用类型记忆
│           ├── team/                  # 团队记忆（可选）
│           └── logs/                  # KAIROS 模式的每日日志
│               └── YYYY/MM/YYYY-MM-DD.md
├── agent-memory/
│   └── <agentType>/
│       └── ...                        # user 作用域代理记忆
└── session-memory/
    └── session.md                    # 当前会话笔记
```

---

## 四、数据流

### 写入流程

```
用户对话
  │
  ├─ 主 Agent 直接写入 ──────────────┐
  │   （系统提示词包含完整保存指令）   │  → Write/Edit → memory/*.md
  │                                  │
  └─ 对话结束 → handleStopHooks ─────┤
      │                              │
      ├─ extractMemories             │
      │  （forked agent 读取对话，     │
      │   提取记忆写入文件）           │  → Write → memory/*.md
      │                              │
      └─ autoDream                   │
         （后台整合，周期触发）        │  → Edit/Write → 合并/修剪记忆
```

**互斥保证**：当主 agent 已写入记忆文件时，extractMemories 检测到文件修改则跳过本次运行（`hasMemoryWritesSince`）。

### 读取流程

```
启动时
  │
  └─ loadMemoryPrompt()
      │
      └─ 读取 MEMORY.md 索引
          │
          └─ 注入系统提示词
              │
              对话过程中
              │
              └─ findRelevantMemories(query)
                  │
                  ├─ scanMemoryFiles() → 扫描所有记忆文件头
                  └─ Sonnet 选择最多 5 个相关记忆
```

---

## 五、REST API 与桌面 UI

### 服务器 API（`src/server/api/memory.ts`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/memory/projects?cwd=...` | 列出项目级记忆目录 |
| GET | `/api/memory/files?projectId=...` | 列出 MD 记忆文件 |
| GET | `/api/memory/file?projectId=...&path=...` | 读取单个文件 |
| PUT | `/api/memory/file` | 写入/更新记忆文件 |

**安全措施**：路径遍历保护、真实路径解析、文件大小限制 512KB、文件数量限制 500。

### 桌面 UI（`desktop/src/pages/MemorySettings.tsx`）

完整的记忆设置页面，包含树状文件浏览器、搜索、面包屑导航、Markdown 渲染。

### CLI 命令

- **`/memory`** — 打开记忆文件选择器，用 `$EDITOR` 编辑
- **`/remember`** — 审查自动记忆条目，提议提升到 `CLAUDE.md`/`CLAUDE.local.md`

---

## 六、构建标志与功能开关

### Bun 构建标志（`feature()`）

| 标志 | 用途 |
|------|------|
| `TEAMMEM` | 团队记忆支持 |
| `KAIROS` | 助手模式每日日志 |
| `EXTRACT_MEMORIES` | 后台记忆提取代理 |
| `MEMORY_SHAPE_TELEMETRY` | 记忆使用遥测 |

### GrowthBook 远程功能开关

| 开关 | 用途 |
|------|------|
| `tengu_passport_quail` | 启用 extractMemories |
| `tengu_herring_clock` | 启用团队记忆 |
| `tengu_onyx_plover` | AutoDream 配置（minHours, minSessions） |
| `tengu_session_memory` | 启用会话记忆 |
| `tengu_sm_compact` | 会话记忆压缩替代传统压缩 |
| `tengu_moth_copse` | 跳过 MEMORY.md 索引维护 |
| `tengu_bramble_lintel` | 提取节流阈值 |
| `tengu_coral_fern` | 搜索过去上下文功能 |
| `tengu_slate_thimble` | 非交互式会话中启用提取 |

---

## 七、设计亮点

1. **forked agent 模式**：后台记忆操作（提取、整合）使用主 Agent 的完美分支，共享 prompt cache，效率高
2. **双重互斥**：主 Agent 写入 vs 后台提取互斥；多进程 AutoDream 通过 PID 锁互斥
3. **光标追踪**：每次提取记录 `lastMemoryMessageUuid`，只处理新增消息
4. **Sonnet 语义检索**：不依赖关键词匹配，用 Sonnet 模型理解记忆内容与查询的语义相关性
5. **新鲜度感知**：超过 1 天的记忆自动附加过期警告，防止模型盲目相信陈旧信息
6. **安全优先**：路径遍历防护、秘密扫描、工具权限最小化、绕过恶意仓库的路径重定向
7. **渐进式复杂度**：个人模式 → 团队模式 → KAIROS 日志模式，同一套底层存储兼容不同场景

---

## 八、测试覆盖

- `src/server/__tests__/memory.test.ts` — API 层测试
- `src/server/__tests__/ws-memory-events.test.ts` — WebSocket 事件测试
- `desktop/src/__tests__/memorySettings.test.tsx` — UI 组件测试