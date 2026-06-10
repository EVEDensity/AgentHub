# AgentHub AI 辅助开发协作总结

## 1. 项目开发全景

### 1.1 项目规模

| 维度 | 数据 |
|---|---|
| 开发周期 | 多轮迭代（30+ commits） |
| 技术栈 | Python FastAPI 后端 + Next.js 13 前端 + Neon PostgreSQL |
| 核心模块数 | 15+ 服务模块、50+ 内置工具、8 个 LLM 适配器 |
| AI 协作会话 | 多轮跨会话（含 context compaction 接续） |

### 1.2 开发过程中的 AI 协作角色

```
┌─────────────────────────────────────────────────────┐
│              人类开发者 (决策者)                       │
│  · 确定方向   · 做出选择   · 审查结果   · 部署执行     │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│             Claude Code (AI 开发伙伴)                 │
│                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │ 需求分析  │ │ 架构设计  │ │ 代码实现  │ │ 故障诊断 │ │
│  │ · 代码库  │ │ · Plan   │ │ · 前端UI  │ │ · 根因   │ │
│  │   探索    │ │   Mode   │ │   重写    │ │   追溯   │ │
│  │ · 端到端  │ │ · 方案   │ │ · 后端    │ │ · 全链   │ │
│  │   追踪    │ │   比选   │ │   Bugfix  │ │   验证   │ │
│  └──────────┘ └──────────┘ └──────────┘ └─────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │ 性能优化  │ │ 代码审查  │ │ 文档生成  │ │ 规范遵守 │ │
│  │ · 包体积  │ │ · 多文件  │ │ · Spec   │ │ · Rules  │ │
│  │ · 滚动    │ │   一致性  │ │   总结    │ │   约束   │ │
│  │ · 订阅    │ │ · 安全   │ │ · 知识   │ │ · Skill  │ │
│  └──────────┘ └──────────┘ └──────────┘ └─────────┘ │
└─────────────────────────────────────────────────────┘
```

---

## 2. 开发协作范式

### 2.1 四大协作范式总览

| # | 范式 | 典型场景 | 关键工具/方法 |
|---|---|---|---|
| 1 | **探索-诊断-修复循环** | Agent 无法对话的端到端排查 | Agent(Explore)、Grep、Read |
| 2 | **Plan-First 设计实现** | Admin 侧边栏重构、弹窗组件 | EnterPlanMode、Write、Edit |
| 3 | **Skill-Driven 专项执行** | UI 设计规范落地 | Skill(frontend-design) |
| 4 | **持续性增量迭代** | 跨会话开发、上下文接续 | /compact、Memory、CLAUDE.md |

### 2.2 范式一：探索-诊断-修复循环

**代表场景**：排查「Agent 无法进行对话」问题

```
用户反馈 → 大规模代码库探索 (Explore Agent)
         → 多路径并行搜索 (Grep × N)
         → 端到端调用链追踪
         → 根因定位 (MockAdapter fallback)
         → 精准修复 (单文件 2 处改动)
         → 语法验证 + 运行时验证
```

**关键特征**：
- **广度优先**：先理解全局架构（路由注册 → WebSocket 处理 → Agent 调用 → 适配器层）
- **深度追踪**：沿 `handleSend` → `connectWs` → `_process_and_stream` → `_invoke_agent` → `stream_agent_response` → `_run_tool_call_loop` → `adapter.execute_prompt` 完整链路逐级排查
- **并行探索**：多个 Grep/Read 同时发起，缩短诊断时间
- **精准修复**：只改必要的最小范围（`execute_prompt` 签名 + `stream_prompt` payload 构建）

### 2.3 范式二：Plan-First 设计实现

**代表场景**：Admin 管理后台侧边栏重设计 + AgentEditModal 弹窗重构

工作流程：

```
Step 1: 进入 Plan Mode (EnterPlanMode)
        · 阅读现有关键文件
        · 理解设计系统 (CSS 变量、data-theme)
        · 确认组件依赖关系

Step 2: 编写 Plan 文档
        · 明确涉及文件（新建/修改/参考）
        · 定义 Props 接口
        · 规划组件结构
        · 列出验证方案

Step 3: 用户审批 (ExitPlanMode)
        · Plan 文档展示给开发者
        · 开发者确认或提出调整

Step 4: 逐步实现
        · 按 Plan 顺序逐文件修改
        · TodoWrite 追踪进度
        · 每步完成后验证

Step 5: 构建验证
        · 编译检查
        · 清理构建缓存
        · 重启服务验证
```

**Plan 文档示例**（`precious-snacking-pumpkin.md`）：

```markdown
# 服务商编辑弹窗重构计划

## 涉及文件
### 新建文件
1. `frontend/components/admin/AgentEditModal.tsx`
### 修改文件
2. `frontend/components/admin/ServiceProviderModule.tsx`
3. `frontend/stores/agentStore.ts`

## 实现方案
### 1. 创建 AgentEditModal.tsx
- fixed inset-0 z-50 覆盖层
- 白色圆角卡片 rounded-2xl
- Header + Body + Footer 结构
- 支持 create / edit 两种模式

### 2. 修改 ServiceProviderModule.tsx
- 移除内联表单（~130 行）
- 替换为 <AgentEditModal> 调用
```

### 2.4 范式三：Skill-Driven 专项执行

**代表场景**：管理控制台左边栏 UI 重设计

通过 `/frontend-design` Skill 调用，将前端设计规范系统化地注入到代码实现中：

```
Skill 触发 → 设计 Thinking 阶段
           · Purpose: 管理员日常配置与监控
           · Tone: 暗色主题 + editorial-grade 导航
           · Constraints: Next.js App Router、Tailwind CSS
           · Differentiation: 3px 左侧边框活跃指示器 + 发光圆点

          → 实现阶段
           · MenuItemMeta 接口定义
           · MENU_GROUPS 分组结构 (核心配置/能力扩展/系统运维)
           · 暗色侧边栏 CSS (bg-warm-900 + 半透明边框)
           · 品牌 Header (⚡ AgentHub 渐变)
           · HSL 渐变用户头像
           · 玻璃态 Header (backdrop-blur-sm)
```

**Skill 带来的价值**：
- 避免「AI slop」——不落入 Inter 字体、紫色渐变等套路
- 有明确的美学方向（editorial-grade, intentional darkness）
- 生成的是**可直接使用的 production-grade 代码**，不是概念稿

### 2.5 范式四：持续性增量迭代

**跨会话开发保障机制**：

| 机制 | 作用 | 示例 |
|---|---|---|
| **Memory 系统** | 持久化用户偏好与项目决策 | `memory/` 目录下结构化记忆文件 |
| **Context Compaction** | 长会话压缩后接续 | `/compact` → 从 `*.jsonl` 恢复上下文 |
| **Plan 文件** | 跨会话保留设计意图 | `plans/precious-snacking-pumpkin.md` |
| **Git 快照** | 每个里程碑可回溯 | 20+ commits，逐步迭代 |

---

## 3. 规范文档体系

### 3.1 文档金字塔

```
                 ┌─────────┐
                 │  Plan   │  ← 具体任务的设计方案
                 │  文档   │     (如 AgentEditModal 重构)
                 └────┬────┘
                      │
              ┌───────┴───────┐
              │  Spec/Schema  │  ← 数据契约 (AgentProtocol,
              │   定义文件     │     Artifact, MessageCard, DAG)
              └───────┬───────┘
                      │
          ┌───────────┴───────────┐
          │  类型定义 & 接口       │  ← TypeScript 接口、
          │  (types.ts, store)    │     Python Pydantic Model
          └───────────┬───────────┘
                      │
      ┌───────────────┴───────────────┐
      │  内联文档 (Docstrings,       │  ← 函数/类的用途说明
      │          行内注释)            │
      └───────────────────────────────┘
```

### 3.2 Plan 文档规范

Plan 文档是 AI 辅助开发中的**核心契约**，位于 `~/.claude/plans/` 目录：

**必备结构**：
```markdown
# {任务标题}

## Context
当前状态 + 存在的问题（2-3 句）

## 涉及文件
### 新建文件
### 修改文件
### 参考文件

## 实现方案
### 1. {子任务标题}
- 具体改动
- 代码结构（Props、组件结构）
- 关键设计决策

## 验证方案
1. 启动 dev server
2. 点击 → 确认 → 确认 → ...
```

**设计原则**：
- Plan 必须包含**可验证的检查点**
- 涉及多文件时**先列全，再逐个实现**
- 实现方案中标注**关键代码段**，不是伪代码

### 3.3 项目内核心 Schema 文件

| Schema | 文件 | 用途 | 开发中价值 |
|---|---|---|---|
| **AgentProtocol** | `app/schemas/agent_protocol.py` | Agent 接入契约 | 新增 Agent 时的字段清单 |
| **Artifact** | `app/schemas/artifact.py` | 产物统一抽象 | 预览/Diff 功能的数据契约 |
| **MessageCard** | `app/schemas/message_card.py` | 10 种消息类型 | 前端渲染的枚举参照 |
| **DAG** | `app/schemas/dag.py` | 任务图节点 | 多 Agent 调度的数据流 |

**开发中的实际用途**：
- 阅读 Schema 即可理解模块间**数据契约**
- 避免「猜测字段名」——直接参照 Pydantic Model
- 新增功能时先对齐 Schema，再写实现

### 3.4 类型定义层

**前端** (`frontend/types/`)：
```typescript
// Message、Session、AgentConfig 等核心类型
// Store 接口 (AdminStore, AgentStore, AuthStore)
// 确保 AI 生成的组件与现有类型系统兼容
```

**后端** (`app/schemas/`)：
```python
# Pydantic BaseModel 子类
# 自带验证、序列化、OpenAPI 文档生成
```

---

## 4. Skills 体系

### 4.1 在开发中使用的 Skills

| Skill | 触发方式 | 使用场景 | 产出 |
|---|---|---|---|
| **frontend-design** | `/frontend-design` | Admin 侧边栏 UI 重设计 | `layout.tsx` 完整重写 + `adminStore.ts` 增强 |
| **(implied) Plan** | EnterPlanMode | AgentEditModal 重构设计 | Plan 文档 + 3 文件修改 |

### 4.2 frontend-design Skill 的设计规范注入

该 Skill 的核心约束（来自 `~/.claude/skills/frontend-design/`）：

**设计 Thinking 前置**：
```
Purpose → Tone → Constraints → Differentiation
   ↓         ↓         ↓              ↓
 解决什么   什么风格   技术限制     什么让人记住
```

**强制避免的「AI Slop」**：
- ❌ Inter / Roboto / Arial 字体
- ❌ 紫色渐变 + 白色背景
- ❌ 千篇一律的卡片布局
- ❌ 无差别的居中对称

**鼓励的差异化方向**：
- 编辑级导航 (editorial-grade)
- 暗色极简 / 极繁主义 / 复古未来 / 有机自然 / …
- 非对称布局、对角线流动、负空间利用

**Warm Studio 设计系统的落地**：
```css
/* 项目自定义的 CSS 变量体系 */
--warm-50  ~ --warm-900   /* 暖灰色阶 */
--primary-200 ~ --primary-600  /* 主色调 */
--success-500              /* 语义色 */
/* data-theme 属性切换 */
```

### 4.3 AgentHub 平台内 Skills vs. 开发用 Skills

重要的是区分两个层面：

| 层面 | 说明 | 位置 |
|---|---|---|
| **开发用 Skills** | Claude Code 在开发 AgentHub 时调用的能力 | `~/.claude/skills/` (frontend-design 等) |
| **平台内 Skills** | AgentHub 提供给其内部 Agent 调用的能力包 | `app/services/tools/skill_tools.py` |
| **平台内 Skill 定义** | AgentHub 用户的 Skill 存储 | `~/.claude/skills/` (anysearch 等) |

---

## 5. Rules 约束体系

### 5.1 `.claude/settings.json` 工具权限白名单

```json
{
  "permissions": {
    "allow": [
      "Bash(npx next *)",           // Next.js 构建/开发命令
      "Bash(npx tsc *)",            // TypeScript 类型检查
      "Bash(python -m py_compile *)", // Python 语法验证
      "Bash(.venv/Scripts/pip.exe install *)", // 依赖安装
      "Bash(python -m uvicorn main:app *)",    // 后端启动
      "Read(//d/Users/xyn/Desktop/agenthub/AgentHubV1.1/**)", // 旧版参照
      ...
    ]
  }
}
```

**设计原则**：
- **最小权限**：只放行开发必需的路径/命令前缀
- **可审计**：每个规则都有明确的通配符匹配范围
- **可扩展**：新增工具需求时追加规则

### 5.2 开发过程中的隐式规则

虽然不是形式化文件，但 AI 在协作中遵守以下隐式规则：

| 规则 | 体现 |
|---|---|
| **先读后改** | 任何 Edit/Write 操作前必须 Read 目标文件 |
| **匹配代码风格** | 新代码保持与周边代码一致的注释密度、命名习惯 |
| **最小改动原则** | 修复 bug 只改必要行数，不重构无关代码 |
| **操作前确认** | 删除文件、清空缓存等破坏性操作前先列出受影响内容 |
| **构建验证** | 每次代码修改后编译检查 + 服务启动验证 |
| **根因追溯** | 报错不只修表象，追踪完整调用链找到根本原因 |
| **Todo 追踪** | 多步骤任务用 TodoWrite 保持进度可见 |

### 5.3 Git 提交规范

```
<type>: <简短描述>

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

- Type 使用中文描述性标题
- 每个 commit 聚焦单一改动范围
- 支持 Co-Authored-By 标记 AI 协作

---

## 6. 工具链协作矩阵

### 6.1 开发阶段 × AI 工具使用

| 开发阶段 | 使用的 AI 能力 | 关键产出 |
|---|---|---|
| **需求理解** | Agent(Explore) + Grep × N | 架构理解、调用链梳理 |
| **方案设计** | EnterPlanMode → Plan 文档 | 可审批的设计方案 |
| **UI 实现** | Skill(frontend-design) | 生产级前端代码 |
| **逻辑实现** | Edit + Write | 精准的代码修改 |
| **故障诊断** | Agent(Explore) + Read + Grep | 根因分析报告 |
| **性能优化** | Read → Edit → Bash(verify) | 性能指标改善 |
| **构建部署** | Bash(npm/npx/uvicorn) | 编译通过 + 服务启动 |
| **文档产出** | Write | Spec 总结文档 |

### 6.2 并发 vs 串行决策

```
并行执行 (无依赖关系):
  · 多个 Grep 搜索不同模式       ← 同时发起
  · 多个 Read 读不同文件         ← 同时发起
  · 探索 Agent + 本地搜索        ← 同时发起
  · 前端构建 + 后端启动          ← 同时发起

串行执行 (有依赖关系):
  · Read → Edit → Verify         ← 必须顺序
  · EnterPlanMode → ExitPlanMode → Implement  ← 审批门
  · 诊断 → 修复 → 验证           ← 因果链
```

---

## 7. 关键协作案例

### 7.1 案例一：Agent 无法对话的端到端诊断

```
背景: 用户反馈「当前Agent无法进行对话」
复杂度: 涉及前端 WebSocket、后端路由、Agent 服务、适配器层、数据库配置
排查深度: 8 层调用栈追踪

协作流程:
  1. [并行] Grep × 6 搜索全项目关键调用链
  2. [并行] Read × 10 精读 15+ 个关键文件
  3. [分析] 在 adapter_manager.py:531-533 定位根因
  4. [验证] 确认 8 个 Agent 的 api_key 均为空
  5. [决策] AskUserQuestion 让用户选择修复方式
  6. [结果] 用户选择通过管理后台配置 API Key

耗时: 单轮对话完成全链路诊断
AI 价值: 人工排查同等深度需要数小时
```

### 7.2 案例二：System Prompt 参数缺失的快速修复

```
背景: 用户配置 API Key 后，后端报错
  "OpenAICompatibleAdapter.execute_prompt() got
   an unexpected keyword argument 'system_prompt'"

协作流程:
  1. [Grep] 搜索 execute_prompt 的定义和所有调用方
  2. [分析] 发现调用方传了 system_prompt= 但方法签名不包含
  3. [检查] 发现 stream_prompt 签名有 system_prompt 但未使用
  4. [修复] Edit × 2: execute_prompt 签名 + stream_prompt payload
  5. [验证] Python 语法检查 + 方法签名验证 + 服务重启

改动量: 1 文件、2 处、各 ≤5 行
修复覆盖: 8 个适配器类 (全部继承自 OpenAICompatibleAdapter)
```

### 7.3 案例三：Admin 侧边栏 UI 重设计

```
背景: "管理控制台的左边栏的UI设计不够好看"
约束: 保持现有功能、不改架构、不引入新依赖

协作流程:
  1. [Skill] /frontend-design 触发设计规范
  2. [Read] 理解现有 layout.tsx + adminStore.ts
  3. [Edit] adminStore.ts: 新增 MenuItemMeta、MENU_GROUPS、MENU_META
  4. [Write] layout.tsx: 完全重写 (暗色侧边栏 + 分组导航 + 活跃指示器)
  5. [Bash] npm run build → 通过

设计系统应用:
  · bg-warm-900 暗色侧边栏 + border-warm-800/50 半透明边框
  · 3px border-l-[3px] 左侧活跃指示器
  · 发光圆点 shadow-[0_0_6px_rgba(123,158,251,0.6)]
  · HSL 渐变用户头像 (基于用户名自动生成色相)
  · 玻璃态 Header (backdrop-blur-sm)
```

### 7.4 案例四：前端性能优化

```
背景: "当前前端页面异常卡顿"

诊断:
  1. react-markdown 被重复打入 2 个 chunk (6.8MB + 6.7MB)
  2. 流式滚动 125 次/秒无节流
  3. 每次渲染 6+ 次 sessions.find()
  4. 全局 store 订阅导致无关 session 更新触发重渲染

修复:
  1. dynamic(() => import(...)) 创建共享 chunk (102KB)
  2. RAF-based 32ms 节流 (~30fps)
  3. useMemo 缓存 currentSession
  4. listenersBySession: Map<string, Set> 按 session 隔离订阅

效果: Admin First Load JS 141kB → 99.1kB (↓30%)
```

---

## 8. 最佳实践总结

### 8.1 AI 与人类开发者的分工边界

| 适合 AI | 适合人类 |
|---|---|
| 大规模代码库探索与搜索 | 产品方向与需求优先级 |
| 多文件一致性修改 | 架构层面的重大决策 |
| 调用链追踪与根因分析 | 验收标准与质量标准 |
| UI 重写（给定设计方向） | API Key、密码等敏感信息 |
| 编译验证与语法检查 | 最终代码审查与合并 |
| 文档生成与维护 | 业务逻辑正确性判断 |

### 8.2 提升 AI 协作效率的关键配置

```
优先级从高到低:
  1. CLAUDE.md / .claude/   ← 项目级别指令 (本项目的空白 = 改进空间)
  2. Rules (.claude/settings.json) ← 工具权限白名单
  3. Memory                  ← 用户偏好、项目约定
  4. Skills                  ← 专项能力注入
  5. Plan 文档               ← 跨会话设计意图
  6. 代码内 Docstrings       ← 内联上下文
```

### 8.3 对本项目的改进建议

| # | 建议 | 说明 |
|---|---|---|
| 1 | 创建 `CLAUDE.md` | 项目根目录放置 Claude Code 的项目级指令文件，包含：常用命令、架构概览、代码风格约定 |
| 2 | 补充 Memory 条目 | 将项目关键决策（如为什么不启用 orchestrator preprocess、MockAdapter 降级策略）写入 memory |
| 3 | 建立 Skill 目录 | 在 `.claude/skills/` 下创建项目级 Skills（如 `deploy`、`test`） |
| 4 | Plan 文档归档 | 将 `~/.claude/plans/` 中的 Plan 文档复制到项目 `docs/plans/` 供团队参考 |
| 5 | 配置 pre-commit hook | 利用 Claude Code 的 hook 机制在提交前自动运行 lint/test |

---

## 9. 附录：关键文件索引

### 9.1 AI 协作产出物

| 类别 | 文件 | 说明 |
|---|---|---|
| Plan | `~/.claude/plans/precious-snacking-pumpkin.md` | AgentEditModal 重构计划 |
| Settings | `.claude/settings.json` | 工具权限白名单 |
| Memory | `~/.claude/projects/.../memory/` | 项目记忆存储 |
| Todo | 会话内 TodoWrite 追踪 | 多步骤任务进度 |
| Transcript | `~/.claude/projects/.../transcripts/` | 完整对话记录 |

### 9.2 项目核心文件速查

| 类别 | 文件 | 说明 |
|---|---|---|
| 入口 | `main.py` | FastAPI 应用 + lifespan |
| 配置 | `app/core/config.py` | 全局 Settings (含 LLM Keys) |
| WebSocket | `app/api/websocket.py` | 实时通信核心 |
| Agent 服务 | `app/services/agent_service.py` | 核心 Agent 调用逻辑 |
| 适配器 | `app/services/adapter_manager.py` | 多厂商 LLM 适配 |
| 前端聊天 | `frontend/pages/index.tsx` | 主聊天页面 |
| 前端管理 | `frontend/app/admin/layout.tsx` | 管理后台布局 |
| 状态管理 | `frontend/stores/adminStore.ts` | 管理后台状态 |
| Session Store | `frontend/lib/sessionStore.ts` | 多 Session 并发状态 |

---
