# 多Agent记忆架构调研与演进路线（Multi-Agent Memory Architecture Research）

> Status: accepted（调研结论与选型已由 [ADR-0108](../architecture/decisions/0108-event-log-as-memory-multi-agent-collaboration.md) 采纳；
> §8 分阶段计划为意图，不是实现证明）
> Owner: architecture maintainers
> Last reviewed: 2026-09-02
> Scope: 多Agent记忆架构调研、Buzz 对标、范式对比、差距分析、选型结论、分阶段演进
> Related: [ADR-0107](../architecture/decisions/0107-memory-slimming-web-chat-decommission.md)、
> [多Agent协作技术方案总图](../architecture/multi-agent-collaboration.md)、
> [北极星路线图](./north-star-developer-cli-experience.md)

本文是 2026-09-01 一次完整开源调研的结构化沉淀，用于长期迭代复用：
后续开发者在升级记忆机制、协作模型或检索能力前，先读本文以避免
重复调研与方向反复。

**事实与判断分离约定**：§1–§4 为调研得到的客观事实（附来源）；
§5–§8 为基于事实的项目演进建议与趋势研判。

***

## 1. 调研背景与方法

- **背景**：AgentHub 记忆子系统刚完成减负（ADR-0107：仅保留 L0 转录 +
  L1 增量会话摘要）；产品愿景对标 Block Buzz——聊天工具形态、会话内
  @Agent 唤醒、人机多 Agent 同房间协作、Agent 承接任务执行。

- **方法**：优先读取 GitHub 仓库 README/架构文档/官方文档，结合
  2025–2026 多Agent记忆行业评测（LoCoMo / LongMemEval / BEAM），
  先完成事实搜集，再做对比与推演。

- **调研对象**：Block Buzz、AutoGen、LangGraph、CrewAI、Mem0、
  Letta(MemGPT)、Zep、Graphiti、OpenHands、AnythingLLM。

## 2. Block Buzz 深度拆解

### 2.1 项目本质与设计哲学

Buzz 是 Block, Inc. 开源的基于 **Nostr 协议（NIP-01 线格式）** 的自托管
团队沟通平台，Apache 2.0，Rust 单体仓库。核心定位：

> "The relay is the workspace … Buzz is the pipe — event store, search
> index, subscriptions, delivery — not the brain."

即：**平台是管道（事件存储/搜索索引/订阅/派发），智能由人类与
Agent 自带**。开场场景即本文方向的样板：凌晨事故频道里问"上次这个
错误发生了什么"，Agent 检索六个月事故历史，带回线程、根因与修复，
并附证据。

### 2.2 架构、身份模型、事件模型

- **一切皆签名事件**：每个动作（消息、reaction、工作流步骤、画板、
  huddle）都是密码学签名的 Nostr 事件，以 `kind` 整数区分；新增功能
  \= 新增 kind，旧客户端不破坏。

- **Relay 是唯一事实源**：客户端经 WebSocket 连接单一 relay；relay
  负责鉴权、验签、持久化、扇出（fan-out）、搜索索引与自动化触发。
  没有 P2P 交换、没有 gossip、没有副本协商。

- **存储**：Postgres（events、channels、tokens、workflows、audit）+
  Redis（presence/typing/跨节点 fan-out）；buzz-search 在 `search_ts`
  上做全文检索。

- **身份模型**：人类与 Agent 是**同等的first-class 成员**；身份可携带。
  **通道成员资格是唯一访问门槛**（开放频道可搜索可自加入；私密频道
  邀请制；访客用 scoped token 限定到特定频道）。

- **租户边界**：community = 工作区；URL 即 community；未知 host
  fail-closed；隔离经 TLA+/Tamarin 形式化验证。

### 2.3 记忆实现机制（重点）

Buzz 的记忆设计可以概括为「**一个事件日志 + 一个搜索索引 + 三种透镜**」：

1. **事件日志即记忆**：不建独立记忆库。六个月的 incident history 之所以
   可检索，是因为所有协作行为本来就以不可变签名事件持久化了。
2. **Receipts（凭据式回答）**：Agent 检索事件流后带回的不是"相似内容"，
   而是**具体线程、根因、修复的引用**——回答自带证据链，可回溯到
   原始事件。
3. **@agent 触发**：Agent 作为频道成员被动收到事件流，@mention 直接
   唤起其执行；Agent 也可主动"监听"频道。
4. **工作流触发**：YAML-as-code 自动化，由事件模式触发，带 trace 与
   审批门（默认仅审批通知）。
5. **七个表面共享一套底座**：Home/Stream/Forum/DM/Agents 目录/
   Workflows/Search——"One event log. One search index. Three lenses."

### 2.4 Buzz 刻意不做的

- 不做认知记忆管线（无 encode/consolidate/recall 认知循环）；

- 不做向量/embedding 记忆层（检索 = Postgres FTS）；

- 不做知识图谱或实体抽取；

- 不做多存储副本（relay 是唯一事实源，无事件复制）；

- 平台不做"大脑"——不替 Agent 决定记什么、忘什么。

> 对 AgentHub 的启示：Buzz 的记忆竞争力来自**结构（事件日志 + 索引 +
> 凭据式检索）而非算法**。AgentHub 已拥有同构结构（Mission/Evidence
> 事件流 + 可回放转录 + 独立验证），差距只在检索视图与触发路由。

## 3. GitHub 高星多Agent项目记忆四大范式对比

| 范式          | 代表项目                               | 实现机制                                                           | 存储                                    | 优点                                      | 缺点                                             | 工程成本                | 评测参考                               |
| ----------- | ---------------------------------- | -------------------------------------------------------------- | ------------------------------------- | --------------------------------------- | ---------------------------------------------- | ------------------- | ---------------------------------- |
| ① 事件日志即记忆   | **Buzz**                           | 不可变签名事件流 + FTS 索引 + receipts 回溯                                | Postgres（事件表）                         | 天然可审计、可回放；零记忆管线维护；回答自带证据                | 无语义泛化（不产生"洞察"）；检索依赖关键词                         | 低（一套事件存储+索引）        | 无（非对话记忆基准目标）                       |
| ② 双栈记忆      | **LangGraph**、AutoGen              | 短期=检查点（thread state），长期=外部 Store 跨线程读写                         | 检查点库 + 可插拔 KV/Postgres/向量             | 边界清晰；短期上下文精确可控；长期记忆可选可换                 | 长期记忆语义需自建；跨会话合并逻辑落在应用层                         | 中（检查点设施已内置，长期层按需）   | LongMemEval 类时序任务依赖自建质量            |
| ③ 抽取/整备管线   | **Mem0**、CrewAI                    | LLM 抽取事实→冲突消解→增删改（ADD/UPDATE/DELETE）→按需注入                      | 向量库 + 图（可选）+ KV                       | 记忆条目化、可解释；token 花在增量上；官方 LoCoMo 报告优于基线  | 管线本身耗 token/调用；抽取错误会固化进记忆；依赖 embedding 服务与额外存储 | 高（服务化组件 + 向量库 + 观测） | Mem0 报 LoCoMo 显著提升（自报口径）；第三方复现有出入  |
| ④ 自编辑/时序知识层 | **Letta(MemGPT)**、**Zep/Graphiti** | Letta：模型自编辑 core memory 块 + 分页；Zep：时序知识图谱，实体带 valid/invalid 时间 | SQLite/Postgres（Letta）；图数据库 + 向量（Zep） | 记忆结构可推理；时序有效性支持"现在 vs 曾经"；Letta 块级自管理灵活 | 架构重、心智负担大；Zep 依赖 Neo4j 级图栈；自编辑可能自我污染           | 高（额外服务/图库/学习成本）     | Zep 报 LongMemEval 时序问题大幅优于基线（自报口径） |

补充事实：

- **OpenHands**：走轻量路线——微代理（microagents）触发 + 会话压缩
  （condenser），与 AgentHub 的 `/compact` 同族。

- **AnythingLLM**：以 RAG 工作区为核心的记忆（文档→向量→检索），
  面向知识问答而非任务记忆。

- **评测基准**：LoCoMo（多会话长程对话回忆）、LongMemEval（时序
  推理与知识更新）、BEAM（大规模行为轨迹检索）是 2025–2026 主流
  口径；注意各项目自报分数与第三方复现存在差异，引用时标注口径。

### 四大范式的本质分歧

范式①②把记忆当作**存储问题**（记什么 = 发生过什么），范式③④把
记忆当作**加工问题**（记什么 = 模型觉得该记什么）。前者可审计、可
回放、零漂移；后者语义灵活但引入管线成本、抽取错误固化与自我污染
风险。AgentHub 的验证权分离文化与前者天然同构。

## 4. AgentHub 现状、需求与产品目标

### 4.1 现状（诚实表）

| 能力                                              | 状态        | 证据                                        |
| ----------------------------------------------- | --------- | ----------------------------------------- |
| Mission/WorkUnit/Artifact/Evidence/Decision 事件流 | ✅         | `app/domain`、`migrations/`、ADR-0001\~0010 |
| 独立 verifier（执行者不自证）                             | ✅         | ADR-0004/0059/0060                        |
| A2A 双向签名身份 + runner 派发                          | ✅ 生产路径    | `a2a_adapter_service.py`、ADR-0037\~0043   |
| @agent mention 解析                               | ✅（Web 面板） | `frontend/…/lib/mention.ts`、UserRoster    |
| 记忆 L0/L1 减负 + 增量摘要                              | ✅         | ADR-0107、`session_memory.py`              |
| CLI 闭环（run/exec/chat/tui/replay）                | ✅         | `app/cli/`、`tests/cli/`                   |
| 跨会话任务检索（receipts）                               | ⚪ 无       | ——                                        |
| Agent 会话成员化 + 触发路由                              | ⚪ 无       | ——                                        |
| Web 聊天 → Mission/v1 API                         | 🔵 迁移中    | ADR-0107 下线 legacy 路径                     |

### 4.2 产品目标（北极星收敛表述）

聊天工具形态的多智能体协作系统：人类与多个 Agent 同会话；@Agent
唤醒即任务承接；执行全程可验证、可回放、可追溯证据。开发者优先
（CLI 一行安装闭环），Web 为同源投影。

## 5. Buzz 能力映射与差距表

| Buzz 能力            | AgentHub 现状                             | 差距                                       | 差距等级      |
| ------------------ | --------------------------------------- | ---------------------------------------- | --------- |
| 不可变事件日志（一切行为）      | Mission 事件流已覆盖任务域；**会话域消息尚未统一入事件流模型**   | 会话事件流建模                                  | 中         |
| 通道成员资格 = 访问门槛      | UserRoster 有雏形；**Agent 尚非正式会话成员**       | 统一成员模型 + Agent 成员化                       | 中         |
| 人类/Agent 同级身份      | A2A 已实现外部 Agent 身份；**内部 Agent 会话身份未建**  | 内部 Agent 能力卡 + 会话身份                      | 中         |
| @agent 触发 → 执行     | mention 解析已有；**缺 mention→Mission 触发路由** | 触发路由                                     | 高         |
| Receipts 检索（带证据回答） | replay 已实现单任务回放；**缺跨会话检索视图**            | Mission/Evidence FTS + `agenthub search` | **高（P0）** |
| 工作流事件触发            | 无                                       | 事件模式→Mission 规则引擎                        | 低（延后）     |
| 全文搜索（Cmd+K）        | 无统一索引                                   | 会话+任务统一索引                                | 低（延后）     |
| FTS 索引（search\_ts） | 无                                       | receipts 视图自带                            | 并入 P0     |
| 形式化租户隔离            | 多租户按环境验证                                | 不阻塞当前阶段                                  | 延后        |

差距结论：**AgentHub 缺的不是"记忆能力"而是"记忆入口"**——事件流、
验证、回放都已就绪，缺的是把既有事件流变成可检索、可触发、可对话的
三块拼图（receipts 视图、成员化、触发路由）。

## 6. 架构选型结论与理由

**结论（ADR-0108）：复用现有 Mission/Evidence 事件流做记忆主干，
不接入 Mem0/Letta/Zep 第三方记忆层。**

理由：

1. **同构结构已存在**：AgentHub 事件流 + Evidence + replay 与 Buzz 的
   事件日志 + receipts + 检索结构同构，补视图即可，无需引外部件。
2. **差异化在证据不在召回**：LoCoMo 式对话回忆不是 AgentHub 的主
   战场；「每个记忆断言可核验到 Artifact 字节」才是。
3. **减负不可回退**（ADR-0107）：引入记忆服务/向量库/图数据库会
   直接违反已接受决策，且违背"本地 SQLite 可跑通"的开箱原则。
4. **维护面**：范式③④要求持续维护抽取质量、图一致性、embedding
   服务；范式①的维护面是"索引别丢"，与本项目人力匹配。
5. **风险**：管线型记忆的抽取错误会固化进长期记忆（错误放大）；
   事件日志型记忆最坏情况是"检索不到"，不会"记错"。

何时重新评估：当出现「开放式跨会话对话回忆」的真实用户用例且
FTS/关键词检索验收不达标时，按 ADR-0107 的 opt-in 条款重新评估
向量路径（仍优先局部、非服务化）。

## 7. 趋势研判（2025–2026）

- **记忆在变轻**：主流工具（OpenHands condenser、各家 /compact）
  收敛到"转录 + 增量摘要"，重型认知管线退潮为服务化组件
  （Mem0/Zep）面向嵌入场景。

- **可审计性在变重**：企业侧对"Agent 说过什么/做过什么"的回放与
  证据要求上升——正对 AgentHub 的 verifier 分离优势。

- **聊天即协调平面**：多Agent 协作入口从编排面板转向会话（@agent
  即任务），Buzz 是该形态最完整的开源样板。

- **评测口径仍分裂**：自报 vs 复现差异大，引用需标注；AgentHub
  应优先自建与自身验收对齐的基准（VERIFY 退出码）。

## 8. 分阶段工作项

> 路线图属意图；每项落地须附测试与验收证据，完成后同步更新
> [技术方案总图](../architecture/multi-agent-collaboration.md) §3/§10 状态。

### 短期（P0，先做）

1. **Receipts 任务检索切片**：Mission/Evidence 上加 FTS/关键词视图 +
   `agenthub search "<query>"`（附 mission 链接与 VERIFY 结果）。
   ✅ 已交付（2026-09-01）：`agenthub search "<关键词>" [--status] [--days] [--json]` + `agenthub replay <mission_id>`（`app/cli/receipts.py::
   search_receipts`，纯读路径零 schema 变更；测试 `tests/cli/
   test_cli_search.py` 17 例）。同时修复 `agenthub missions` 缺
   `workspaceId` 查询参数导致 422 的存量缺陷，以及 v1 API camelCase
   字段兼容。
2. **Web 聊天迁移 Mission/v1 API**：聊天表面脱离 legacy orchestrator，
   记忆走 `build_compact_context`。验收：聊天任务与 CLI 同源同终态。
   ✅ P0 切片（2026-09-01）：`POST /api/v1/chat/mission` 适配器
   （`app/api/v1/chat_mission.py`，create+start 合并返回 SSE
   streamUrl）；`GET /api/v1/workspaces/{scope_id}/members` 统一
   成员目录（`app/api/v1/workspace_members.py`）；前端
   `frontend/lib/workspaceMembers.ts` 替代硬编码 FALLBACK\_AGENTS，
   `frontend/lib/missionEventMapper.ts` 将 Mission 事件映射为聊天气泡
   类型。legacy WebSocket/`websocket_processor.py` 暂保留，后续切片
   逐步替换。

### 中期（P1）

1. **Agent 成员化第一刀**：统一会话成员目录（人类/内部/外部），
   会话内 Agent 可见、可 @。验收：UserRoster 与成员目录一致。
   ✅ P1 切片（2026-09-01）：`GET /api/v1/workspaces/{scope_id}/members`
   返回统一成员视图（当前人类 + 启用的 Agent Catalog 绑定）；
   `frontend/app/page.tsx` 改为从 v1 API 拉取 Agent 列表，替代
   `/api/agent/registry` legacy 端点；`DatabaseAgentBindingResolver
   .list_enabled` 新增批量查询方法。测试 `tests/api/
   test_v1_workspace_members.py` 4 例 + 前端 vitest 3/6 例。
2. **消息→Mission 触发路由**：mention 解析结果自动创建 Mission 并
   回写结果事件。验收：会话内 @dev 完成一次端到端任务闭环。
3. **项目事实落地**：`.agenthub/memory.md` 键级覆盖语义 + 门控注入。
   ✅ 已交付（2026-09-01）：`agenthub facts list|set|get|remove`
   （`app/cli/project_facts.py`，键级覆盖原地生效、无关事实保序）；
   `execute_objective` 注入前按当前目标关键词门控筛选，全库永不
   整体注入（测试 `tests/cli/test_project_facts.py` 12 例）。
4. **MCP 记忆工具**：`recall`/`retain` 只读暴露 L0/L1 与事实。

### P1.5 补充切片（2026-09-02 追加）

1. **会话事件流模型**：SessionEvent domain + migration + SSE 端点 +
   Mission→Session milestone bridge。
   ✅ 已交付：`message.created` / `mention.detected` / `rule.triggered` /
   `mission.created` 四个 write point；`GET /api/v1/sessions/{id}/events/stream`。
2. **规则确认门**：rule engine + YAML + confirm/cancel 端点。
   ✅ 已交付：`RuleEngine` pattern match → `PendingConfirmation` 状态机
   (PENDING→CONFIRMED/CANCELLED/EXPIRED)；`AGENT_RULES.yaml` 热加载。
3. **跨域统一搜索**（原 P3 提前）：CLI `agenthub search --scope both`。
   ✅ 已交付：scope={mission,session,both} + FTS-only per ADR-0108。

### 长期（P2/P3）

1. 订阅/规则触发（先确认后执行）；
2. 事件流统一索引（会话+任务跨域）；
3. 会话事件流与 Agent 目录/工作台表面（Web 投影）。

### T2 Runner 执行加固（2026-09-02 全完成）

- ✅ T2-1: Chat Mission 端到端集成测试 19/19 passed（含 default participant / @mention / session 自动创建 / session_events 写入 / @archivist / rule confirm / cancel）。
- ✅ T2-2: A2A outbound runner-based dispatch 固化（无 env 变量）；gateway 直派已硬删除；runner strict peer manifest。
- ✅ T2-3: Runner claim fencing 10→1 压测通过；lease expiry + heartbeat gap 恢复测试通过；A2A inbound claim fencing 通过。
- ✅ `_inline_derive_work_units` 清理（desktop runner loop 已覆盖 CHAT source）。

### 明确不做（停止条件）

- ❌ Nostr relay / 线协议重构；

- ❌ 引入 Mem0/Letta/Zep/Graphiti 作为业务依赖；

- ❌ 默认启用向量/embedding 检索（opt-in 前置验收标准）；

- ❌ 在 legacy orchestrator 运行时上新建聊天功能；

- ❌ 无 Evidence 的任务完成公告 / 执行者自证（永久红线）；

- ❌ 会话转录复制进第二存储。

## 9. Sources

- Buzz 仓库与文档：

  - <https://github.com/block/buzz> （README：relay 即工作区、七表面、成员模型）

  - <https://github.com/block/buzz/blob/main/ARCHITECTURE.md> （事件模型、fan-out、存储、buzz-search FTS）

  - <https://github.com/block/buzz/blob/main/docs/multi-tenant-relay.md> （租户隔离与形式化验证）

- 范式对比对象：

  - AutoGen：<https://github.com/microsoft/autogen>

  - LangGraph（Memory / 双栈）：<https://langchain-ai.github.io/langgraph/concepts/memory/>

  - CrewAI（Memory）：<https://docs.crewai.com/concepts/memory>

  - Mem0：<https://github.com/mem0ai/mem0> 及 <https://docs.mem0.ai/>

  - Letta / MemGPT：<https://github.com/letta-ai/letta>

  - Zep：<https://github.com/getzep/zep> ；Graphiti：<https://github.com/getzep/graphiti>

  - OpenHands：<https://github.com/All-Hands-AI/OpenHands>

  - AnythingLLM：<https://github.com/Mintplex-Labs/anything-llm>

- 评测基准：

  - LoCoMo：<https://github.com/snap-research/locomo>

  - LongMemEval：<https://github.com/xiaowu0162/LongMemEval>

  - BEAM：<https://github.com/google-deepmind/beam> （大规模行为轨迹检索评测）

- 本项目内部依据：ADR-0104\~0108、北极星路线图、`app/cli/runtime.py`、
  `app/services/memory/`。

