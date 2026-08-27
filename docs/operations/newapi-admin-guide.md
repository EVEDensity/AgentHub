# new-api 管理控制台操作指南（U1）

面向首次接手网关运营的管理员，目标：30 分钟内完成"渠道 + 模型 + token"
配置。配套部署与迁移见 [deploy/newapi/README.md](../../deploy/newapi/README.md)；
故障处置、渠道熔断与回滚演练见
[newapi-channel-fuse-decision-table.md](newapi-channel-fuse-decision-table.md)。

## 1. 登录与初始检查

- 打开 `http://<host>:3000`，用 `root` 及自己设置的密码登录（首次登录先
  完成初始化；生产环境请改密并关闭注册）。
- 检查「系统设置 → 运营设置」：**自用模式** 应开启，否则模型调用会报
  `model_price_error`（关闭后需要为每个模型单独定价）。

## 2. 添加渠道（供应商连接）

路径：**渠道 → 添加渠道**。

| 字段 | 说明 | 示例 |
|---|---|---|
| 名称 | 渠道标识，建议 `agenthub-<供应商>` | `agenthub-openai` |
| 类型 | 按供应商选（OpenAI 兼容用 `OpenAI`，Claude 用 `Anthropic`） | OpenAI |
| 密钥 | 上游 API Key | `sk-...` |
| 代理地址/base_url | **不要带 `/v1`**（new-api 自动追加） | `https://api.openai.com` |
| 模型 | 逗号分隔的模型列表 | `gpt-4o,gpt-4o-mini` |
| 分组 | 默认 `default` | default |

> 一个渠道可绑定多个模型；同供应商多 key 用「批量添加」模式，new-api
> 负责轮询与故障切换。

## 3. 配置 token（AgentHub 使用的调用密钥）

路径：**令牌 → 添加令牌**。

- 名称：`agenthub-gateway`
- 额度：自用环境下勾选 **无限制**；对外按量付费则设额度与过期时间。
- **模型限制**：默认不限制（绑定渠道中的所有模型）。
- 创建后**仅显示一次完整 key**，立即复制并安全保存。替换 `AGENTHUB_NEWAPI_API_KEY`
  后重启后端即生效。

## 4. 自检清单

```bash
# 探活
curl http://127.0.0.1:3000/api/status

# 一键验证（会创建 canary 链路并打印结果）
python deploy/newapi/verify_newapi.py --base-url http://127.0.0.1:3000 --root-token <密码>
```

预期：`models exposed: [...]`、`gateway reply: ...`、`[PASS]`。

## 5. 常见问题

| 现象 | 处理 |
|---|---|
| 调用报 `model_price_error` | 打开自用模式（或给模型配置价格） |
| 报 `/v1/v1/...` 404 | 渠道 base_url 不要带 `/v1` |
| 报 `No available channel for model X` | 渠道未绑定模型 X 或渠道被禁用 |
| 报 401 Invalid token | `AGENTHUB_NEWAPI_API_KEY` 不是 new-api token（Session 登录态不能当 key） |
| 渠道 key 失效后 5xx 突增 | 查看 M3 告警并按渠道禁用/替换 key |