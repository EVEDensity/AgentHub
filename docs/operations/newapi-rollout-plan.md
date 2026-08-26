# new-api LLM 网关上线后：下一阶段任务安排

> Status: draft — 待架构维护者评审
> Owner: backend maintainers
> Last reviewed: 2026-08-26
> 前置：ADR-0104 已实施（`AGENTHUB_LLM_GATEWAY=newapi` 开关、迁移脚本、
> compose 栈、mock 链路验证通过）。本文排布"替换完成之后"的落地任务。
>
> 执行状态（2026-08-26）：T1-T5（e2e 矩阵/迁移回归/回滚演练/安全卫生）
> 单测与真实 new-api 矩阵已落地；P1 `streaming_ttft` 门禁已接入 CI；
> P2 连接池/超时对齐验证通过；P4 tokenizer 门禁可测量性测试通过
> （无原生 tokenizer 时仍诚实 SKIP）；M1/M3 告警规则、M2 用量导出脚本、
> U1 管理台手册（docs/operations/newapi-admin-guide.md）已落地；
> D1-D5 文档已更新。剩余：真实供应商渠道 e2e 需团队提供 key 后补跑。

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