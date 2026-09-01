# 多Agent协作系统技术方案总图（Multi-Agent Collaboration Blueprint）

> Status: draft（目标架构；能力声明按「✅ 已实现 / 🔵 迁移中 / ⚪ 规划」分级，
> 方案文档不等于实现证明）
> Owner: architecture maintainers
> Last reviewed: 2026-09-01
> Scope: 会话协作层、身份与成员模型、@Agent 触发、任务执行链路、
> 记忆检索与证据溯源、前后端联动
> Related: [ADR-0107](./decisions/0107-memory-slimming-web-chat-decommission.md)（记忆减负）、
> [ADR-0108](./decisions/0108-event-log-as-memory-multi-agent-collaboration.md)（事件日志即记忆）、
> [北极星路线图](../roadmaps/north-star-developer-cli-experience.md)、
> [记忆架构调研报告](../roadmaps/multi-agent-memory-architecture.md)

本文是 AgentHub 面向「聊天工具形态多智能体协作」北极星的**单一技术方案
总图**。它规定各模块的边界、数据归属、迭代优先级与禁止项；后续开发者
在此基础上做增量迭代时，应先读本文，再读对应 ADR 与组件文档。

***

## 1. 定位与设计原则

产品终态：人类与多个 Agent 在同一个会话（房间）中协作；用户在对话内
`@agent` 唤醒 Agent 承接任务；Agent 独立执行并回传**带证据**的结果。

六条不可妥协原则（源自代码现状 + Buzz 范式验证，详见调研报告）：

| # | 原则                                                                   | 出处/证据                    |
| - | -------------------------------------------------------------------- | ------------------------ |
| 1 | **事件日志即记忆**：一切协作行为落为不可变事件，检索与回忆建立在事件流之上，不建平行记忆库                      | ADR-0108；Buzz 的 relay 模式 |
| 2 | **管道不是大脑**：平台提供事件存储、索引、订阅、派发；智能由模型与 Agent 提供                         | Buzz 设计哲学                |
| 3 | **验证权分离**：执行 Agent 不能自证完成，Mission 成功只能由独立 verifier 判定                | ADR-0004/0059/0060       |
| 4 | **最小依赖**：本地 SQLite 可跑通全链路；不引入 PostgreSQL/Docker/向量库作为前置              | 北极星 §4.1                 |
| 5 | **CLI 与 Web 同源**：所有表面驱动同一 Mission 状态机，前端只是投影                         | 北极星 §4.1「复用引擎，不造平行实现」    |
| 6 | **记忆减负不可回退**：L0/L1 + 扁平项目事实（ADR-0107）；向量检索默认关闭，仅在明确用例 + 验收标准下 opt-in | ADR-0107                 |

***

## 2. 总体架构图

```text
┌───────────────────────── 接入表面（Projection，不拥有业务真相）─────────────────────────┐
│   CLI / TUI（agenthub run/chat/tui/exec）      Web 聊天协作面板（v1 API）      A2A 对等节点 │
│   ✅ 已实现                                    🔵 迁移中（脱离 legacy 运行时）   ✅ 已实现 │
└───────────────┬────────────────────────────────────┬──────────────────────────┬─────────┘
                │                                    │                          │
                ▼                                    ▼                          ▼
┌───────────────────────── 会话协作层（Collaboration Layer）─────────────────────────────┐
│  统一成员目录（人类 + 内部 Agent + 外部 A2A Agent 同级）                    ⚪/✅ 部分   │
│  会话事件流（session event log）：消息 / @mention / Mission 引用 / 判定摘要  ⚪         │
│  @mention 解析 → 成员与权限校验 → 触发路由（指令/订阅/工作流规则）           ✅ 解析已有  │
│  UserRoster / 成员可见性（Buzz 通道成员模型）                                ✅ 已有雏形  │
└───────────────┬────────────────────────────────────────────────────────────────────────┘
                │ Mission 创建（objective = 消息 + 会话上下文压缩包）
                ▼
┌───────────────────────── Mission Control（唯一事实源，记忆主干）────────────────────────┐
│  不可变事件流：Mission / Contract / WorkUnit / Artifact / Evidence / Decision  ✅        │
│  记忆 = 事件日志：                                                             │
│    L0 会话/Mission 转录（唯一可回放事实源，绝不复制到第二存储）        ✅               │
│    L1 增量摘要（change-only fold，光标后新增轮次并入既有摘要）        ✅               │
│    项目事实（.agenthub/memory.md，键级覆盖语义）                      🔵               │
│  Receipts 检索：Mission/Evidence 上的 FTS/关键词视图 + 证据回溯        ✅ CLI 切片      │
└──────┬──────────────────────────┬──────────────────────────────┬──────────────────────┘
       ▼                          ▼                              ▼
┌───────────────┐      ┌────────────────────┐      ┌──────────────────────────────┐
│ Runner        │      │ Verifier（独立）    │      │ A2A / MCP 适配器（边缘协议）   │
│ 隔离执行/租约/ │      │ VERIFY: 命令门禁、   │      │ 外部 Agent 委派 / 工具与资源  │
│ 心跳/沙箱  ✅  │      │ artifact-set 评估器 │      │  ✅（生产路径）                │
│       │       │      │  ✅                  │      └──────────────────────────────┘
│       ▼       │      └─────────┬────────────┘
│ Harness  ✅   │                │
│ 有界模型循环、 │                ▼
│ 工具调用、预算 │      Evidence（SHA-256 封装）→ Decision（PASS/FAIL，到期监督）✅
└───────┬───────┘                │
        ▼                        ▼
  Artifact（CAS，字节级校验）✅    结果事件回写会话事件流（含 mission_id + 证据摘要）⚪
```

数据流闭环：**消息 → mention → Mission → WorkUnit → Runner/Harness →
Artifact → Verifier → Decision → Evidence → 回写会话流**。会话事件流既是
输入（上下文）也是输出（结果公告），这就是「事件日志即记忆」的完整回路。

***

## 3. 核心能力架构与现状分级

| 模块          | 职责                                       | 状态           | 现有实现锚点                                                            |
| ----------- | ---------------------------------------- | ------------ | ----------------------------------------------------------------- |
| 会话事件流       | 会话内一切行为的不可变日志（L0）                        | ⚪ 设计定稿       | Mission 转录（`app/cli/runtime.py::build_compact_context`）为既有同类形态    |
| 统一成员模型      | 人类/内部/外部 Agent 同级目录与可见性                  | 🔵 雏形        | `UserRoster`、A2A Agent Card 目录投影（ADR-0026/0031）                   |
| @mention 触发 | mention 解析、权限校验、Mission 路由               | 🔵 解析已有，触发待接 | `frontend/…/lib/mention.ts`；缺 Mission 自动创建                        |
| 任务执行链路      | Mission→WorkUnit→Runner→Harness→Artifact | ✅            | `app/domain`、`app/services/runner_worker.py`、`harness_service.py` |
| 独立验证        | Verifier + Decision + Evidence           | ✅            | ADR-0004/0059/0060、`verifier_service/`                            |
| 记忆 L0/L1    | 转录 + 增量摘要                                | ✅            | ADR-0107、`app/services/memory/`                                   |
| 项目事实        | `.agenthub/memory.md` 键级覆盖               | 🔵           | ADR-0107 §存储形态已定契约                                                |
| Receipts 检索 | 跨会话任务检索带证据回溯                             | ✅ CLI 切片      | `agenthub search`/`replay`（`app/cli/main.py`、`app/cli/runtime.py::search_receipts`），测试 `tests/cli/test_cli_search.py` |
| 前后端联动       | Web 面板走 v1/Mission API                   | 🔵 迁移中       | ADR-0107（web chat 下线重做）                                           |

***

## 4. 身份认证体系

设计要点：**人类与 Agent 是同一种东西——会话成员**（Buzz 的第一原则）。

```text
统一成员目录（mission-control 拥有）
├── 人类成员      认证：Web 会话 / CLI 本地凭证（.agenthub/db）
├── 内部 Agent    注册能力卡片：能力清单 + 权限分级 + 模型绑定
└── 外部 Agent    A2A Agent Card + 双向签名信任（ADR-0037~0040）
```

访问控制两层（沿用并扩展现有边界）：

1. **成员可见性 = 唯一准入门槛**（Buzz 通道成员模型）：不在会话成员
   目录里的身份，既收不到事件流，也不能被 @。
2. **工具权限分级**：`suggest / edit / auto`（已落地的 Codex 式分级），
   控制成员在会话内的行为面；Verifier 与 Runner 的授权继续走
   workspace ACL（ADR-0054\~0056），不因聊天形态放宽。

边界约束：身份与成员目录归 Mission Control；前端 Roster 只是投影；
A2A 信任不越过系统边界改变内部权限（ADR-0022）。

***

## 5. 会话协作模型

一个会话 = 一条不可变事件流 + 一个成员集合。事件类型（沿用
Mission 事件流的 append-only + 事务投影模式，ADR-0008）：

| 事件                     | 产生者         | 说明                              |
| ---------------------- | ----------- | ------------------------------- |
| `member.joined / left` | 系统          | 成员变更，驱动可见性                      |
| `message.created`      | 任何成员        | 人类消息 / Agent 回复，L0 转录           |
| `mention.detected`     | 系统          | @agent 解析结果（目标、能力绑定）            |
| `mission.created`      | 触发路由        | 含 mission\_id，回链执行域             |
| `mission.completed`    | 系统          | 含终态 + Evidence 摘要 + Artifact 引用 |
| `decision.recorded`    | Verifier 通道 | PASS/FAIL 判定公告                  |

取舍说明：

- **不引入 Buzz 的 Nostr 线协议**——事件流语义复用，传输层复用现有
  HTTP/WebSocket；Buzz 的价值在其记忆模式，不在其协议（ADR-0108 备选
  分析）。

- **会话流不持久化到第二存储**：会话事件直接以 Mission 转录 + 会话
  事件表落库，L1 摘要只存摘要文件（ADR-0107）。

- **会话 ≠ Mission**：一个会话可派生多个 Mission；一个 Mission 的
  执行事件不灌回会话流（只在里程碑处回写摘要事件），避免刷屏与
  token 膨胀。

***

## 6. Agent 唤醒触发机制

```text
message.created 事件
   │
   ▼
① mention 解析（复用现有 detectMention 语义：frontend/…/lib/mention.ts）
   │  解析出 @target + 消息意图
   ▼
② 成员与权限校验（失败 → 系统提示事件，绝不静默丢弃）
   │  · target 在会话成员目录中？
   │  · 发起者有权唤醒该类 Agent？（suggest/edit/auto 分级）
   ▼
③ 触发类型路由
   ├── 指令触发："@dev 修复登录 bug 并跑测试" → 立即创建 Mission
   │             objective = 消息 + L1 会话摘要（build_compact_context 产物）
   ├── 订阅触发：Agent 订阅会话关键词/事件模式（如 "error"、"部署失败"）
   │             命中 → Agent 主动回复（先问，不自动开任务）
   └── 规则触发：工作流规则匹配事件模式 → 自动 Mission（YAML 声明，带审批门）
   │
   ▼
④ mission.created 事件回写会话流（含 mission_id）；Agent 可在会话内
   持续追加说明（作为 Mission 上下文注入）
```

设计取舍：

- **触发即 Mission，不建第二执行运行时**——聊天只是 Mission 的又一
  个入口表面（北极星原则 2）。

- **订阅/规则触发默认「先确认后执行」**，只有显式指令触发可直达
  auto 分级；这是对「Agent 自主扩大行动面」的结构性防御。

- 每个 Mission 的上下文注入遵循**门控注入**（ADR-0107）：只注入
  匹配当前 objective 的摘要与事实，绝不整库注入。

***

## 7. 任务执行链路

完全复用既有链路，聊天形态不新增任何执行语义：

```text
Mission 创建（objective 携带会话上下文压缩包）
  → Contract（验收标准：VERIFY: 命令 / artifact-set 评估器）
  → WorkUnit 派生 → Runner claim/lease/heartbeat（ADR-0002/0012）
  → Harness 有界循环（预算/检查点/工具：文件、执行、lint、web、搜索）
     └─ 可经 A2A/MCP 委派外部 Agent 或工具（ADR-0034/0036）
  → Artifact 注册（CAS + 字节级校验，ADR-0007/0009/0010）
  → Verifier 独立验证 → Decision（PASS/FAIL，fail-closed，ADR-0056/0063）
  → Evidence 封装（SHA-256 envelope，ADR-0058）
  → mission.completed 回写会话流：终态 + 证据摘要 + Artifact 引用
```

验收即证据：会话内 Agent 的「完成」公告必须携带 verdict 与
artifact 引用；没有 Evidence 的完成公告不允许出现（延续
「不返回 demo/synthetic 成功」红线）。

***

## 8. 记忆检索与证据溯源（Receipts）

这是本方案新增的核心能力，也是对 Buzz receipts 模式的落地：

```text
检索入口：
  CLI   agenthub search "上个月修过什么" --days 30   ✅ 已实现（P0 切片）
  CLI   agenthub replay <mission_id>                  ✅ 已实现（目标/证据/产物回放）
  会话  @archivist 查一下上次登录重构的结论           ⚪ 规划（同一检索服务）
        │
        ▼
Receipts 视图（Mission/Evidence 上的关键词视图 + 时间/状态过滤；
当前实现：app/cli/runtime.py::search_receipts，纯读路径，零 schema 变更）
        │  命中记录：objective、终态、verdict、artifact 引用、时间
        ▼
带证据回答：每条结论附 mission 链接 + VERIFY 结果 + Artifact 摘要
        │
        ▼
回放：agenthub replay <mission_id> 展示目标/终态/证据/产物；
chat 内 /replay 沿执行检查点复现全过程
```

记忆栈全景（与 [memory.md](./components/memory.md) 对齐）：

| 层        | 内容                         | 检索方式                   | 状态   |
| -------- | -------------------------- | ---------------------- | ---- |
| L0       | 会话/Mission 转录              | 顺序回放 / replay          | ✅    |
| L1       | 增量会话摘要                     | 门控注入 prompt            | ✅    |
| 事实       | `.agenthub/memory.md` 项目事实 | 键级覆盖，关键词注入             | 🔵   |
| Receipts | Mission/Evidence 跨会话检索 | FTS/关键词视图 | ✅ CLI 切片 |
| 向量       | embedding 检索               | **默认关闭**，opt-in + 验收标准 | 禁止默认 |

溯源链：**结论 → mission\_id → WorkUnit 尝试 → Artifact（digest）→
Evidence（verdict）**。任何记忆中的断言都可沿此链核验到字节级产物，
这是 AgentHub 区别于「相似度召回」型记忆方案的根本差异。

***

## 9. 前后端联动逻辑

分层契约：

```text
前端（Next.js，纯投影）
  ├── 会话视图：事件流渲染（消息/mention/mission 卡片/verdict 徽标）
  ├── 成员视图：Roster（人类+Agent 目录、能力、权限分级）
  └── 检索视图：receipts 结果列表（mission 链接 + 证据徽标）
        │ 全部经 v1/Mission API（迁移中）
        ▼
Mission Control（唯一事实源）
  ├── v1 API：CRUD + 事件流读 + mention 触发写
  ├── 实时通道：会话事件订阅（现有 WebSocket 设施收敛）
  └── CLI/TUI/Web 三表面同源（同一 mission_id，同一状态机）
```

联动规则：

1. 前端不拥有任何业务状态，不做兜底成功（system-boundaries 既有
   边界，聊天形态不放宽）。
2. Web 聊天重做必须落在 Mission + v1 API + `build_compact_context`
   记忆路径（ADR-0107 后果条款）；legacy orchestrator 运行时上禁止
   新增功能。
3. CLI 先行、Web 跟随：任何会话能力先在 CLI 验证闭环（含退出码
   契约），再投影到 Web。

***

## 10. 迭代优先级

按「先证据检索、再成员化、再自动触发」排序（论证与验收标准详见
[调研报告 §8](../roadmaps/multi-agent-memory-architecture.md)）：

| 优先级 | 工作项                                                       | 依赖          | 一句话验收                                   |
| --- | --------------------------------------------------------- | ----------- | --------------------------------------- |
| P0 | Receipts 检索切片：Mission/Evidence FTS 视图 + `agenthub search` | 无（纯读路径） | ✅ 已交付（2026-09-01）：`agenthub search "<关键词>" [--status] [--days] [--json]` 返回带 mission 链接、verifier verdict 与 evidence 摘要的条目；配套 `agenthub replay <mission_id>`；修复 `missions` 列表缺 `workspaceId` 参数导致 422 的存量缺陷 |
| P0  | Web 聊天迁移 Mission/v1 API                                   | 既有 v1 API   | 聊天表面脱离 orchestrator，与 CLI 同源            |
| P1  | Agent 成员化：会话成员目录 + @任意成员                                  | 统一成员模型      | 会话内可见 Agent 目录并可 @ 触发                   |
| P1  | 消息→Mission 触发路由                                           | 成员化         | 会话内 @dev 指令创建 Mission 并回写结果事件           |
| P2  | 订阅/规则触发（先确认后执行）                                           | 触发路由        | 关键词命中后 Agent 先提问确认                      |
| P2  | MCP 记忆工具：`recall`/`retain` 暴露 L0/L1                       | receipts    | 外部 Agent 经 MCP 读写项目事实                   |
| P3  | 事件流统一索引（会话+任务跨域检索）                                        | receipts 稳定 | 单一检索覆盖聊天与任务                             |

## 11. 技术取舍与禁止项

**明确取舍**：

- 复用 Mission 事件流 vs 引入 Mem0/Letta/Zep → **复用**（ADR-0108）。

- 借鉴 Buzz 模式 vs 复刻 Nostr relay → **只借模式**（事件日志+索引+
  receipts），不引入其线协议与 relay 部署形态。

- FTS/关键词 vs 向量检索 → **先 FTS**：任务记忆的核心查询是
  「何时/何任务/何证据」的结构化回溯，关键词 + 时间过滤即可覆盖；
  向量仅在 LoCoMo 类开放式对话回忆成为真实用例后 opt-in。

- 会话流隔离 vs 执行事件直灌会话 → **里程碑摘要回写**，控制 token
  与噪音。

**禁止项（红线）**：

1. 不引入第三方重型记忆组件（Mem0/Letta/Zep/Graphiti）作为业务依赖。
2. 不做 Nostr relay 完整重构。
3. 不默认启用向量/embedding 检索路径。
4. 不在 legacy orchestrator 运行时上新建任何聊天功能。
5. 不允许没有 Evidence 的任务完成公告；不允许执行 Agent 自证。
6. 会话转录不得复制进第二存储（保持单一可回放事实源）。
7. 订阅/规则触发不得默认越过确认直接执行 auto 级行为。

## 12. 维护约定

- 本文档随每个 P0/P1 工作项落地更新状态分级（⚪→🔵→✅）并补实现
  链接；能力声明无链接即降级为规划表述（与北极星 §6 一致）。

- 涉及边界变更（成员模型、触发路由、检索契约）必须先出 ADR。

- 与 [memory.md](./components/memory.md) 的职责划分：本文管协作总图，
  memory.md 管记忆实现基线；两者冲突时以 ADR 为准。

