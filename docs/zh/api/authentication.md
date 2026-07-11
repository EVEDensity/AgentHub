# API 认证

AgentHub 使用 JWT (RS256) 进行 API 认证，支持 API Key 和 OAuth2 两种方式。

## 认证方式

### API Key (推荐用于自动化)

```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
  http://localhost:8080/api/agents
```

在管理后台 **设置 → API Keys** 中创建和管理密钥。

### OAuth2 (推荐用于第三方应用)

支持 OIDC/SAML/LDAP 联合身份认证：

1. 注册 OAuth2 客户端 → 获取 `client_id` / `client_secret`
2. 用户授权 → 获取 `authorization_code`
3. 换取 `access_token` (15min TTL) + `refresh_token` (7d)

### JWT Token

```json
{
  "sub": "user-42",
  "workspace_id": "ws-default",
  "scopes": ["agent:read", "agent:write", "workflow:execute"],
  "exp": 1712345678,
  "iat": 1712344778
}
```

## Scopes (权限范围)

AgentHub 定义了 19 个 OAuth2 scopes：

| Scope | 说明 |
|-------|------|
| `agent:read` | 查看 Agent 配置 |
| `agent:write` | 创建/修改/删除 Agent |
| `agent:execute` | 调用 Agent 执行任务 |
| `workflow:read` | 查看工作流 |
| `workflow:write` | 创建/修改/删除工作流 |
| `workflow:execute` | 触发工作流执行 |
| `knowledge:read` | 检索知识库 |
| `knowledge:write` | 上传/管理文档 |
| `workspace:admin` | 工作区管理 |
| `user:manage` | 用户和角色管理 |
| `audit:read` | 查看审计日志 |
| `mcp:manage` | MCP 工具注册管理 |

## 安全特性

- **Argon2id** 密码哈希 (t=3, m=64MB, p=4)
- **WebAuthn** 生物认证 (指纹/FaceID/YubiKey)
- **IP/UA 绑定** + 会话劫持检测
- **Rate Limiting**：100 req/s per IP (burst 200)
- **Account Lockout**：5 次失败后锁定

## 刷新 Token

```bash
curl -X POST http://localhost:8080/api/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "YOUR_REFRESH_TOKEN"}'
```

## 下一步

- [Agent API](/zh/api/agent) — Agent CRUD 操作
- [安全架构](/zh/advanced/security) — 纵深防御模型
