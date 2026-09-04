# AgentHub CLI 追赶 Claude Code 路线图

> 状态：accepted（实现基线，非完成声明）  
> 版本：2026-09-03

本文是 CLI 后续迭代的唯一公开进度文档。代码、契约和测试优先于本文；“已实现”只表示本地代码路径存在，不表示真实供应商端到端验收完成。

## 北极星

开发者在任意 Git 仓库执行 `agenthub "修复这个问题并运行测试"`，即可获得连续、可恢复、可审计的：

```text
理解目标 -> 计划 -> 修改 -> 工具执行 -> 流式反馈 -> 独立验证 -> 可审查补丁
```

Mission Control 是 Mission、WorkUnit、Artifact、Evidence、Decision、Outcome 的唯一业务真相；CLI 只消费事件和发出用户命令，不运行第二套模型循环。

## 真实现状

### 已验证（代码 + 自动化测试）

- Mission SSE 消费、`afterSequence` 游标、eventId 去重、断线后轮询降级。
- Harness/Model Adapter 的 `assistant.delta` 与 `assistant.completed` 发布路径。
- `tool.started`、有界 `tool.output`、`tool.completed` 事件。
- `decision.pending` 实时确认；失败时 fail-closed；后端版本冲突受保护。
- Rich Spinner、耗时状态、Git diff/changes/patch、确认式 `/undo`。
- `/resume`、`/compact`、`/context` 和 70/85/95% token 提醒。
- `exec --json`、`exec --jsonl`、稳定退出码、`doctor`、bash/zsh/PowerShell completion。
- 独立 Verifier、Artifact/Evidence 与租约隔离仍是完成判定依据。

### 尚未达到生产级

- 尚未在干净机器完成安装、升级、回滚和无 Python 环境验证。
- REPL、TUI、JSONL 尚未完全共享同一 EventReducer。
- 真实多供应商流式、工具调用、断线恢复矩阵尚未纳入 CI。
- 文件变更尚未形成与 Mission attempt 绑定的快照/恢复模型；`/undo` 只恢复已跟踪工作区。
- 权限 UX 仍是粗粒度 `suggest/edit/auto`。
- JSONL schema、兼容策略、GitHub Action 和发布包尚未稳定化。

## 与 Claude Code 的差距排序

1. 安装即用和跨平台发布。
2. 统一会话状态与稳定的流式文本/工具显示。
3. 文件级 patch 审核、撤销和恢复。
4. 细粒度权限确认与危险命令解释。
5. 多供应商真实端到端验证和错误恢复。
6. CI JSONL、插件生态、诊断和升级回滚。

## 可落地迭代计划

### P0：真实性与发布阻断

困难：mock 测试无法证明供应商流式协议；密钥不能进入仓库。  
方案：env-only provider matrix；录制/模拟 SSE fixture 覆盖 chunk、tool call、超时、断线；真实密钥 opt-in 的 nightly smoke，日志脱敏。  
验收：每个 provider 至少完成一次 `assistant.delta -> tool -> verification`；无密钥时明确 SKIP，不伪造成功。

当前进度：已加入 `scripts/cli_provider_smoke.py` 与无密钥流式契约测试；2026-09-03 在受控环境完成 DeepSeek `deepseek-chat` 真实流式 smoke（返回 2 个文本 chunk）。完整 `assistant.delta -> tool -> verification` 矩阵仍需后续 CI 验收。

### P1：统一 EventReducer

困难：回调分散在 runtime、chat、TUI，事件语义会漂移。  
方案：纯函数 reducer 折叠为 `SessionViewState`；Rich、TUI、JSONL 只消费 state/event。  
验收：同一 fixture 三种渲染一致；重复、乱序、重连测试通过。

当前进度：已实现 `app/cli/reducer.py` 纯函数状态折叠，并接入 runtime、JSONL、Rich 和 TUI；EventCursor 负责去重，SSE 批次内 Mission 事件按 durable sequence 有界重排。

### P2：仓库变更安全模型

困难：Git diff 不能表达一次 Mission 修改边界，恢复可能误伤既有改动。  
方案：记录基线 commit/status；每次写入产生 attempt 变更清单；`/undo` 只反向应用本次 attempt 且默认确认。  
验收：既有改动不被覆盖；失败任务仍可审查和恢复；绝不自动 commit。

当前进度：已记录 Mission 启动前 Git HEAD commit/status，并在结果中提供 `missionChangedFiles`、`baselineCommit` 和 `baselineChangedFiles`；`/undo` 优先仅恢复最近一次 Mission 的相对路径变更，拒绝路径穿越。完整 attempt 快照和 index 恢复仍待后续迭代。

### P3：权限与 Decision 产品化

困难：不同工具风险不同，单一模式不足。  
方案：按工具/路径/命令分类，提供一次允许、会话允许、拒绝；所有决策写入 Mission Decision 并 fail-closed。  
验收：危险命令拒绝后不执行；允许后同一 attempt 恢复；策略可回放。

当前进度：CLI “Always allow” 已从全局会话开关收敛为按工具名的会话白名单，并在 `/status` 中可见；后端 PermissionManager 的路径/风险规则仍是最终裁决，逐路径策略 UI 待后续迭代。

### P4：CI 与发行

困难：CLI 依赖本地服务、运行时栈和平台差异。  
方案：固定 JSONL schema 和退出码；GitHub Action；Python/frozen/npm 包；doctor 无密钥诊断；升级可回滚。  
验收：干净 Windows/macOS/Linux 安装并完成 mock 任务；CI 可增量消费并正确失败。

当前进度：已固化 JSONL 的 `schemaVersion: 1` 外层记录，并新增 `.github/workflows/cli-jsonl-smoke.yml`；跨平台打包、真实 provider nightly 和干净机器安装仍未完成。

### P5：体验追平

困难：成熟 CLI 的优势来自大量细节。  
方案：真实开发任务 benchmark，比较首 token 延迟、工具反馈延迟、恢复成功率、误操作率和验证可见性。  
验收：每次版本附 benchmark，未达标项进入下一迭代。

当前进度：新增 `scripts/cli_benchmark.py` 与基准说明，可记录首事件、首 token、总耗时和 Token；真实任务对比数据仍需持续积累。

## 开发纪律

- 每个小阶段独立提交；只在整个 Phase 完成后暂停汇报。
- 新执行行为必须经过 Mission Control、租约、Artifact/Evidence 和独立 Verifier。
- API Key 仅允许环境变量；禁止写入配置、测试、日志和文档。
- “已完成”声明必须附代码路径、测试命令和已知缺口。
- 遇到实现问题，先记录到 [AI 问题解决日志](../development/ai-problem-solving-log.md)。

## 当前阶段

Phase 0-6 的核心代码骨架已落地，但 P0-P5 的生产验收仍未完成。下一顺序固定为：`真实 provider smoke -> EventReducer -> 变更安全模型 -> 权限产品化 -> 发布验证`。
