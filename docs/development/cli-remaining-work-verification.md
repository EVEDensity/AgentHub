# CLI 剩余任务验收手册

> 状态：accepted
> 版本：2026-09-05

本文把当前尚未由本地自动化证明的项目拆成可执行验收。代码和 fixture 的通过不等于真实 provider、真实 TTY 或 npm registry 通过。

## 本地自动化

```powershell
python -m pytest tests/cli tests/npm tests/api/test_v1_permissions_api.py -q
python scripts/cli_benchmark.py --task-file benchmarks/cli_tasks.json --task-id conversation-basic --check-thresholds
python benchmarks/gates.py run --name streaming_ttft
python -m app.cli doctor
```

`doctor` 不输出密钥；provider 状态来自 `.agenthub/provider-health.json`（若不存在则显示 declared/no observations）。benchmark 超阈值必须返回 1。

## DeepSeek 真实闭环（人工/CI）

在 GitHub Actions Secret 中设置 `AGENTHUB_CLI_MODEL_API_KEY`，运行 provider nightly workflow 两次，模型分别为 `deepseek-v4-flash` 和 `deepseek-v4-pro`。通过条件：artifact 中仅有脱敏 JSON，事件顺序包含 `assistant.delta`、`tool.started`、`tool.output`、`verification.started`、`verification.completed`、`mission.completed`，无重复 `call_id`。无 key 只能是 `SKIP`，不能伪造 PASS。

## SSE 断线恢复（人工/CI）

使用可注入故障的 Mission Control 代理，在收到至少一个 durable sequence 后主动关闭连接，再恢复服务。日志必须出现 `reconnecting`，重连请求携带上次 `afterSequence`，重复事件不产生第二次工具执行；断线期间的 `decision.pending` 只能产生一次 `resolve` 请求。保存请求日志和 CLI JSONL 作为 artifact。

## 真实 TTY（人工/CI）

在 Windows Terminal、macOS Terminal、Linux PTY 各运行一次交互命令，终端宽度分别设置 40、80、120。录制 ANSI 输出并检查 Spinner 持续刷新、文本不越界、工具/Decision/终态不互相覆盖。PowerShell 可用 `mode con: cols=40`，Unix 使用 `stty cols 40`；无 TTY 的管道测试必须保持非阻塞。

## npm registry（发布后人工/CI）

发布 tag 后，在干净 VM 执行：

```text
npm view @agenthub/cli version
npm install -g @agenthub/cli@<previous>
agenthub doctor
npm install -g @agenthub/cli@<target>
agenthub --version
npm install -g @agenthub/cli@<previous>
agenthub --version
```

Windows 必须完成 mock closed-loop；macOS/Linux 若暂无 native binary 必须稳定退出 127 并明确提示，不得报告成功。保存 npm debug log、`doctor` 输出和版本号。

## 完成判定

本地测试通过即可标记 `implemented`；真实 provider、真实 TTY、真实 registry 等外部证据齐全后，才能升级为 `production-verified`。失败归类写入 `docs/development/ai-problem-solving-log.md`。
