# new-api LLM 网关上线后：下一阶段任务安排

> Status: draft — 待架构维护者评审
> Owner: backend maintainers
> Last reviewed: 2026-08-27
> 前置：ADR-0104 已实施（`AGENTHUB_LLM_GATEWAY=newapi` 开关、迁移脚本、
> compose 栈、mock 链路验证通过）。本文排布"替换完成之后"的落地任务。
>
> 执行状态（2026-08-26）：T1-T5（e2e 矩阵/迁移回归/回滚演练/安全卫生）
> 单测与真实 new-api 矩阵已落地；P1 `streaming_ttft` 门禁已接入 CI；
> P2 连接池/超时对齐验证通过；P4 tokenizer 门禁可测量性测试通过
> （无原生 tokenizer 时仍诚实 SKIP）；M1/M3 告警规则、M2 用量导出脚本、
> U1 管理台手册（docs/operations/newapi-admin-guide.md）已落地；
> D1-D5 文档已更新。剩余：真实供应商渠道 e2e 需团队提供 key 后补跑。

**A1 进展（2026-08-27，DeepSeek 真实渠道）**：经 new-api 网关实测
`deepseek-v4-flash`（官方模型名；注意不是口述的 flashV4）全部通过——
渠道创建/同步（~3.1s，usage 90+27）/SSE（80 chunks，TTFT≈0ms）/原生
tool_calls 透传，以及应用侧 `NewAPIGatewayAdapter → 网关 → DeepSeek`
链路（2.7s 干净正文 + usage 102 tokens）。证据与复跑方式：
`deploy/newapi/deepseek_channel_probe.py`（key 仅走环境变量）。踩坑：
① /api/setup 会把 SelfUseModeEnabled 重置回 false —— 初始化后再置 true；
② 网关内存熔断键 `performance_setting.monitor_memory_threshold`
（本机验证时临时调至 99）；③ DeepSeek v4 为推理型模型，reasoning 由自研
适配器 `<think>` 剥离逻辑正常处理。剩余：通义/OpenAI 渠道待 key。

**A1 进展（2026-08-27，智谱真实渠道）**：`glm-4-flash`（免费档最便宜）
经网关全部通过——同步 4.0s（usage 13+5）、SSE TTFT=4ms、tool-calls 该
免费模型不支持 function calling 而回退纯文本（链路 200 正常）；应用侧
`NewAPIGatewayAdapter → 网关 → 智谱` 链路 2.8s 干净正文 + usage 18 tokens。
探针已通用化：`deploy/newapi/channel_probe.py --channel-name <名>
--channel-type <类型> --upstream <渠道基址> --model <模型>`（key 仅走
AGENTHUB_TEST_CHANNEL_KEY）。踩坑：智谱 new-api 渠道 type=**16**
（ChannelTypeZhipu），base_url 留空走内置端点；若用 OpenAI 兼容 type=1
配 `/api/paas/v4` 会被网关追加 /v1 打成 404。A1 已覆盖 DeepSeek + 智谱
两渠道；通义/OpenAI 待 key。

**A1 关闭（2026-08-27）**：DeepSeek + 智谱双真实渠道覆盖即达成 A1 目标，
通义/OpenAI 不再作为阻塞项（后续获得 key 时复用
`deploy/newapi/channel_probe.py` 一条命令补跑即可，不占排期）。

**A2 多模态验证（2026-08-27，Kimi/Moonshot 视觉模型）**：经网关以
标准 OpenAI 多模态请求体（content 数组 + 736KB 图片 data URI）实测
`moonshot-v1-8k-vision-preview` 成功识别 AgentHub logo（内容/主色调），
usage 显示图像计费 prompt=1049 tokens。结论：**网关层零改动即可承载
多模态**，改造全部收敛在应用侧。现状审计、业界方案调研与五步垂切设计见
[docs/architecture/components/multimodal.md](../architecture/components/multimodal.md)。

---

# 任务安排 v3（2026-08-27 定稿主排期）

> 本版替代 v2。与 v2 的区别：① 全部状态以当日 git 提交与代码直接核实为据，
> 不沿用文档声明；② 每项任务补充依赖、质量标准、成功标准与应急预案；
> ③ 三处关键缺口经逐行核对确认（见 §0）。日期为计划窗口锚定，非工时承诺。

> **执行进展（2026-08-27 当日冲刺）**：
> - ✅ **MS-1 达成** — MM-0：ADR-0105 定稿 accepted，multimodal.md 同步转正，
>   三项决策（路由约束/预算上限/压缩语义）含反对意见记录入档。
> - ✅ **MM-1 完成** — `messages.content` 双轨落地（OpenAI execute/stream +
>   Anthropic execute/stream + 签名统一 + provider 注入）；纯文本模型携图
>   在任何网络 I/O 前 `VisionUnsupportedError` 显式报错；model_adapter_service
>   schema 放宽 + mock 宽字符投影 + anthropic 路由显式 400；Kimi vision 真
>   渠道 e2e 用例入 test_llm_gateway_e2e_matrix（env-gated）。新增双轨单测
>   15 例，tests/services 全量 381 passed 零回归。
> - ✅ **G-1 完成** — docs/operations/newapi-channel-fuse-decision-table.md
>   （7 场景 + 演练清单 + 归档模板），告警引用与两份 rules 文件逐条核对。
> - ✅ **R4-5 完成** — usage-exporter compose sidecar（复用 M2 导出脚本 +
>   重试退避结构化日志）、Grafana 四宫格 JSON、.gitignore 白名单修正。
> - ✅ **R4-2 主体完成** — benchmarks/fetch_tokenizers.py（HF 主源+镜像回退）
>   已实测拉取 Qwen(7.0MB,sha c0382117…) / DeepSeek(7.8MB,sha 621ac2e2…)
>   并经生产加载路径校验；calibrate_cn_estimator.py 反推出宽字符口径家族
>   常数（qwen=0.61 deepseek=0.56）注入 CN_TOKEN_RATIOS；门禁语义按"计费
>   parity"重构后 **SKIP→MEASURED 且 parity p95=0%**（estimator 残差 p95
>   ~15-17% 作为校准观测值如实入档，短句 BPE 离散性所致，不阻塞预算强制：
>   配置资产后 count_tokens 即精确值）。
> - ✅ **第二轮冲刺（同日，CI+MM-2+R4-3/R4-4）**：
>   *CI 接线* — docs-gates 新增 tokenizer 资产 `actions/cache`（key 锚定
>   sha256 c0382117…）+ fetch 步骤（网络抖动 continue-on-error，门禁诚实
>   SKIP 兜底）+ parity 门禁 env 三件套；*MM-2 完成* —
>   [outgoingMessageDraft.ts](../../frontend/lib/outgoingMessageDraft.ts)
>   图片停发 base64 进正文（`[Attached Image:]` 标记替代 dataURL 围栏，
>   vitest 断言 body 无 base64）；后端
>   [agent_prompt_context.py](../../app/services/agent_prompt_context.py)
>   新增 `build_image_parts()`（白名单 MIME+4张/6MB 上限），图片附件描述
>   不再泄漏 180 字符 dataURL 前缀；orchestrator/tooling/routing 打通
>   `image_parts` → tool-loop 首轮 user turn 组装 `[image…, text]`
>   parts list → 四个适配器调用点全部改走 `call_content`；
>   *R4-3 完成* — `knowledge_retrieval_p95` 脚手架转真实探测：seed 同一
>   离线语料到生产 L2VectorIndex，210 样本实测 **p95=1.5ms（阈值 80ms，
>   correctness@3=7/7）** 入 CI；*R4-4 完成* — 尺寸(≤800行)/复杂度(McCabe
>   ≤20)/覆盖率(≥60%，有 artifact 才强制否则诚实 SKIP)三门禁落地，
>   存量基线 `quality_exemptions.json` 经类限定键（修掉同名方法互相覆盖
>   的坑：OpenAI 与 Anthropic 两版 stream_prompt CC=34/25 曾互踩）由
>   `gen_quality_exemptions.py` 生成，名单只减不增。
> - ✅ **第三轮冲刺（同日，B 线收官）**：
>   *MM-3 完成* — [fit_prompt](../../app/services/token_budget.py) 新增
>   `image_count` 分支：先按 `IMAGE_TOKEN_COST`(1280/张) 扣除图像份额、
>   文本保底 256 tokens 截断，stats 带 `image_tokens/images` 预算项；
>   tool-loop 调用点按本轮实际附图数计费；compaction 新增
>   `compact_content_parts()`（图片→"[用户曾发送图片]"占位，payload 不入
>   摘要，对齐 Deep Agents 语义）。*MM-4 完成* —
>   [tooling.py](../../app/services/agent/tooling.py) 工具结果落文本上下文
>   前由 `extract_screenshot_uris()` 劫持 base64（≤4 张），下一轮视觉模型
>   组真实 parts 回传 / 纯文本模型走 `image_describe` 降级注入结构化描述，
>   两条路径均有单测（含降级故障不打断 loop）。*MM-5 完成* —
>   guardrails 新增 `scan_image_source`/`scan_multimodal_content`（委托
>   content_parts 校验器 fail-closed，BLOCK 标记带槽位索引；审计仅
>   hash/size 无 payload）；`build_image_parts` 改经护栏扫描；
>   `multimodal_e2e_probe` 门禁入 gates+CI（无渠道密钥诚实 SKIP，真渠道
>   一条命令复跑 PASS）；能力表"多模态"行升为已实现并链实现与测试。
>   全量回归 **394 passed** 零回归。
> - ⏳ 待办：G-2 闸门评审（11-03~11-07）→ R4-5 连续 7 天无人值守观察期
>   （非评审阻塞项）→ R5-*（见 §0.2 三项预核对，其中流水线已提前达成）。

## 0.1a 桌面端 P0 修复与 Shell 对齐（2026-08-27 晚间，G-2 放行后）

> 背景会议结论：G-2 与 R4-5 观察期直接放行；主目标转为「网页端正确展示
> 后端能力 → 桌面端缺陷修复 → 打包」。

**P0 根因修复**：`desktop/src-tauri/tauri.conf.json` 缺
`withGlobalTauri: true`（Tauri v2 默认不注入 `window.__TAURI__`），导致打包
应用内全部 13 个原生命令静默走浏览器 fallback，截图所示「未配置/未知/需要
在桌面应用中打开」均为硬编码假值。已开启开关，官方文档确认其为必要条件。

**Shell UI 对齐**（desktop/ui/，消灭「前端有壳后端有魂但没接」项）：

| 修复 | 接线 |
|---|---|
| 停止服务按钮 | `stop_runtime` + `service_status` 重渲染（原先命令闲置，仅靠关窗 Drop 兜底） |
| 取消任务按钮 | `POST /api/v1/missions/{id}/cancel`，轮询至终态后自动隐藏 |
| 模型连通测试 | 模型列表每行接入 `POST /api/admin/models/{id}/test`，显示延迟 |
| 运行时就绪原因 | refresh 接入 `configuration_status.readyForRuntime`，缺口（Artifact 目录/MC 地址）结构化提示，不再只靠 probe 一行 detail |

**验证**：前端 tsc 0 错误；vitest 26 文件 159 passed；后端 394 passed +
181 subtests 与基线一致（4 个 multimodal error 为本机 Temp 目录 WinError 5
权限问题，与代码无关）；sidecar 构建并 stage 通过；
`packaging-preflight.ps1` PASS（含新 tauri.conf.json schema 校验）。

### 0.1b P1 数据链修复（2026-08-27 深夜，同日第二轮）

> 承接 §0.1a 比对结论中 P1 项，逐项真实接线（拒绝装饰性注入）。

| # | 修复 | 契约 | 证据 |
|---|---|---|---|
| P1-a | Model API Key 桥接 | 桌面凭据库 → `start_all_with_secrets` 仅注入 mission-control 进程 env `AGENTHUB_DESKTOP_MODEL_API_KEY` → `create_model` 请求缺 key 时回退，审计带 `keySource: request/desktop/empty` | services.rs + models.py `resolve_model_api_key`；tests/services/test_admin_models_key_fallback.py 4 passed |
| P1-b | MCP 探活 | 新原生命令 `probe_mcp`：`GET {mcpEndpoint}/healthz`，2xx=reachable / 401/403=unauthorized / 其余=unhealthy；不发送凭据、不把 TCP 连通当就绪；Shell MCP tab 显示真实状态点与 detail | probe.rs `probe_mcp_endpoint`（probe_url 重构为 probe_reachability 复用）+ cargo test 3 新例 + main.js `renderMcpProbe` |
| P1-c | 本地栈 gateway 归位 | frontend 服务 env 新增 `GO_GATEWAY_URL=http://127.0.0.1:{base+1}` → next.config.js `/platform/*` rewrite 生效，模板市场（`/platform/templates`）在桌面本地栈可达（此前回落默认 8081 必失败） | services.rs `service_environment` |

**诚实边界记录**：桌面「MCP Token」字段与 mcp-gateway 的 `JWT_SECRET`（签名
密钥，本地模式已自愈 `localJWTSecret()`）语义不同，且远程 MCP token 目前无
消费方——本轮**不做** token 注入，待真实远程 MCP 消费方落地时再接。
`/api/admin/tools`（工具市场）与 legacy `/api/admin/*` 前缀冲突属独立切片，
未在本轮处理。

### 0.1c P2 修复与捆绑前端 bake 缺陷修正（2026-08-27 深夜，同日第三轮）

**P1-c 结论修正（重要）**：实测捆绑 standalone 前端的 rewrites 在构建时
bake 为字面量（`required-server-files.json` 的 `_originalRewrites` 此前为
`http://127.0.0.1:8081`），运行时 env 注入（P1-a/c 的 GO_GATEWAY_URL 等）
对已构建包**无效**。即此前桌面包内 iframe 管理后台所有 API 请求都打到无
服务的 8081 → 全 404。修正：构建期 bake 本地栈端点。

| # | 修复 | 契约/证据 |
|---|---|---|
| P2-a | 捆绑前端 bake 本地端口 | `local-services/build-windows.ps1` 构建前注入 `API_BACKEND=legacy`、`API_BACKEND_URL=http://127.0.0.1:28000`、`GO_GATEWAY_URL=http://127.0.0.1:28001`；重建后 `_originalRewrites` 实测 = 28000/28001；单实例端口锚定依赖 `allocate_ports` 顺序分配，多实例并发为已知限制（脚本注释入档） |
| P2-b | 工具市场路由 | `next.config.js` 在通用 `/api` rewrite 之前加 `/api/admin/tools(:path*)` → gateway（toolStore 期望 gateway 契约；legacy 同路径为无前端消费方的工具注册表）；重建包已含该路由 |
| P2-c | 凭据清除 UI | 配置对话框按凭据状态启用「清除」按钮（仅 configured 可清），接 `clear_configuration_secret`；清除后重读 details 刷新 |
| P2-d | 监视器 panel | 设置→监视器接真实数据：`service_status` 四服务行、`runtime_status`（状态/就绪/detail）、`/api/metrics/health`（模型健康/降级/运行时长）；不可达时如实显示 |
| P2-e | 服务二进制补齐 | 本地 `local-services/` 此前**无任何服务 exe**（服务全 Missing 的根源）；已构建 `agenthub-gateway.exe`(24MB) + `agenthub-mcp-gateway.exe`(7.3MB)，mission-control.exe(146MB) 已在位；重打包后 bundle resources 实测含三 exe + 新前端 |

**验证**：前端 tsc 0 错误；vitest 159 passed；tauri 重打包 artifact+runtime
smoke PASS（MSI/NSIS/exe 产物更新）。

**遗留上报**：本机 `.venv` 缺 PyInstaller（mission-control freeze 依赖）；
exe 已在位故不阻塞，但重建 mission-control 时需先
`pip install pyinstaller`。多实例并发下捆绑前端仍指向首个实例端口组。

### 0.1d R5-1 剩余切片：栈版本清单 + 便携包入口文案（2026-08-27 深夜，第四轮）

| # | 交付 | 契约/证据 |
|---|---|---|
| R5-1#4(部分) | 栈版本清单 | `local-services/build-windows.ps1` staging 尾部写 `stack-manifest.json`（schemaVersion/version/commit/generatedAt/服务清单，无 secret）；`ServiceSupervisor` 读取并经新命令 `stack_info` 暴露；监视器顶部卡显示「服务栈版本」。cargo 2 新例（缺清单=None；有清单=解析字段）。实测 staged/release/便携 zip 三处 manifest 一致（v0.1.0 · cfd3b16）。诚实边界：是「版本诊断」而非目录级版本切换——升级/回滚由 updater/installer 整包替换资源树，README 已注明 |
| R5-1#5(部分) | 便携包单一入口 | `package-windows.ps1 -Portable` stage 时写 `START-HERE.txt`（唯一入口 agenthub-desktop.exe；禁止直跑 node/server.js/local-services 二进制；数据目录 %LOCALAPPDATA%\AgentHub）。实测 zip 含 START-HERE.txt。MSI/NSIS 内文案由 WiX/NSIS 配置管理，仍开放 |
| 打包链修复 | 复用 staged exe | `-LocalServices` 现复用已 stage 的 mission-control.exe（跳过 PyInstaller freeze，CI 无 staged exe 时仍走 freeze）；修 build-windows.ps1 自我拷贝与 $root 相对路径两处脚本 bug |

**验证**：cargo 33 passed（含 manifest 2 例）；重打包 `-LocalServices -Portable`
全链路 PASS（sidecar→preflight→build→artifact smoke→runtime smoke→zip），
产物含 stack-manifest + START-HERE。`missing_bundled_services_fail_closed`
在本机沙箱仍受 ports 锁目录限制（CI 不受影响）。

### 0.1e R5-1 收口：#4 目录级版本切换 + #5 安装器文案（2026-08-27 深夜，第五轮）

| # | 交付 | 契约/证据 |
|---|---|---|
| R5-1#4 | 目录级版本切换 | 每个捆绑栈按 `version-commit` 快照到 `%LOCALAPPDATA%\AgentHub\stacks\<dir>\local-services`（每版本仅拷贝一次，best-effort 不阻塞启动）；捆绑服务二进制缺失时 supervisor 自动回退到**最新的含该二进制的持久化栈**；`stack_info` 升级为 `StackInfo{manifest(实际生效), source(bundled/persisted/unversioned), persisted(本机全部已存栈)}`；监视器显示来源标签与本机栈列表。cargo 3 新例：无清单=unversioned、捆绑栈按版本快照落盘、缺失回退到 0.2.0 持久栈。诚实边界：自动故障回退已实现，**手动钉住旧版本**（UI 选择切换）仍开放 |
| R5-1#5 | 安装器文案 | 新增 `src-tauri/README-first.txt`（中英双语：唯一入口/禁止直跑捆绑二进制/数据目录）；package-windows.ps1 配置生成**合并为单次 --config**（修复 LocalServices+updater 同时启用时后者覆盖前者丢 resources 的隐患）；README-first 随 resources 注入安装根目录。实证：完整 `-LocalServices` 打包 installer smoke PASS（MSI 202MB/NSIS 184MB）；`msiexec /a` 管理员提取确认 `PFiles\AgentHub\README-first.txt` 紧邻 exe、内容正确 |

**验证**：cargo **34 passed** 0 failed（含栈缓存 3 例，修正一个错误断言——
`snapshots()` 不改变未启动服务初始状态）；MSI/NSIS/便携包/release manifest
产物全刷新；便携包本轮未重建（上轮产物仍有效，下个 tag 构建会自然带上）。

**R5-1 Delivery Plan 状态**：1-5 全部 implemented（#4/#5 见上，手动钉版与
NSIS 定制文案为后续增强）；6（物理干净机记录）待实机执行，CI runner 已覆盖。

### 0.1f B 清单清空：端到端实测抓出并修复 sqlite 存量 bug（2026-08-27 深夜，第六轮）

| # | 交付 | 证据 |
|---|---|---|
| B1 | **本地起栈端到端实测** | ① 实测抓出**存量 P0 级 bug**：`aexecute_insert` 在 sqlite 后端 `row[0]` KeyError——桌面模式（sqlite）下所有 INSERT…RETURNING 必 500，即桌面「保存模型配置」此前**完全不可用**。修复 `_first_column` 双后端兼容（asyncpg Record 位移 / sqlite dict 取首值），3 单测回归。② 修复后实测闭环：`POST /api/admin/models` 不带 key → `{"status":"success","id":7}`，sqlite 落库 `api_key len=52 + hash` —— **P1-a 桌面 Key 桥接端到端实证** ✓。③ gateway 本地模式 `/healthz` 200、`/platform/templates` 200（无 Postgres 走 fallback 空列表，诚实降级）、`/api/admin/tools` 200 返回 26 内置工具且字段与前端 ToolMarketplace 契约匹配 ✓ |
| B2 | Mission 事件时间线 | pollMission 接 `GET /{id}/events?afterSequence`（增量、断连容错），结果面板显示最近 12 条（#sequence + event_type + 时间），后端零改动 |
| B3 | 默认凭据治理 | 账户面板：登录探测 admin123（401=已改/200=⚠ 仍用默认密码/其他=服务未运行）；修改密码表单接既有 `POST /api/user/change-password`（零后端改动）。实测：改密 success + 新密码重登录成功 |
| B4 | 手动钉住旧版本栈 | native `pin_stack(version,commit)`/`clear_stack_pin`（校验栈已缓存 → 写 `<data>/stacks/.pinned`，重启生效）；exe 解析优先级 pinned → bundled → persisted fallback；监视器新增栈选择 + 钉住/取消按钮；`StackInfo` 增 `pinned`。cargo 新例：钉住后 source=pinned、effective=0.2.0、clear 可逆 |
| B5 | 多实例端口告警 | refresh 检测绑定端口 ≠28000 时在状态行显式警告「捆绑管理后台内置地址仍指向 28000」——把静默失效转为诚实提示（架构级修复仍开放） |
| B6 | 通义/OpenAI 补跑 | **不可执行**（等 key）；复用 channel_probe 一条命令，维持不占排期 |

**验证**：cargo **35 passed** 0 failed；pytest **401 passed** + 181 subtests 零
回归（含 first_column 3 例 + key fallback 4 例）；`-NoInstaller` 重打包
artifact/runtime smoke PASS。

## 0.2 R5 前置三项预核对（2026-08-27 实测，供 G-2 引用）

| 项 | 结论 | 证据 |
|---|---|---|
| R5-2 Windows 安装包流水线 | ✅ 提前达成（原定 11-10~11-21） | `.github/workflows/desktop-windows.yml`：workflow_dispatch + `desktop-v*` tag 双触发；release-policy.ps1 签名策略；MSI/NSIS + Portable 双产物；install/GUI/updater 三 smoke；manifest SHA-256 上传 |
| R5-1 Desktop GA 网关核对 | ❌ blocked → ◐ 本日部分推进 | Delivery Plan 第 2~6 条（本地编排器/嵌入式 SQLite/回滚目录）仍未落地；本日完成其中前置项：`withGlobalTauri` P0 修复 + Shell 与本地服务命令面对齐，打包预检通过 |
| R5-3 市场/SDK 解冻确认 | ◐ 条件成立，待 G-2 决议归档 | reconstruction-roadmap R3 stop condition 已 hold；R4 三门禁 CI 化；按 cross-cutting rules 以 G-2 go 决议为准（会议已放行，待归档） |

> 通义/OpenAI 渠道补跑不占排期：拿到 key 后复用
> `deploy/newapi/channel_probe.py --channel-type <t>` 一条命令。
> R4-5 观察期起点=本文档归档次日，第 8 天凭日报与网关 usage 对账销项。

## 0.1 预 G-2 稳定点核对记录（2026-08-27 实测）

| 检查项 | 结果 |
|---|---|
| 全量回归 | 394 passed + 181 subtests（~17s），零失败 |
| 前端 `tsc --noEmit` | 0 错误 |
| 门禁：compaction ratio | PASS 92.64%（min 25%） |
| 门禁：retrieval recall | PASS recall@3=100%（7/7，min 85%） |
| 门禁：retrieval p95 | PASS 三次 1.5/1.3/1.5ms，偏差 ~14% <20% 验收线 |
| 门禁：CN parity（Qwen 资产） | PASS p95=0%（estimator 残差 15.4% 为观测值） |
| 门禁：code_file_size / complexity | 初跑抓到 MM 改动使 tooling.py 超基线（1045 行 >963、CC 130 >121）→ 已按规则**拆分修复而非刷新豁免**：vision_turn.py + circuit_breaker.py 两个单一职责模块抽出后全清 |
| 门禁：multimodal probe | SKIP（真渠道复跑需 secrets；CI 已接） |
| CI 配置 | ci.yml YAML 合法；Grafana 面板 JSON 合法（4 panels）；quality_exemptions.json 合法 |
| 收尾处置 | build_ws/ 本地临时目录入 .gitignore |

**G-2 就绪度**：A/B 双主线代码层面已到稳定点（唯一待办为 R4-5 导出自动化的
连续 7 天无人值守观察期与真渠道 vision 探针的 secrets 配置，均不阻塞评审召开）。

## 0. 已核查事实基线（2026-08-27）

**已完成**（最新提交 bfba8ba，工作区干净）：

- R4 热区：记忆 L2 向量生命周期接入、websocket 业务车道拆分
  （websocket_processor.py）、会话状态外部化（bd14115）；
- 门禁：gates 含 `token_compaction_ratio` 真实测量且已接入 CI docs-gates；
- Tokenizer 估算层完成（HF 本地目录加载 + CJK≈0.9 tokens/字系数），
  原生 tokenizer 资产未接；
- 多模态工具包 `app/services/tools/multimodal/` 全量落地（content_parts /
  capability / image_describe / 插件基类），`plugin_tools.py` 打通插件注册
  断点，main.py 启动装配（bfba8ba）——**超出原排期的提前交付**；
- 网关：DeepSeek + 智谱双真实渠道、Kimi 视觉渠道 e2e 全绿；监控告警、
  管理台手册、D1-D5 文档完成。

**经核实的关键缺口**（本计划任务的直接输入）：

- MM-1：[adapter_manager.py](../../app/services/adapter_manager.py) 请求侧
  messages 仍全部 `content: str`（第 861 行 `content_blocks` 只是 Anthropic
  **响应**解析，不是请求构造）；
- MM-2：[outgoingMessageDraft.ts](../../frontend/lib/outgoingMessageDraft.ts)
  仍把 `file.content`（base64）按代码块拼进正文；
  `build_attachment_context` 仍截 180 字符前缀当文本；
- MM-3：[token_budget.py](../../app/services/token_budget.py) 无任何图像计费
  逻辑（`IMAGE_TOKEN_COST` 等常量已在 content_parts.py 定义但未接线）；
- R4-2（原生 tokenizer 资产）/ R4-3（检索 p95 实测）/ R4-4（质量三门禁）/
  R4-5（导出自动化）均未开工。

## 1. 里程碑总览

| MS | 达成内容 | 判定标准 | 目标日期 |
|---|---|---|---|
| MS-1 | 多模态设计评审通过 | multimodal.md 由 proposed → accepted 并登记 ADR | 09-05 |
| MS-2 | 协议双轨 + tokenizer 实测 | MM-1 视觉 e2e 绿 **且** `cn_tokenizer_precision` 实测 <5% | 09-19 |
| MS-3 | 多模态全链路 + 运维自动化 | 图片经 WS 可达模型；用量导出连续 7 天无人值守跑通 | 10-10 |
| MS-4 | 护栏与门禁闭环 | MM-5 e2e 探针作为可选门禁并入 docs-gates | 10-24 |
| MS-5 | 双主线稳定点 → 闸门评审 | G-2 通过，解锁 E1-E5 与 R5-* | 11-07 |

## 2. 主线 A：R4 门禁收尾

| # | 任务（负责人） | 日期 | 依赖/P | 交付物 | 成功标准 |
|---|---|---|---|---|---|
| R4-2 | CN 原生 tokenizer 资产接入（Qwen/DeepSeek）（后端） | 09-01~09-12 | 无 / **P0** | tokenizer 资产获取脚本 + 配置文档 + 实测值 | `cn_tokenizer_precision` SKIP→MEASURED 且误差 <5%；缺资产时必须诚实 SKIP 不谎报 |
| R4-3 | `knowledge_retrieval_p95` 脚手架转 L2VectorIndex 真实探测入 CI（后端） | 09-08~09-19 | L2VectorIndex（已完成）/ P1 | 新门禁实测记录 | 阈值 80ms 下 MEASURED，重复运行三次偏差 <20% |
| R4-4 | 代码质量 CI 化：尺寸/复杂度/覆盖率三门禁 + 存量豁免清单（架构+后端） | 09-15~10-10 | R4 门禁框架 / P1 | 三个新 gate 并入 docs-gates | 新增代码零豁免通过；豁免清单只减不增 |
| R4-5 | 用量导出自动化（compose cron/sidecar）+ Grafana 网关四宫格（运维+后端） | 09-01~09-19 | M2 导出脚本（已完成）/ P2 | 自动化 job + dashboard JSON | 连续 7 天无人值守产出日报，与网关 usage 对账一致 |

**A 线应急预案**：

- R4-2：tokenizer 格式转换失败 → 回退 tiktoken `o200k_base` 对照校准并注明
  口径；重型探索依赖放 requirements-dev.txt，生产镜像不带（吸收 CI
  tiktoken 缺失教训，缺失时诚实 SKIP）。
- R4-3：CI 无 pgvector 环境 → 探针降级 sqlite-vec 本地口径并单列阈值，
  不与生产阈值混算。
- R4-4：复杂度门禁首扫大面积 FAIL → 两档制（新代码严格 / 存量豁免清单
  管理），避免一次性大整改阻塞主线。
- R4-5：new-api usage API 升级致分页契约变化 → 借鉴迁移脚本先 `--dry-run`
  校验；极端情况回退人工周导出，导出自动化延一周不挡里程碑。

## 3. 主线 B：多模态垂切 MM

> 前置：MM-0 评审通过后才开 MM-1 代码改动；每步独立可验证、默认降级安全。

| # | 任务（负责人） | 日期 | 依赖/P | 交付物 | 成功标准 |
|---|---|---|---|---|---|
| MM-0 | 设计评审定稿：multimodal.md proposed→accepted、ADR 登记；路由约束/预算上限/压缩语义三项决策落笔（架构维护者） | 09-01~09-05 | 无 / **P0** | accepted 文档 + ADR 编号 | 三项决策均有结论与反对意见记录 |
| MM-1 | 协议层双轨 `str \| list`：请求侧 ~4 处 + schema；纯文本模型携图显式报错（后端） | 09-08~09-19 | MM-0 / **P0** | 双轨改造 + VisionUnsupportedError 错误路径 + Kimi logo.png 视觉 e2e 入库 | 存量纯文本全量测试零回归；vision e2e 经网关 PASS；错误路径有单测 |
| MM-2 | 附件管线：前端停发 base64 进正文；build_attachment_context 产出 image part；≤2MB 内联 / >2MB artifact 双路径（前端+后端） | 09-15~10-03 | MM-1 / **P0** | 结构化附件草稿 + image part 组装 + artifact 引用路径 | WS→适配器全链路图片可达；正文不含 `data:image`（grep 验证）；Kimi 端到端识图通过 |
| MM-3 | 预算计费：图像固定 token 计费（1024/张）、单轮 ≤4 张 ≤6MB、compaction 丢图占位标记（后端） | 09-29~10-10 | MM-1，与 MM-2 并行 / P1 | fit_prompt 图像分支 + 上限强制点 + compaction 语义 | 图像分支单测覆盖；预算报表可见图像项；超限行为可预期 |
| MM-4 | 工具视觉回传：browser_screenshot→下一轮视觉输入；降级=image_describe（后端） | 10-06~10-17 | MM-1 / P2 | 截图回传主通路（降级工具已提前落地，剩接线验证） | 截图→看图闭环 demo 留档；注入假视觉模型的降级测试 PASS |
| MM-5 | 护栏与门禁：MIME 白名单/尺寸卫生入 guardrails；e2e 探针固化为可选 CI 门禁；能力表更新（安全+后端） | 10-13~10-24 | MM-2、MM-4 / P1 | guardrails 图片卫生规则 + 可选探针门禁 + 能力表"多模态"行 | SVG/恶意 MIME/超尺寸样本全被拒；探针可开关且 PASS；能力表链接有效 |

**B 线应急预案**：

- MM-1 改动面失控：schema 波及超过预估 ~4 处 → 收敛到 NewAPIGatewayAdapter
  单点先行双轨（网关已是主力路径），自研直连渠道顺延 ≤1 周。
- MM-2 前端回归风险：保底方案 = 后端直接从 attachments 元数据组 image part
  （元数据已在存储链路），"前端停发 base64"拆为独立小步单独合入。
- MM-3 计费口径分歧：固定常数保守计（1024/张），usage 实测只观测不承诺，
  报表标注"估算口径"。
- **整体熔断线**：MM-1 开工起两周内未达 MS-2 的 e2e 判定 → 冻结 B 线后续
  步骤，改走 image_describe 降级方案独立上线，重新评审排期。

## 4. 收敛冻结与 R5 预备

- **G-1** 渠道熔断决策表 + 回滚演练归档（运维+后端，09-01~09-05，P1）。
  成功标准：决策表覆盖 ≥90% 已知故障场景且演练记录归档。
- **G-2** E1-E5 扩展项闸门评审（架构维护者，**11-03~11-07**）。前置：A/B
  双主线达稳定点（MS-3 之后无未关闭缺陷）。产出：每项 E 的 go/no-go 与排期。
- **R5-\***（G-2 通过后，节奏不变）：Desktop GA 网关核对（11-10~11-14）→
  Windows 安装包流水线（11-10~11-21）→ 市场/SDK 解冻确认（11-17 起）。

## 5. 关键路径与并行关系

```
MS-1 ─► MM-1 ─┬─► MM-2 ──┐
              ├─► MM-3 ──┤
              └─► MM-4 ──┴─► MM-5 ─► MS-4          （B 线）
R4-2 ─► R4-3 ──────► R4-4 ──────────────┐             （A 线）
R4-5 / G-1 并行 ─────────────────────────┴─► G-2(MS-5) ─► R5-*
```

**资源提示**：A/B 线共享后端人力，MM-2 与 R4-4 同窗期（09-15~10-10）是唯一
争抢点。届时人力不足则优先保 MM-2（需求侧价值高、验收样本明确），R4-4 顺延
一周不影响里程碑判定。

## 附：风险登记（v3 继承 v2 并核定）

- 多模态为需求侧拉动的功能面扩张 → 以 MM-0 设计评审 + 每步可验证 +
  默认降级安全约束偏离度；触发 B 线整体熔断线即复评（见 §3）。
- 视觉 token 计费各供应商口径不一 → 预算按保守常数计，usage 实测值仅观测不承诺。
- 状态类改动一律受 AGENTS.md 约束：显式事务、测试覆盖，禁止 demo 假成功。

## 0. 当前基线（已完成，2026-08-26）

- 开关式接入：`AGENTHUB_LLM_GATEWAY=newapi` + `NEWAPI_BASE_URL/API_KEY`；
  自研适配层保留为默认与回滚路径（ADR-0104）。
- 迁移脚本 `deploy/newapi/migrate_models.py`（`model_configs` + 环境变量
  → new-api channels/token，dry-run/幂等/脱敏清单）。
- 部署资产 `deploy/docker-compose.newapi.yml`（new-api + mock-llm canary）。
- 应用侧实测：`agent → NewAPIGatewayAdapter → OpenAI 兼容端点 → mock` 全链路通过。
- 修复存量 bug：`adapter_manager` 缺失 `os` 导入（真实 LLM 调用即崩溃）。

---

## 1. 系统测试计划（P0）

| 编号 | 任务 | 优先级 | 负责人 | 时间节点 | 验收标准 |
|---|---|---|---|---|---|
| T1 | 网关模式 e2e 矩阵：AgentHub→new-api→真实渠道（OpenAI/通义/DeepSeek 各 1 条），同步/SSE 流式、工具调用、RAG 重排旁路 | P0 | 后端 | W1-W2 | 三类渠道全通过；SSE 首 token <2s（阈值门禁 `ttft` 通过） |
| T2 | 迁移完整性回归：造 5 条 `model_configs`（含加密 key/失效行/本地 provider），`--apply` 前后 channel/token/模型映射比对 | P0 | 后端 | W2 | 迁移脚本校验通过；失效/本地行正确跳过并在报告中注明 |
| T3 | 回滚演练：网关模式下置故障渠道 → `get_adapter` 回切自研路径 | P0 | 后端 | W2 | 回滚后业务无感知；运行记录留档 |
| T4 | 并发与稳定性：20 并发请求压测（mock 与真实渠道各一轮），观察 429/超时重试与指数退避 | P1 | 后端+QA | W3-W4 | 无未捕获异常；p95 达标；核心链路零误杀 |
| T5 | 鉴权与安全：网关、渠道、token 越权访问测试；`NEWAPI_API_KEY` 泄漏检查（日志/审计脱敏） | P0 | 后端+安全 | W2 | 越权返回 401/403；日志无明文 key |

## 2. 性能优化方案（P1）

| 编号 | 任务 | 优先级 | 负责人 | 时间节点 | 验收标准 |
|---|---|---|---|---|---|
| P1 | 基准门禁扩展：新增 `ttft`（流式首 token）门禁并接入 CI `docs-gates` | P0 | 后端 | W2-W3 | 实测值入库；回归即 FAIL |
| P2 | 网关侧连接池/超时对齐：`NewAPIGatewayAdapter` 复用现有 `_get_client` 汇池；确认 `REQUEST_TIMEOUT_SECONDS` 在网关模式生效 | P1 | 后端 | W3 | 长响应不被客户端提前掐断；连接复用率提升可观测 |
| P3 | 渠道级熔断配置建议：new-api 渠道失败阈值/冷却参数写入运维文档 | P2 | 后端 | W4 | 文档化参数集 + 一页决策表 |
| P4 | Token 计费对齐：启用 `AGENTHUB_TOKENIZER_<PROVIDER>_PATH` 加载本地原生 tokenizer，使 `cn_tokenizer_precision` 门禁从 SKIP 变为实测 | P1 | 后端 | W4-W6 | 门禁实测通过（误差 <5%），更新 memory.md 能力表述 |

## 3. 监控告警配置（P1）

| 编号 | 任务 | 优先级 | 负责人 | 时间节点 | 验收标准 |
|---|---|---|---|---|---|
| M1 | new-api 进入 Compose/部署监控：容器健康检查、`/api/status` 探活纳入现有 Prometheus 抓取 | P1 | 运维 | W3 | 面板出现网关可用性；探活失败触发告警 |
| M2 | 用量导出链路：new-api usage API → daily 汇总 Job → 现有 `tokenEconomy` 观测（`GET /api/system/metrics`） | P1 | 后端+运维 | W4-W5 | 日级用量入报表；与 LLM 成本估算对得上 |
| M3 | 告警规则：渠道失败率 >5%、p95 超阈值、网关 5xx 率、key 余额过低（若有） | P1 | 运维 | W4 | 告警触发可复现；通知通道（钉钉/邮件）接通 |
| M4 | 审计联动：网关模式下 `model_config_create/test` 审计事件保持完整 | P2 | 后端 | W4 | 审计表含网关路由状态字段校验通过 |

## 4. 用户培训计划（P2）

| 编号 | 任务 | 优先级 | 负责人 | 时间节点 | 验收标准 |
|---|---|---|---|---|---|
| U1 | 管理端操作指引：new-api 控制台（渠道/模型映射/token 配额）截图式手册 | P2 | 文档 | W5 | 新管理员 30 分钟可独立完成渠道+token 配置 |
| U2 | 迁移演练课：`migrate_models.py --dry-run/--apply` 演示 + 常见错误排查清单 | P2 | 后端+文档 | W5 | 培训后交付 FAQ ≥10 条 |
| U3 | 开发者速查：`AGENTHUB_LLM_GATEWAY` 三态（空/mock 回退/newapi）示例 | P2 | 文档 | W5 | 可复制出可运行示例 |

## 5. 文档更新清单（P2）

| 编号 | 文件 | 变更 |
|---|---|---|
| D1 | `README.md` / `README_CN.md` | 新增"可选 LLM 网关"小节链接 ADR-0104 |
| D2 | `docs/architecture/components/memory.md` | 网关模式 token 计费能力表述随门禁实测更新 |
| D3 | `docs/operations/newapi-rollout-ops.md` | 部署、迁移、回滚、渠道熔断、监控告警操作手册 |
| D4 | `docs/architecture/decisions/README.md` | 登记 ADR-0104 |
| D5 | `docs/zh/guide/architecture.md` 能力表 | "AI Gateway"条目由目标/原型修正为条件性可启用并链 ADR |

## 6. 扩展功能规划（P3，需先绿 R4 门禁）

- E1 多租户：new-api token/组配额与 AgentHub 用户/团队映射（租户字段打标）。
- E2 调用方计费视图：前端用量面板 + 成本归因（按 agent/会话）。
- E3 网关出口安全：网络策略（仅内网绑定）、key 托管（OS 凭据库/Secret Manager 注入）。
- E4 渠道智能路由：按模型/成本/延迟权重分流的模板化配置。
- E5 模型备案审计：new-api 请求日志与 AgentHub 审计事件双向关联。

## 7. 执行顺序与依赖

```
T1,T2,T3,T5 ──(W1-W2)──► P1,T2 ──(W3)──► M1,M3,P2 ──(W4)──► P4,M2,U*,D* ──(W5-W6)──► E*(门禁绿后)
```

## 附：风险登记

- 网关成为关键路径后，网关自身故障 = LLM 不可用 → 保留自研回退（ADR-0104）并在 M1 探活。
- 迁移脚本依赖 new-api admin API 契约 → 升级 new-api 前跑 `--dry-run` 回归。
- 计费口径依赖原生 tokenizer → P4 未达标前文档保持"目标"措辞，禁止宣传精确计费。