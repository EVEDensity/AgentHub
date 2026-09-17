# new-api 渠道熔断决策表与回滚演练归档（G-1）

> Status: active
> Owner: 运维 + 后端 maintainers
> Last reviewed: 2026-08-27

本文是 new-api LLM 网关的故障处置决策表（对应 v3 排期 G-1，成功标准：
覆盖 ≥90% 已知故障场景且演练记录归档），以及配套的回滚演练检查清单和
演练归档模板。原规划中的 `docs/operations/newapi-rollout-ops.md`
（v3 排期 D3）尚未创建，故按此文件独立承载渠道熔断与回滚部分；
部署与管理台操作分别见
[deploy/newapi/README.md](../../deploy/newapi/README.md) 与
[newapi-admin-guide.md](newapi-admin-guide.md)，
方案依据见
[ADR-0104](../architecture/decisions/0104-optional-newapi-llm-gateway.md)。

## 1. 适用范围与总原则

- 本文的"网关"指 new-api LLM 网关（可选供应商层，`AGENTHUB_LLM_GATEWAY=newapi`
  时启用），不是 Go 控制面 `gateway-service`。
- **分层处置原则**：优先做渠道级处置（禁用/替换 key/切换渠道），不做整站回滚；
  仅当网关自身不可用或错误率失控且短期内无法恢复时，才执行全局回滚。
- **回滚机制**（ADR-0104）：自研按供应商适配器始终保留为默认路径，
  取消 `AGENTHUB_LLM_GATEWAY` 环境变量并重启后端即回退，无需改库、无需改代码。
- **密钥纪律**：任何 key 只经环境变量注入（如 `AGENTHUB_TEST_CHANNEL_KEY`），
  不写入文档、代码或仓库；迁移/导出产物中的 key 均已脱敏。
  API key 不落盘。

## 2. 检测信号对照索引

检测信号列中引用的告警均来自现有 Prometheus 规则，逐条如下：

| 告警/指标名 | 来源文件 | 含义 |
|---|---|---|
| `NewAPI5xxRateHigh` | deploy/agenthub_rules.yml（组 `newapi_gateway`，共享栈已合并加载）；同款亦在 deploy/newapi/prometheus-rules.yml | new-api 转发 5xx 占比 >5%（近 5 分钟），severity: warning |
| `NewAPIProbeDown` | 同上两处 | 共享栈表达式 `up{job="new-api"} == 0`；独立栈版本为 `probe_success{job="new-api"} == 0`。探活失败超 2 分钟，severity: critical |
| `NewAPIErrorRateHigh` | 同上两处 | new-api 4xx/5xx 合计占比 >10%（近 10 分钟），常见于 key 失效/配额耗尽/价格未配置，severity: warning |
| `NewAPILatencySLO` | 仅 deploy/newapi/prometheus-rules.yml（独立栈版） | 转发 p95 延迟 >2s（近 10 分钟），severity: warning。注意：该条未合并进共享栈 agenthub_rules.yml |
| `NewAPIChannelKeyBalanceLow` | 同上两处 | 合成序列 `agenthub_newapi_channel_min_balance < 1e6`（由 `export_usage.py` 导出），最低渠道余额偏低预警，severity: critical |
| `SSEConnectionDrop` | deploy/agenthub_rules.yml（组 `agenthub_p2_info`，stream-delivery-service） | `delta(sse_connections[10m]) < -10` 持续 5 分钟，SSE 连接快速下降 |

> 当前规则库没有针对 new-api 容器/主机内存的专用告警，内存问题依赖
> compose 容器健康检查与 `NewAPIProbeDown` 兜底（见决策表场景 4）；
> 补充容器 RSS 告警列入演练清单改进项。

## 3. 渠道熔断决策表

| 故障场景 | 检测信号 | 网关自动行为 | 人工处置 | 回滚动作 |
|---|---|---|---|---|
| 1. 上游渠道 401/403（key 失效/越权） | 告警 `NewAPIErrorRateHigh`（4xx 占比升高）；管理台渠道"测试"按钮失败；应用侧日志出现 401/403 转发失败 | 该渠道持续失败累计达到运营后台渠道设置中的失败阈值/冷却参数后被自动禁用；同渠道批量添加的多 key 由网关轮询，坏 key 之外仍有余量时业务自动续用可用 key | 管理台确认失败原因 → 替换/修复 key 或补配额 → 用 `channel_probe.py` 复测通过后再启用渠道；余额临近耗尽提前看 `NewAPIChannelKeyBalanceLow` | 单渠道故障不回滚；仅当多数渠道 key 同时失效且无法及时更换时，执行 §4 全局回滚切回自研适配器 |
| 2. 上游 429 限流 | 告警 `NewAPIErrorRateHigh`（429 计入 4xx）；调用方报"rate limit"；伴随 p95 抖动（独立栈看 `NewAPILatencySLO`） | 批量多 key 渠道内轮询分摊压力；触发限流的 key/渠道按失败阈值与冷却参数参与调度降权 | 上游提升配额或增补 key 数量；评估下调该渠道权重、错峰高并发请求；必要时给同渠道加备用 key | 一般不回滚；若持续 429 致 `NewAPIErrorRateHigh` 长时间不停且影响核心链路，可临时全局回滚 |
| 3. 上游 5xx / 超时 | 告警 `NewAPI5xxRateHigh`（5xx 占比 >5%）或 `NewAPILatencySLO`（p95 >2s，仅独立栈规则）；用户反馈等待超时/空回复 | 失败计入渠道失败阈值，达阈值后渠道进入冷却/自动禁用；同模型映射了多个渠道时自动切换到可用渠道（故障转移） | 查上游状态页与网络连通性；可将该模型临时改绑到健康渠道；关注是否存在单渠道放大故障 | 单渠道问题不回滚；全模型大面积 5xx 且上游确认长时间故障时，按 §4 回滚到自研适配器直连 |
| 4. new-api 内存熔断：请求报 503 `system_memory_overloaded` | 应用侧收到 503 `system_memory_overloaded`；若容器被 OOM 杀掉则升级为 `NewAPIProbeDown`（critical）。无专用内存告警规则，靠容器健康检查兜底 | 网关内置内存自保护：超过 `performance_setting.monitor_memory_threshold`（默认 90%，管理台"运营设置"或 PUT /api/option/ 可调）后直接拒绝新请求返回 503，保护进程不被打挂；内存回落即自行恢复，无需重启 | 扩容内存/降低并发/重启容器释放；排查是否有大上下文或并发尖峰。**已知教训**：本机验证智谱视觉模型时曾临时把阈值调至 99 才测通——生产环境必须保持默认 90 并接入监控告警，严禁为放行流量调高，也不要随意调低（调低=更早拒流，会造成假性故障） | 内存问题短时无法缓解（无资源可扩、反复触发）→ 按 §4 全局回滚，待扩容后再切回网关模式 |
| 5. `/api/setup` 把 `SelfUseModeEnabled` 重置回 false 的初始化陷阱 | 调用返回 `model_price_error`；伴随 `NewAPIErrorRateHigh`（4xx 升高）；时间点紧随一次网关首次初始化 | 无自动行为——这是初始化接口的已知副作用，非渠道层问题，熔断不介入 | 正确顺序：先完成 `/api/setup` 初始化，再开启自用模式；事后修复 = 管理台「系统设置 → 运营设置」重新开启自用模式即可恢复 | 配置级修复，无需回滚；演练清单中保留此项确保顺序习惯固化 |
| 6. SSE 流式中断（流式响应中途断开） | 用户反馈流式输出中断/截断；告警 `SSEConnectionDrop`（10 分钟内连接数骤降 >10）；常伴随 `NewAPILatencySLO` 或上游 5xx 先兆 | 断开的流即当次对话轮终止，断流不自动续传；上层只能整体重试或由用户重发 | 先分段定位：AgentHub 流式交付层问题（`SSEConnectionDrop` 归 stream-delivery-service 维度）还是上游渠道断流；上游侧用 `verify_newapi.py` / `channel_probe.py` 复现核实；多次断流的渠道按场景 1/3 处置 | 定位在上游渠道且短期不稳 → 换渠道；整条流式链路（含应用侧流式投递）持续异常 → 按 §4 回滚并用非流式请求回归确认自研路径完好 |
| 7. new-api 网关整体宕机/不可达 | 告警 `NewAPIProbeDown`（critical，探活失败超 2 分钟）；容器重启/OOM 后通常随之而来 | 无自动故障转移——自研适配器不会被动接管（回滚必须是显式的环境变量动作，避免双路径混写），网关模式下远程模型调用在此期间失败 | 看 compose 栈容器状态与日志（`docker compose -f deploy/docker-compose.newapi.yml logs`），OOM 则先按场景 4 处理；探活 `/api/status` 恢复再观察 5 分钟 | `NewAPIProbeDown` 触发即为全局回滚的直接准入条件：立即按 §4 回滚，恢复业务后再修网关 |

## 4. 标准回滚程序（ADR-0104）

适用条件（任一满足）：`NewAPIProbeDown` 触发、`system_memory_overloaded` 反复出现且无法即时扩容、多渠道同时不可恢复导致远程模型基本不可用。

```bash
# 1) 在后端运行环境取消网关开关（自研适配层即刻成为路由目标）
unset AGENTHUB_LLM_GATEWAY AGENTHUB_NEWAPI_API_KEY

# 2) 重启后端进程，业务恢复按供应商直连；无需改库、无需改代码
```

- 回滚后验证：抽一个高频模型发起同步与流式各一次请求确认自研路径正常；
  观察应用日志无残留网关寻址报错。
- 恢复网关模式：先按场景处置根因，再设回
  `AGENTHUB_LLM_GATEWAY=newapi` 与 `AGENTHUB_NEWAPI_API_KEY` 并重启；
  通过 `verify_newapi.py` 后才算恢复完成。
- 期间 new-api 服务与数据保留不动，便于事后对账 usage。

## 5. 回滚演练检查清单

建议每季度至少一次，优先在带 `mock-llm` canary 的离线环境执行，
涉及真实渠道时可利用 `channel_probe.py` 制造可控故障。

- [ ] 选择低峰窗口并通知相关方（变更说明含"网关故障演练"字样）
- [ ] 记录演练前状态：`AGENTHUB_LLM_GATEWAY` 三态取值、当前渠道清单截图/快照
- [ ] 注入故障 A（渠道级）：对某渠道注入失效 key 或停掉 mock 渠道
- [ ] 确认告警链路可见：`NewAPIErrorRateHigh` / `NewAPI5xxRateHigh` 能否按预期触发
- [ ] 在管理台完成渠道级处置（禁用→替换→复测→启用），业务侧错误率回落
- [ ] 注入故障 B（网关级）：停止 new-api 容器，确认 `NewAPIProbeDown` 触发
- [ ] 执行 §4 标准回滚命令并重启后端，确认业务切回自研路径无感知
- [ ] 回滚后验证：同步 + 流式各一请求正常，日志无网关残留报错
- [ ] 恢复网关模式并通过 `verify_newapi.py`，关闭故障注入
- [ ] 将本次结果填入 §6 归档模板并提交存档；有改进项则登记跟进人

## 6. 演练归档模板

每次演练完成后复制下表追加一条记录（请勿删除历史行），链接列可指向
工单或告警截图存储位置（不得包含明文 key）。

| 演练日期 | 操作人 | 演练场景（对应决策表编号） | 故障注入方式 | 结果（PASS/FAIL + 一句话结论） | 改进项/链接 |
|---|---|---|---|---|---|
| （待填写） | （待填写） | （待填写） | （待填写） | （待填写） | （待填写） |
| （待填写） | （待填写） | （待填写） | （待填写） | （待填写） | （待填写） |
| （待填写） | （待填写） | （待填写） | （待填写） | （待填写） | （待填写） |

---

## 附：维护说明

- 新增已知故障场景时：先在 deploy/agenthub_rules.yml（或独立栈
  prometheus-rules.yml）落实检测告警，再更新 §2 索引与 §3 决策表，
  不允许出现没有检测信号的决策行长期滞留。
- new-api 网关配置字段名以管理台实际界面为准；本文对渠道失败阈值/
  冷却等参数使用泛化描述，避免因上游版本升级导致字段名失真。
