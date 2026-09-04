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

## 待记录问题

- 真实供应商流式协议差异与重连行为。
- EventReducer 在 TUI/REPL/JSONL 间的一致性。
- attempt 级 Git 快照与用户既有改动保护。
- frozen/npm 包在干净机器上的服务启动诊断。
