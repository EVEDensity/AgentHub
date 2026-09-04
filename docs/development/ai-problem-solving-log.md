# AI 开发问题与解决思路

> 状态：implemented（持续维护）

记录 CLI、SSE、Mission Control 和发布工作中的真实问题，避免后续 AI 重复踩坑。禁止写入 API Key、用户数据或完整生产日志。

## 记录格式

日期、症状、根因、解决方案、验证命令、遗留风险。

## 已知问题

### 2026-09-03：持久化事件名与 CLI 规范名不一致

- 症状：EventEnvelope 要求至少三段点号事件名，`assistant.delta` 不能直接持久化。
- 根因：持久化契约与 CLI 展示契约职责不同。
- 解决：后端使用 `harness.assistant.delta`、`harness.tool.output`；CLI 归一化为 `assistant.delta`、`tool.output`。
- 验证：`python -m pytest tests/cli/test_cli_events.py -q`。
- 风险：新增事件必须同步更新后端 Literal、CLI 映射和 fixture。

### 2026-09-03：Decision 请求字段与领域枚举不一致

- 症状：CLI 原先发送 `ALLOW/DENY`，后端接受 `RETRY_WORK_UNIT/FAIL_MISSION`，还要求版本和 rationale。
- 解决：携带 `expectedVersion`，显式映射允许/拒绝；异常时安全拒绝。
- 验证：`python -m pytest tests/cli tests/api/test_missions_api.py -q`。

### 2026-09-03：JSONL stdout 被状态文本污染

- 症状：CI 无法逐行解析执行输出。
- 解决：`--jsonl` 禁用人类状态文本，仅输出 event/result JSON 行；错误仍使用稳定退出码。
- 验证：`python -m pytest tests/cli/test_cli_main.py tests/cli/test_cli_e2e.py -q`。

### 2026-09-04：SSE 批次事件乱序

- 症状：网络批次可能先收到较新的 Mission sequence，导致状态短暂回退。
- 解决：仅在当前批次内对 Mission aggregate 按 durable sequence 排序；WorkUnit/Decision 保持各自到达顺序，避免混用独立序列空间。
- 验证：`python -m pytest tests/cli/test_cli_events.py -q`。
- 风险：跨批次乱序仍依赖服务端 afterSequence 和重连窗口，CLI 不无限缓存事件。

### 2026-09-04：执行基线采集位置错误

- 症状：基线变量被放入列表查询函数，执行结果构造时未定义。
- 解决：基线只在 `execute_objective` 启动 Mission 前采集；列表查询保持无副作用。
- 验证：`python -m pytest tests/cli/test_cli_chat_compact.py tests/cli/test_cli_ui.py -q`。
- 风险：基线仍是路径集合，不是完整 Git index/文件快照；精确恢复属于后续 P2 迭代。

### 2026-09-04：attempt 恢复必须防止外部覆盖

- 症状：直接执行 `git restore` 可能覆盖任务开始前已有改动或任务完成后的外部改动。
- 解决：快照保存基线 hash 与任务前修改文件副本；恢复仅在当前 hash 等于任务后 hash 时执行，冲突立即停止。
- 验证：`python -m pytest tests/cli/test_cli_snapshots.py -q`。
- 进展：恢复前同时比较工作区和 Git index 的当前状态；确认无冲突后恢复基线 index，并删除本次新增文件。
- 风险：多个 WorkUnit 同文件的合并策略待后续实现。

### 2026-09-04：provider 矩阵测试不能依赖未声明插件

- 症状：测试环境没有 `pytest-asyncio` 时，异步矩阵测试无法收集。
- 解决：使用标准库 `asyncio.run` 驱动异步契约，避免隐式测试依赖。
- 验证：`python -m pytest tests/services/test_provider_streaming_matrix.py tests/services/test_provider_tool_call_matrix.py -q`。
- 风险：fixture 只能证明归一化契约，真实供应商行为仍需 nightly secret smoke。

### 2026-09-04：npm 发布前不能只验证 staging 脚本成功

- 症状：平台包和入口包可能在 staging 阶段生成，但缺少对最终 tarball 文件清单的门禁，发布后才暴露遗漏文件或入口配置错误。
- 解决：在 `.github/workflows/npm-cli.yml` 的 publish 前对两个 staging 目录执行 `npm pack --dry-run --json`；该检查复用 npm 自己的打包规则，不上传、不修改 registry。
- 本地验证：`python -m pytest tests/npm/test_npm_cli_package.py tests/cli -q`。
- 边界：Windows 工作区无法替代干净 Linux/macOS/npm frozen 安装；升级、回滚、全局 PATH 和服务启动诊断仍必须由 CI 的真实干净机 job 验收。

## 待记录问题

- 真实供应商流式协议差异与重连行为。
- EventReducer 在 TUI/REPL/JSONL 间的一致性。
- attempt 级 Git 快照与用户既有改动保护。
- frozen/npm 包在干净机器上的服务启动诊断。
- npm 升级/回滚和跨平台安装的真实 CI 结果（本地仅完成 staging 结构与 `npm pack --dry-run` 门禁）。
- CLI 权限规则已增加工作区 `.agenthub/permissions.json` 持久化；仅保存工具名和路径 glob，不保存凭据或决策正文。
- provider nightly 现在分别验证文本流和声明式 tool call；本地无密钥只能验证 `SKIP`，真实结果必须来自 GitHub Secret 运行记录。
- 新增 `.github/workflows/cli-package-install.yml` 覆盖三平台安装；只有 Windows x64 执行真实 `agenthub --help` 和版本切换，其他平台验证稳定的 unsupported-platform 诊断。
- 权限同步 API 使用认证用户 ID 作为作用域，并在响应中保留数据库 `source/priority`；同步只能写入 user-owned 规则，不能覆盖组织全局规则。
- Attempt manifest 只保存 WorkUnit/Artifact 标识、类型、状态、文件列表和摘要 hash，不保存内容；同一文件由多个 WorkUnit 修改时仍以 attempt 聚合恢复。
- 三种 CLI renderer 通过 reducer 的 `state_to_dict` 与 `state_summary` 共享状态快照，避免 JSONL、Rich、TUI 各自推断终态。
