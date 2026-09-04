# AgentHub 开发者 CLI 核心产品交付方案

> 状态：accepted（执行基线，持续更新）
> 版本：2026-09-04
> 适用范围：`app/cli/`、Mission Control 事件契约、Harness/Runner 集成、CLI 发行与 CI。
> 替换：本文件替换此前以 UI 重构和零散 P0-P5 为主的 CLI 路线图。旧实现记录不再作为后续排期依据。

本文件定义将 AgentHub CLI 建设为程序员日常开发工具的实施顺序、架构约束和验收条件。路线图不是完成证明；任何能力声明必须由版本化契约、自动化测试和必要的真实 CI 运行记录共同支撑。

## 1. 北极星与完成定义

开发者在任意 Git 仓库执行：

```bash
agenthub "修复登录失败并运行相关测试"
```

应获得连续、可恢复、可审计的闭环：

```text
理解目标 -> 制定计划 -> WorkUnit 执行 -> 工具调用 -> SSE 流式反馈
-> 人工确认副作用 -> 独立验证 -> 可审查补丁 -> 恢复或继续执行
```

北极星完成不等于“命令能运行”。一次真实任务必须同时满足：

1. CLI 首先显示 Mission 和工作状态，随后连续显示模型文本与工具输出；断线后不丢失已确认事件。
2. 任意副作用都有可解释的 Decision，用户拒绝后不执行，异常时默认拒绝。
3. 文件修改按 attempt 可审查、可精确恢复，且不会覆盖 attempt 前后的用户修改。
4. Mission 成功必须由独立 Verifier 及 Artifact/Evidence 支持，执行 Agent 不能自证成功。
5. `exec --jsonl` 的输出可被 CI 稳定消费；正常交互、无 Python 的发行包和失败诊断可在干净机器复现。

## 2. 不可改变的架构边界

| 边界 | 唯一职责 | 禁止事项 |
|---|---|---|
| Mission Control | Mission、Contract、WorkUnit、Artifact、Evidence、Decision、Outcome 的持久化真相和事务状态转换 | 不执行模型循环或工具 |
| Harness | 一个 WorkUnit 的有界模型循环、工具调用、预算和 checkpoint 事件 | 不持久化 Mission 真相，不声明成功 |
| Runner | 隔离 attempt、租约、Artifact 收集和到 VERIFYING 的推进 | 不创建 Evidence，不绕过 Decision |
| Verifier | 使用独立执行/规则确认 Outcome | 不复用执行 Agent 的“成功”结论 |
| CLI | 创建/恢复 Mission、消费事件、渲染状态、发起用户命令 | 不维护第二套业务状态或模型循环 |

新能力必须收敛到上述边界。A2A 和 MCP 仅是协议适配器，不能成为新的业务真相。

## 3. 核心产品能力与验收矩阵

| 能力 | 用户结果 | 实现基线 | 生产验收证据 |
|---|---|---|---|
| SSE 流式会话 | 逐 token 文本、工具与验证状态持续可见 | `assistant.delta`、Mission/WorkUnit/tool/decision 事件、游标、去重、轮询降级 | 真实 provider 连续任务、断线恢复记录 |
| 统一渲染状态 | REPL、Rich/TUI、JSONL 对同一事件得出同一状态 | `EventReducer`、版本化 JSONL 外层记录 | 共用 fixture 三种输出一致，兼容版本测试 |
| 文件安全 | `/diff`、`/patch`、`/undo` 不误伤用户工作 | attempt 快照、hash/index 冲突预检、fail-closed | 多 WorkUnit 同文件、未跟踪文件、外部并发修改的恢复测试 |
| 权限与 Decision | 高风险操作能解释、允许、拒绝和回放 | 路径 glob 策略、Decision API、`.agenthub/permissions.json` | 拒绝后无副作用、允许后同 attempt 继续、策略导入导出测试 |
| 多模型可靠性 | 不同供应商在同一 CLI 体验中可用 | provider 归一化 fixture、DeepSeek `v4-flash`/`v4-pro` nightly | `assistant.delta -> tool -> verification` 真实 nightly 记录 |
| 安装与升级 | 开发者不依赖仓库 Python 环境使用 CLI | frozen binary、npm wrapper、`doctor`、稳定退出码 | Windows/macOS/Linux 干净机安装；Windows 升级与回滚成功记录 |
| 可验证交付 | 任务结果可被审查而非只看模型说法 | Artifact/Evidence、独立 Verifier、可审查 patch | mock 与真实仓库任务的证据包和失败路径测试 |
| 体验质量 | 用户知道系统在做什么、为何暂停、结果如何 | Spinner、计时、Token/成本、错误摘要、benchmark | 首 token、工具反馈、恢复成功率、误操作率的版本趋势 |

## 4. 目标事件与数据契约

CLI 的主输入是 SSE。持久化事件使用命名空间事件名，CLI 可映射为展示事件名，但不能猜测状态。

```text
mission.lifecycle.created       -> mission.created
work_unit.lifecycle.leased      -> work_unit.claimed
work_unit.lifecycle.started     -> work_unit.running
harness.assistant.delta         -> assistant.delta
harness.tool.started            -> tool.started
harness.tool.output             -> tool.output
harness.tool.completed          -> tool.completed
work_unit.checkpoint.recorded   -> checkpoint.created
decision.lifecycle.requested    -> decision.pending
artifact.lifecycle.registered   -> artifact.registered
mission.lifecycle.verifying     -> verification.started
work_unit.lifecycle.verified    -> verification.completed
mission.lifecycle.succeeded     -> mission.completed
```

每个 SSE 事件必须包含足够的 Mission/WorkUnit 关联信息、稳定 `eventId` 和适用时的 durable sequence。CLI 必须：

1. 使用 `afterSequence` 恢复 Mission 事件。
2. 使用 `eventId` 去重；只对同一 Mission sequence 空间做有界批内重排。
3. 立即处理 `decision.pending`，不得等待轮询结束。
4. SSE 失败时显示 reconnecting，短轮询只作为降级；恢复 SSE 后继续游标消费。
5. JSONL 使用稳定外层 `schemaVersion`；新增字段只能向后兼容。

## 5. 分阶段实施方案

每个阶段由多个小任务组成。每个小任务必须单独提交并附测试；仅在阶段验收结束后汇报暂停。禁止为了通过 demo 返回合成成功。

### Phase A：流式正确性与真实供应商闭环

**目标**：把 SSE 和文本/工具流从“代码路径存在”提升为可观测、可恢复的真实服务契约。

**小任务**：

1. 为所有目标事件建立版本化 fixture：正常 chunk、空 chunk、tool call 分片、超时、服务端 4xx/5xx、SSE 断线和重连。
2. 将 REPL、Rich/TUI、JSONL 的事件入口全部收敛到 `EventReducer`；移除重复状态推断。
3. 为每个 renderer 建立同 fixture 的快照断言，包括重复、乱序和 reconnect。
4. 扩展 `scripts/cli_provider_smoke.py`：文本流、声明式工具调用、真实验证步骤；真实密钥只由 CI Secret 注入。
5. nightly 固定 DeepSeek `v4-flash`、`v4-pro`；失败日志仅输出 provider、模型、事件计数、HTTP 类别和脱敏错误类型。

**困难与方案**：供应商流格式和工具调用片段不一致。Harness/Model Adapter 负责归一化；fixture 固化边界，真实 nightly 只证明供应商行为，不把原始响应写入仓库。

**完成门槛**：每个 renderer 的契约测试通过；两个 DeepSeek 模型各完成一次 `assistant.delta -> tool -> verification` nightly；无 key 明确 SKIP；断线后不重复副作用。

### Phase B：统一会话状态与交互体验

**目标**：用户在 REPL、Rich/TUI 和 CI 中看到同一任务事实，而不是不同的临时回调视图。

**小任务**：

1. 明确 `SessionViewState` 字段、终态和 reducer 迁移规则；为未知事件保留可观测诊断但不改变业务状态。
2. Rich 只渲染 reducer state：Header（目录、Git branch、模型）、live 状态、工具面板、文本流、结果元数据。
3. TUI 与 REPL 复用 state 投影；JSONL 保持机器可读且 stdout 不混入状态文本。
4. 支持 `/help`、`/status`、`/context`、`/cost`、`/resume`、`/compact`，命令只通过 Mission/Session API 改变真相。
5. 对 token 70/85/95% 阈值、取消、超时、验证失败、SSE 降级提供明确而不误导的显示。

**困难与方案**：终端渲染是异步的，直接在回调中修改 UI 会造成状态漂移。所有回调只产生事件，reducer 是唯一状态折叠点；渲染器是纯投影。

**完成门槛**：同一 fixture 在三种输出中终态、任务 ID、Decision 状态、文件摘要一致；JSONL 可逐行解析；无 TTY 时无阻塞提示。

### Phase C：Attempt 变更安全与多 WorkUnit 恢复

**目标**：把代码修改变成可审查、可恢复的 attempt 资产，而不是泛化 `git restore`。

**小任务**：

1. 在 attempt 开始记录 HEAD、工作区 hash、Git index、预存在未跟踪文件和基线状态。
2. 在 attempt 结束记录聚合后的文件 hash 和 index；同一文件被多个 WorkUnit 修改时只有一个 attempt 级基线和一个最终状态。
3. `/undo` 先做全量工作区/index 冲突预检，再原子恢复；外部变化、缺失快照或无法恢复的路径必须 fail-closed。
4. 对每个 attempt 输出可审查 manifest：变更文件、来源 WorkUnit、Artifact 引用、恢复状态；不记录文件内容到日志。
5. 支持明确的冲突解决 UX：列出冲突路径、建议 `/diff`，禁止自动覆盖或自动 commit。

**困难与方案**：多个 WorkUnit 不可可靠地做逐个逆向 patch，因为最终内容可能互相依赖。恢复单位固定为 attempt 聚合快照；WorkUnit 归属只用于审查，不用于依次反演文件内容。

**完成门槛**：预存在修改、staged 修改、未跟踪文件、新增文件、同文件多 WorkUnit、外部并发修改均有测试；所有冲突路径在任何文件写入前被发现。

### Phase D：权限、Decision 与策略同步

**目标**：让副作用规则可解释、可管理、可迁移，但服务端仍保有最终裁决权。

**小任务**：

1. 统一 Decision 分类：文件写入、shell、网络、Git、包管理和高危命令；事件提供工具、路径、风险摘要和可选命令摘要。
2. 提供一次允许、attempt 允许、会话允许、路径规则允许、拒绝五种 UX，所有选择通过 Decision API 带 `expectedVersion` 提交。
3. 扩展 `/permissions` 为列表、添加、删除、编辑、优先级解释和 dry-run 匹配预览；语法错误不写策略。
4. 使用版本化 `.agenthub/permissions.json` 保存非敏感工具/路径规则；支持 `export`、`import merge`、`import replace` 和 schema 校验。
5. 跨设备同步采用显式文件传递或受认证的用户设置 API；不自动上传工作区策略，不同步 API Key、命令正文、Decision rationale 或历史事件。
6. 后端 `PermissionManager` 必须再次按路径/风险校验，CLI allow 绝不能绕过租约、Contract 能力或服务端 deny。

**困难与方案**：本地策略和组织策略可能冲突。优先级固定为：服务器强制 deny > Contract/能力约束 > 服务端路径策略 > CLI deny > CLI allow > 用户确认。无法判定时拒绝。

**完成门槛**：拒绝后无副作用；允许后只在授权范围生效；导入恶意/损坏文件不改变内存或磁盘策略；策略回放可解释命中来源。

### Phase E：多供应商工具调用矩阵与错误恢复

**目标**：将模型供应商差异限制在 Adapter 内，让 CLI 和 Harness 面对一致的流、工具和错误语义。

**小任务**：

1. 每个 provider 建立文本增量、普通工具调用、分片工具调用、空参数、无效 JSON、拒绝、超时和限流 fixture。
2. Adapter 将工具调用标准化为 name、call ID、arguments、完成状态和 usage；未知形状返回结构化失败，不能猜测执行。
3. Harness 对相同 call ID 幂等执行，对无效参数在下一轮返回结构化反馈。
4. 夜间真实矩阵按 provider/model 记录首 token、工具 call 数、验证结果、断线恢复与失败类别。
5. 新 provider 必须先补 fixture、Adapter 契约和 secret opt-in nightly，后才开放 CLI 配置。

**困难与方案**：真实 provider 不能保证稳定调用工具。nightly 使用短、明确、无副作用的读取工具和独立 mock verification；不将“模型输出文本”当作工具调用成功。

**完成门槛**：受支持 provider 的 fixture 矩阵全绿；每个已声明生产模型有最近有效 nightly；连续失败自动标记为 degraded 并在 `doctor` 显示。

### Phase F：发行、干净机器与升级回滚

**目标**：让 CLI 可安装、可诊断、可升级，并将平台缺口诚实呈现。

**小任务**：

1. 保持 frozen binary、平台 npm 包和 wrapper 的单一版本来源；发布前运行 `verify_cli_release.py` 和两个包的 `npm pack --dry-run --json`。
2. 在 tag 发布后触发干净机器验证：Windows 安装并执行 mock closed-loop；macOS/Linux 在支持二进制前验证稳定 `127` 与“无预编译二进制”诊断。
3. Windows job 安装上一版本、执行 `--help`/mock 任务、升级到目标版本、再次执行、再回滚；失败时保留 npm 和 CLI 诊断。
4. 每新增平台二进制，先将该平台由“诊断验收”升级为“真实 mock closed-loop 验收”。
5. `doctor` 检查 Node、binary、Mission Control 启动、provider 配置和权限目录，不读取或输出密钥。

**困难与方案**：发布前不可能从 npm registry 验证尚未发布的版本。发布流程分为 pre-publish artifact gate 和 post-publish clean-machine gate；后者失败应阻断“稳定版”标记并给出回滚说明。

**完成门槛**：每个发布版本都有 pre-publish 产物证据与 post-publish CI 记录；Windows 升级/回滚真实通过；平台不支持时错误信息稳定且可操作。

### Phase G：独立验证、基准与持续追平

**目标**：以真实开发结果而非界面观感追赶成熟开发 CLI。

**小任务**：

1. 建立受版本控制的匿名 benchmark 任务集：修复、重构、测试失败、权限拒绝、SSE 重连、恢复冲突。
2. 记录首事件延迟、首 token 延迟、工具反馈延迟、总耗时、Token、验证可见率、恢复成功率和误操作率。
3. 每个 release 生成 benchmark 报告；任何显著退化必须有 issue、阈值豁免或回滚。
4. 将真实失败按“模型、工具、权限、服务端、渲染、发行”分类，写入 AI 问题日志并转成 fixture 或回归测试。

**完成门槛**：每次候选发布都有基准对比；验收指标的退化可追踪且不会被平均值掩盖。

## 6. 推荐实施顺序与依赖

```text
Phase A 流式正确性
  -> Phase B 统一状态
  -> Phase C 安全恢复
  -> Phase D 权限与同步
  -> Phase E 多供应商矩阵
  -> Phase F 发行验收
  -> Phase G 基准与持续优化
```

Phase A 是阻断项：没有真实流式和断线语义，UI 优化没有可信基础。Phase C 与 D 可在 Phase B 稳定后并行，但所有写入行为必须经过 Mission Control 的 Decision 与 attempt 边界。Phase E/F 依赖 CI Secret、npm registry 和发布权限，代码合并不等于外部验收完成。

## 7. 当前实现基线与下一动作

| 范围 | 已落地基线 | 下一验收动作 |
|---|---|---|
| SSE/文本/工具流 | SSE cursor/去重/降级、`assistant.delta` 连续输出、Mission Control 生命周期事件规范化、tool lifecycle 事件、batch 重排 | 将真实 `assistant.delta -> tool -> verification` 结果写入 nightly artifact |
| EventReducer | reducer 已接入 runtime、Rich、TUI、JSONL；canonical `state_to_dict/state_summary` 与 renderer snapshot contract 已建立；普通对话使用只读模型路径；未知事件进入 diagnostics；SSE connected/reconnecting/polling 和 FAILED/CANCELLED/TIMEOUT 统一投影；Rich/TUI 使用可录制状态面板 | 增加真实终端录制和更完整的错误态投影 |
| attempt 恢复 | 工作区和 index 快照、冲突预检、新文件删除、同文件多 WorkUnit 聚合测试、内容最小化 manifest、WorkUnit/Artifact provenance | 增加恢复前 UX 预览与更细的事件来源关联 |
| 权限 | 路径 glob、会话持久化、导入导出、规则删除、匹配预览、认证策略同步 API、服务端来源字段 | 增加组织策略优先级的 CLI 可视化 |
| provider | fixture 矩阵、DeepSeek `v4-flash`/`v4-pro` 文本及 tool-call nightly workflow | 配置 CI Secret 并保留真实运行证据，补 verification 链路 |
| npm/发行 | frozen smoke、tarball gate、跨平台安装与 Windows rollback workflow | tag 后执行真实 registry 验收，支持 macOS/Linux binary 后升级为闭环 smoke |
| benchmark | `scripts/cli_benchmark.py` 已输出首事件、首 token、首工具反馈、SSE 重连、Decision 拒绝和恢复成功指标；CLI fixture 测试通过 | 建立版本化任务集和 release 对比阈值 |

下一开发阶段固定为 **CLI 鲁棒性重构**，执行基线见 [CLI 鲁棒性重构与边界说明](../development/cli-robustness-refactor.md)。Phase A/B 的源码 fixture 与 reducer/renderer 一致性测试已完成，当前剩余阻断项是真实 DeepSeek `v4-flash`/`v4-pro` tool-call、真实 TTY 录制、真实网络断线恢复及 npm registry 跨平台安装升级回滚；这些必须由 CI 或干净机器证据确认。

## 8. 质量、安全与文档纪律

1. API Key 仅可通过环境变量或 CI Secret 注入，禁止进入仓库、配置、测试输出和文档。
2. 真实 provider 失败不得被 mock 覆盖；无密钥只能报告 SKIP。
3. 不允许自动 commit、自动覆盖用户修改或在冲突后进行部分恢复。
4. 所有新增事件、权限字段、JSONL 字段和策略 schema 必须版本化并有兼容测试。
5. 每项小任务提交前运行受影响模块、契约和状态转换测试；阶段完成运行对应完整回归。
6. 实现中的问题、根因、验证命令和残余风险记录到 [AI 问题与解决思路日志](../development/ai-problem-solving-log.md)。

## 9. 明确不作为完成声明的事项

- workflow 文件存在，不代表 GitHub Actions 已在真实 secret 或 registry 环境通过。
- 有 npm wrapper，不代表 macOS/Linux 已具有可执行二进制。
- 有 provider fixture，不代表真实供应商在任意时间稳定支持工具调用。
- 有本地权限 allow，不代表能绕过服务端 `PermissionManager`、Contract 或租约限制。
- 有 UI 状态文本，不代表 Mission 已经通过独立验证。
