# AgentHub North Star：开发者优先的 CLI 体验

> Status: draft
> Owner: architecture maintainers
> Last reviewed: 2026-08-31
> Scope: developer-facing surface (CLI → TUI), packaging, verification, release
> Related: [reconstruction-roadmap.md](./reconstruction-roadmap.md)（代码债排序）、
> ADR-0004 / ADR-0059 / ADR-0060（验证权分离）、ADR-0053（A2A outbound cutover）
>
> 本文档是 AgentHub 面向开发者的**北极星目标**。它不是实现清单，而是
> "下一阶段往哪走"的单一来源。每当达成或新增里程碑，在此追加记录；
> 任何与开发者触达相关的重大变更，都应先更新本文件。

## 0. 一句话北极星

> 开发者在本机执行 `npm i -g @agenthub/cli` 之后，运行
> `agenthub "修复这个 bug，并跑测试证明"`，agent 就能在终端里自主完成
> 「计划 → 改码 → 沙箱执行 → VERIFY 门禁证明完成 → 产出可审查 diff」的
> 闭环，并可通过 `agenthub exec --json` 接入 CI/PR——**全程不需要安装
> PostgreSQL / Docker / Node / Python，也不需要打开 Web UI。**

## 1. 为什么（Why）—— 三个已由代码验证的事实

1. **核心引擎已经具备**：有界模型循环与预算
   （`app/services/harness_service.py`）、Runner claim/lease/heartbeat
   （`app/services/runner_worker.py`）、Windows restricted-token + Job Object
   沙箱（`app/services/runner/sandbox_windows.py`）、桌面本地工具集
   `code_execute` / `command_execute` / `lint_check` / `delegate_subtask`
   （`app/services/desktop_runner_tools.py`）、`VERIFY:`/`RUN:` 验收命令门禁
   （`app/services/runner/loops.py`）。
2. **验证权分离已领先竞品**：Mission 成功只能由独立 verifier 判定
   （ADR-0004 / ADR-0059 / ADR-0060），benchmark 已用 `VERIFY:` 命令把
   acceptance 转成退出码（`docs/benchmark/README.md`）——正面回答了
   "agent 自己说自己完成"这一行业通病。
3. **但也已经具备"把外部 agent 当后端"的能力**：本地发现 `claude` / `codex`
   （`app/services/local_agent_discovery.py`），Codex CLI one-shot、
   Claude Code 交互式反馈分别建模（`app/services/agent/tooling.py`），
   可作为 MCP STDIO 工具接入（`docs/zh/guide/mcp-integration.md`）。

**上市缺口**：开发者第一触点缺失——无 CLI/TUI、桌面发布被 5 个签名密钥
阻塞、`start.bat` 只是开发启动器。能力都在，但没人能"先跑起来"。

## 2. 现状评估（2026-08-31，诚实表）

| 项                                                   | 状态                                                       | 证据                                                                                                       |
| --------------------------------------------------- | -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Mission / WorkUnit / Artifact / Evidence / Decision | ✅ 已落地                                                    | `app/domain`、`app/repositories/mission_repository.py`、`migrations/`                                      |
| A2A 出站 + 入站                                         | ✅ 已落地（生产路径）                                              | `a2a_adapter_service.py`、inbound mapping 迁移、`A2A_DISPATCH_MODE=runner` cutover + 单测                      |
| 独立 verifier（执行者不能自证）                                | ✅ 已落地                                                    | ADR-0004/0059/0060、`verifier_service/`                                                                   |
| 桌面单 exe 开箱即用                                        | ❌ 未兑现                                                    | 打包流水线绿，但签名发布依赖 5 个 secrets；无公开 Release 产物                                                                |
| CLI / TUI 入口                                        | ✅ CLI 已实现（M0）；TUI 未开始                                    | `app/cli/`（`python -m app.cli`）、`tests/cli/`                                                             |
| headless exec / PR 审查 Action                        | ⚠️ exec 已实现并本地验证；CI workflow 未接线                         | `app/cli` exec --json + 退出码契约；GitHub Actions 接入属 M1                                                      |
| 公开 agent 能力基准（Terminal-Bench 等）                     | ❌ 未接入                                                    | benchmarks 仅覆盖 P95/召回/tokenizer 精度                                                                       |
| 分层项目指令 / skills 生态                                  | ✅ 已实现（M1：分层 AGENTS.md 合并注入 + 只读 skill\_list/skill\_load） | `app/cli/runtime.py`、`app/services/desktop_runner_tools.py`、`tests/services/test_desktop_skill_tools.py` |
| Web 搜索 / 浏览器工具                                      | ⚠️ web\_search 已实现（M1，Tavily/DDG）；浏览器操作仍缺失               | `app/services/tools/network_tools.py`、`tests/services/test_web_search_tool.py`                           |

> 注：`docs/index.md` 仍把 A2A 标注为"原型"，属文档滞后，与代码不一致，
> 应为"已实现"。

## 3. 核心缺陷（开发者视角，多维评估摘要）

对比基准（2026 年热门 agent：OpenCode、Claude Code、Codex CLI、Gemini CLI、
Aider、Cline、Goose）：

| 维度          | AgentHub 现状          | 热门 agent                    | 对开发者的影响       |
| ----------- | -------------------- | --------------------------- | ------------- |
| 第一触点        | 仅 Web UI + 未发布的桌面包   | 终端优先、一行安装                   | 试用/获客门槛极高     |
| 快速上手        | 需 Python/Docker/环境变量 | 开箱即用，有免费层                   | 验证成本趋零 vs 手术级 |
| headless/CI | 内部门禁，无对外 PR Action   | `exec`/`-p` + GitHub Action | 无法嵌入日常 git 流程 |
| 项目指令生态      | 单一 AGENTS.md         | 分层 AGENTS.md + skills       | 上下文复用与一致性缺失   |
| 公开基准分数      | 无                    | Terminal-Bench 83.4%(Codex) | 无法量化证明"能干活"   |
| 记忆架构        | L0-L2 较重，L3 未落地      | SQLite 轻量记忆                 | 人力成本 vs 完成度   |
| 工具面         | 文件/执行/lint 有界        | +web 搜索/浏览器操作               | 调研类任务不可用      |

**被诟病的行业通病与本项目态势**：

1. "agent 说自己完成就完成" → 本项目已用独立 verifier 解决 ✅
2. "无效迭代循环 + 上下文爆掉 + token 失控" → 已有 bound/budget，
   缺交互式 compact/回放 ⚠️
3. "权限弹窗打断 + 执行不安全" → 已有沙箱+审批，
   缺 Codex 式（suggest/edit/auto）细粒度策略分级 ⚠️

## 4. 北极星原则（不可妥协）

### 4.0 产品形态基线：desktop exe 是引导下载器，不是完整运行时（2026-08-31 确立）

与 Codex 等市面 agent 工具一致的发布形态：

1. **`AgentHub.exe`** **是一个轻量引导器（bootstrap installer/launcher）**，
   本身不携带完整运行时。首次点击后，引导器把完整运行时栈**下载/落盘
   到用户数据目录**（Windows：`%LOCALAPPDATA%\AgentHub`），随后每次启动
   由引导器拉起该目录中的栈。

2. **数据目录是系统的家**，布局（部分已由
   `desktop/src-tauri/src/services.rs` 实现）：

   ```
   <数据目录>/
     stacks/<version>-<commit>/local-services/   # 版本化运行时栈（可多版本共存）
       stack-manifest.json                        # 栈清单（schemaVersion/version/commit）
     stacks/.pinned                               # 当前钉住的栈版本
     data/                                        # 业务数据（SQLite、artifacts CAS、日志）
     workspaces/                                  # 用户代码工作区
   ```

3. **栈版本化 + 钉住 + 回滚**：新版本下载为新 `stacks/<version>/` 目录，
   验证就绪后才切换 `.pinned`；旧版本保留，可回退。

4. **CLI 与桌面同构**：CLI 的 `.agenthub/` 本地状态目录（db/data/logs）
   即同一形态的最小实现；两者都不把状态写进安装位置。

> 现状核对：`services.rs` 已有 `StackInfo`（manifest/source/persisted/pinned）、
> `stacks/<version>/` 版本化缓存、bundled→persisted 拷贝降级路径；
> **尚未实现**的是"从远端下载栈"这一引导环节（当前 bundled 栈来自安装包
> 内资源），以及下载进度/校验/断点续传。归入 M3（生态与发布）。

### 4.1 原则

1. **CLI-first**：所有让开发者上手更费力的表面，优先级都低于"至少存在一个
   好用的终端入口"。TUI 是 CLI 的后续增强，不阻塞 CLI。
2. **复用引擎，不造平行实现**：CLI/TUI 直接复用现有
   `harness_service` + `desktop_runner_tools` + `runner/loops` + Mission 状态，
   不新建第二套执行循环。
3. **验证权分离不可妥协**：执行者不能自证完成，`VERIFY:`/verifier 门禁在
   CLI 路径同样生效。
4. **开箱即用**：`agenthub` 到手即可用，不要求开发者安装
   PG / Docker / Node / Python；本地首次运行默认 SQLite + 内置 mock，真实
   agent 只需一个 API key 环境变量。
5. **可回放、可审计**：每个动作可回放（复用 `execution_checkpoints`），
   每次完成必须有 Evidence，杜绝 demo 假成功。
6. **安装位置 ≠ 数据位置**：任何形态（exe 引导器/CLI）都不把可变状态写进
   程序安装目录；一切状态归数据目录（见 4.0）。

## 5. 里程碑（每个 M 必须产出可运行产物，不空转）

### M0 — CLI 最小闭环（核心已交付 2026-08-31，CI 接线余项）

交付：

* `agenthub init`：初始化本地项目目录（sqlite + 工作区 + 配置）。

* `agenthub run "<objective>" [--model ...]`：一次对话完成一个任务，
  全程驱动现有 desktop runner 工具集与 harness。

* `agenthub exec --json`：无头模式，`--json` 输出结构化结果，退出码
  与 Mission 终态一致（供 CI 使用）。

* 输出：计划 → 变更文件 diff（复用 changed-files 逻辑）→ VERIFY 结果 →
  Evidence/Artifact 摘要。

* 默认环境：PostgreSQL 可选；本地 `SQLite` profile 一跑就跑。
  验收标准：

* `agenthub run "修复 XXX 并 VERIFY"` 在全新目录可完成闭环，
  且**不要求预装 Docker/PG**。

* `agenthub exec --json` 在 CI（GitHub Actions）可跑通并正确返回退出码。

* Mission 的 verifier 门禁在 CLI 生效：执行代理无法伪造 PASS。

> **状态（2026-08-31）**：核心三条已实现并本地验证。入口为
> `python -m app.cli`（`app/cli/`，见 [app/README.md](../../app/README.md)）。
> 证据：
>
> * 无 key 开箱（mock 回退）与 `init/run/exec` 三个子命令：
>   `tests/cli/test_cli_main.py`（单元）；
>
> * 端到端闭环 + **诚实失败**（mock 注册 artifact 字节验证通过，但
>   `VERIFY:` 命令一票否决 → FAILED → 退出码 1）：
>   `tests/cli/test_cli_e2e.py`（`AGENTHUB_CLI_E2E=1` 门控）；
>
> * 修复了 `_PROJECT_ROOT` 越出仓库根导致 CLI 触碰工作区外路径的缺陷
>   （`tests/core/test_project_root.py`）。
>   余项：GitHub Actions workflow 中跑 `exec --json` 冒烟（并入 M1），
>   以及 `npm i -g` 打包分发（按计划属于 M3）。
>   用法注意：`VERIFY: <命令>` 需独占一行，才会被提取为验收命令。

### M1 — CLI 完善 + 能力补全

* 分层项目指令：读取 `AGENTS.md`（根 → 项目 → 子目录合并）。
  ✅ 已交付（2026-08-31）：CLI 合并分层 `AGENTS.md`（workspace 根 → cwd，
  浅层优先），经 `AGENTHUB_DESKTOP_PROJECT_INSTRUCTIONS_FILE` 注入
  desktop 系统提示（20k 字符截断并显式标注）。见
  `app/cli/runtime.py`（`collect_agents_md_layers`/`merge_project_instructions`）、
  `app/services/runner/model.py`（`compose_desktop_system_prompt`），
  测试 `tests/cli/test_cli_main.py::AgentsMdLayerTests`。

* skills 加载：复用 `plugins/` 机制做可分发 skill 包。
  ✅ 已交付（2026-08-31）：desktop 白名单新增只读 `skill_list`/`skill_load`
  工具，复用 `skill_tools._parse_skill_md` 解析 `<workspace>/.claude/skills/`
  下的 SKILL.md（frontmatter+正文+脚本清单）；技能脚本执行仍不进白名单
  （代理可读指引、不可执行任意脚本）。测试
  `tests/services/test_desktop_skill_tools.py`。可分发打包归 M3。

* Web 搜索工具补入工具集，补齐调研类任务。
  ✅ 已交付（2026-08-31）：`web_search` 工具 —— 设置
  `AGENTHUB_TAVILY_API_KEY` 走 Tavily，否则零 key 走 DuckDuckGo HTML
  （诚实失败，无合成结果）；结果含标题/URL/摘要并截断；桌面档经
  `AGENTHUB_DESKTOP_WEB_SEARCH` 门控（默认关，CLI 默认开、
  `--no-web-search` 可关）。测试 `tests/services/test_web_search_tool.py`。

* 会话持久化与 `--resume`。
  ✅ 已交付（2026-08-31）：本地状态持久化（`.agenthub/db` + `data` 跨
  运行复用）；`agenthub missions` 列出历史任务；`run/exec --resume <mission_id>` 把先前任务的目标/终态/沉淀摘要作为只读上下文
  前置（绝不虚构历史）。测试见 `tests/cli/test_cli_main.py`。

* CI 接线（自 M0 余项并入）：`.github/workflows/ci.yml` 新增
  `Developer CLI` job —— 单测 + e2e + 无头 `exec --json` 冒烟
  （退出码必须为 1，验证 verifier 否决路径，杜绝假成功）。
  验收标准：M0 的 3 条验收继续成立，且新增能力有对应单测。

### M2 — TUI（基线与全屏均已交付 2026-08-31）

* ✅ **交互式 REPL 基线**：`agenthub chat`（
  `app/cli/chat.py`）——斜杠命令（/help /missions /resume /unresume
  /new /status /quit）、每轮实时 `[status]` 流、多轮自动链式 resume
  （上一任务的 objective/终态/摘要作为下一轮只读上下文）、基础设施
  错误不终止会话。零新依赖，直接复用 `execute_objective` 引擎。
  测试 `tests/cli/test_cli_chat.py`（脚本化输入/输出注入）。

* ✅ **全屏 TUI（Textual）**：`agenthub tui`（`app/cli/tui.py`）——
  Header（模型/运行态）+ RichLog 流式转录 + 输入行 + 状态栏布局；
  任务在 thread worker 中执行（UI 保持响应），状态与结果经
  `call_from_thread` 回传；运行中拒绝并发提交；多轮自动链式与
  REPL 一致。框架选型记录：**Textual 8.x**（纯 Python、Windows
  原生、`run_test()` pilot 支持无头测试——ratatui 需独立 Rust
  产物、ink 需 Node 运行时，均与"复用引擎零平行实现"冲突）。
  依赖已入 `requirements.txt`。测试 `tests/cli/test_cli_tui.py`
  （pilot 无头驱动真实 App）。

* 全屏 diff 分屏查看/编辑、会话列表视图：（未开始，在 Textual
  App 之上增量迭代）

  验收标准：TUI 能完成 M1 的全部交互，且状态与 WEB/CLI 同源（同一 Mission）。

### M3 — 生态与发布

* **桌面引导下载器（4.0 产品形态基线的落地）**：`AgentHub.exe` 轻量引导器
  首次运行从发布源下载完整运行时栈至 `%LOCALAPPDATA%\AgentHub\stacks\`
  （含下载进度、栈清单校验 sha256、失败重试/断点续传），验证
  `readyz` 后切换 `.pinned`；旧版本保留可回滚。现有
  `services.rs` 的 stacks/pinned/manifest 机制即其宿主。
  验收标准：全新机器上仅凭引导器 exe 完成首次下载→启动→跑通一个
  Mission；断网中断后重试可续；`.pinned` 回滚可用。

* `agenthub` 打包：`npm i -g` 分发（二进制内置，零运行时依赖）。

* PR 审查 Action：`agenthub review-pr` / GitHub Action，接入现有 verifier。

* 公开基准分数：把 benchmark cases 对接到 Terminal-Bench 类评测，给出可引用
  分数（诚实标注模型与日期）。

* 桌面签名发布解阻塞：将 5 个签名 secrets 流程化提上日程；mac 不在当前
  范围（明确暂不投入）。
  验收标准：README 可声明"一行安装 + 有公开分数 + PR 审查可用"。

## 6. 硬性停止条件

* 任何里程碑不得以"降低验证标准"换取完成：VERIFY 门禁、Evidence、
  审计回放必须保持。

* 不允许在 `agenthub` 路径里出现 demo/synthetic 成功。

* 涉及执行边界、状态模型、部署归属的变更必须新增/更新 ADR（文档治理
  标准 §Review requirements）。

* 北极星文档自身的每个能力声明必须链接到实现或测试，否则降级为
  目标/原型表述（同 `what-is-agenthub.md` 的能力标级约定）。

## 7. 后续维护约定

* 达成一个 M 后：更新本文档状态、补验收证据（测试链接）、刷新 §2 现状表。

* 每次发布评审：检查 §3 缺陷表是否有已解决项，同步删除。

* 方向争议时：本文档优先于旧 roadmap 中未更新的产品表述，但不得越过
  tests/contracts/ADR。

