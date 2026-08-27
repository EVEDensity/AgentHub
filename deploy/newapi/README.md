# new-api LLM 网关（可选供应商层）

AgentHub 默认使用自研按供应商适配器。`AGENTHUB_LLM_GATEWAY=newapi` 时，
所有远程模型统一经 new-api 的 OpenAI 兼容入口转发。方案依据 ADR-0104。

## 目录

- `docker-compose.newapi.yml` — new-api + 本地 mock-llm canary 上游
- `Dockerfile.mock-llm` / `mock_llm.py` — OpenAI 兼容 mock 上游（离线验证用）
- `migrate_models.py` — 将 `model_configs` 表与环境变量 key 迁移为 new-api 渠道
- `verify_newapi.py` — 上线后一键验证全链路
- `export_usage.py` — 日级用量导出（M2）+ Prometheus textfile 指标
- `prometheus-rules.yml` — 网关告警规则（M3）
- 控制台操作手册：[docs/operations/newapi-admin-guide.md](../../docs/operations/newapi-admin-guide.md)

## 启动（服务器 / 有 Docker 的环境）

```bash
# 生产环境务必设置：
export NEWAPI_SESSION_SECRET=<随机串>
export NEWAPI_ROOT_TOKEN=sk-agenthub-root
docker compose -f deploy/docker-compose.newapi.yml up -d --build
# 管理台 http://<host>:3000   root / <NEWAPI_ROOT_TOKEN 之外的管理员密码>
```

> 网络受限/本机验证：可用 Release 二进制直接运行
> （`new-api-v1.*.exe`），需设 `SQL_DSN`、`SESSION_SECRET`、`INITIAL_ROOT_TOKEN`。

## 数据迁移

```bash
# 预览（只读，不落盘到 new-api）
python deploy/newapi/migrate_models.py

# 正式迁移（幂等，重复执行会跳过已存在渠道）
python deploy/newapi/migrate_models.py --apply \
  --base-url http://127.0.0.1:3000 --root-token sk-agenthub-root
```

迁移产物 `deploy/newapi/migration-report.json`（key 已脱敏）：
渠道清单、跳过原因、模型映射、网关 token 前缀。

## 应用侧启用

```bash
export AGENTHUB_LLM_GATEWAY=newapi
export AGENTHUB_NEWAPI_BASE_URL=http://127.0.0.1:3000/v1
# 用管理台创建的 scoped token（或 INITIAL_ROOT_TOKEN）
export AGENTHUB_NEWAPI_API_KEY=sk-agenthub-xxxx
```

重启后端后，远程供应商的模型调用经网关路由；`mock` 与本地方案
（Ollama/CLI/cloud_code）保持直连。`/api/memory/semantic` 等 RAG 路径的
rerank 保留在自研侧（`model_adapter_service`），不受影响。

## 验证

```bash
python deploy/newapi/verify_newapi.py \
  --base-url http://127.0.0.1:3000 --root-token sk-agenthub-root
```

预期输出：`models exposed: ['mock-llm']`、`gateway reply: [mock:mock-llm] ...`、
`[PASS] gateway verified at ...`。

## 上线要点（实测踩坑）

- **自用模式**：new-api 默认要求为模型配置价格。自用/离线请在管理台
  「系统设置 → 运营设置」开启 **自用模式**（`SelfUseModeEnabled=true`），
  否则 `/v1/chat/completions` 返回 `model_price_error`。
- **渠道 base_url 不带 `/v1`**：new-api 会自动向渠道 `base_url` 追加
  `/v1`，若配置里写了 `.../v1` 会请求变成 `/v1/v1` 返回 404。mock 渠道应
  用 `http://127.0.0.1:8101`（不带 `/v1`）。
- **token key 掩码**：new-api 的 API 响应对 token key 全程掩码（仅创建
  时提示一次）。自用可本地读 sqlite（`tokens.key`，文件默认
  `one-api.db`）；多机部署请从管理台复制并注入密钥管理。迁移脚本不会把
  明文 key 写入任何报告。
- **幂等**：`migrate_models.py --apply` 可重复执行；已存在的渠道/token
  自动跳过。
- **setup 会重置选项**：`/api/setup`（首次初始化）会把
  `SelfUseModeEnabled` 重置回 false —— 正确顺序是先完成初始化，再开启自用模式。
- **内存熔断**：网关自带内存自保护，默认阈值 90%，超限返回 503
  `system_memory_overloaded`。键为
  `performance_setting.monitor_memory_threshold`（管理台「运营设置」或
  PUT /api/option/ 可调）；低内存验证机可临时调高，生产建议保留 90 并接入 M3 告警。
- **真实渠道探针**：通用版 `AGENTHUB_TEST_CHANNEL_KEY=<key>
  python deploy/newapi/channel_probe.py --channel-name <名> --channel-type
  <类型> --upstream <基址> --model <模型>`（key 只经环境变量）。已验证：
  DeepSeek（type=1，`https://api.deepseek.com`，`deepseek-v4-flash`）、
  智谱（**type=16**，base_url 留空走内置端点，`glm-4-flash` 免费）。
  注意：type=1 渠道的 base_url 一律不带版本段（网关自动追加 `/v1`）；
  智谱若用 type=1 + `/api/paas/v4` 会得到 404。

## 回滚

自研适配层始终保留为默认路径，回滚=取消环境变量：

```bash
unset AGENTHUB_LLM_GATEWAY AGENTHUB_NEWAPI_API_KEY
# 重启后端即恢复按供应商直连；无需改库、无需改代码。
```

## 安全注意

网关 key 属敏感配置，走密钥管理注入；对外暴露时强制 HTTPS 或仅绑定内网
(0.0.0.0 绑定的部署应在网络策略层收敛到 127.0.0.1/内网)。