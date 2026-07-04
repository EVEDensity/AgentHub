# 安全架构

AgentHub 采用纵深防御 (Defense in Depth) 策略，从网络边界到数据加密提供 5 层安全防护。

## 5 层防御模型

### Layer 0: 网络边界

- **TLS 1.3** 加密传输 (HSTS preload)
- **WAF** (ModSecurity CRS 4.x)：SQLi/XSS/CSRF/LFI 防护
- **Rate Limiting**: 100 req/s per IP (burst 200)
- **DDoS 防护**: Cloudflare Magic Transit / iptables connlimit

### Layer 1: 认证与会话

- **OIDC/SAML/LDAP** 联合身份 (Okta/AzureAD/Keycloak)
- **WebAuthn** 生物认证 (指纹/FaceID/YubiKey)
- **JWT** (RS256, 15min TTL) + Refresh Token 轮换 (7d)
- **Session Hijacking** 检测 (IP/UA 绑定 + 指纹 hash)
- **Argon2id** 密码哈希 (t=3, m=64MB, p=4)

### Layer 2: 授权 (RBAC + ABAC)

```
19 OAuth2 Scopes × 4 角色层级
Owner → Admin → Member → Viewer
```

- **RBAC**: 基于角色的访问控制
- **ABAC**: 基于属性的访问控制 (workspace_id + agent_type + time_of_day + risk_level)
- **OPA/Wasm 策略引擎**: Rego → .wasm，边缘评估 < 1ms

### Layer 3: 数据保护

- **信封加密**: DEK (AES-256-GCM, 每行) + KEK (HSM/云 KMS)
- **列级加密**: `api_key`, `sensitive_config`, `user_pii`
- **mTLS**: 内部服务间通信加密
- **密钥轮换**: DEK 自动 24h / KEK 手动 90d

### Layer 4: 审计与异常检测

- **全量审计日志**: who + when + what + which + where + result
- **Append-only 存储**: 独立 PG schema，不可删除
- **异常检测**: 异地登录 / 高频操作 / 非工作时间 / 敏感操作
- **合规导出**: SOC2 / ISO27001 / 等保 2.0 报告模板

## OWASP Top 10 防护

| 威胁 | 防护措施 |
|------|---------|
| A01 访问控制失效 | OPA 策略引擎 + API handler 中间件授权检查 |
| A02 加密失效 | TLS 1.3 only + AES-256-GCM + Argon2id |
| A03 注入 | 参数化查询 + go-playground/validator + sqlc ORM |
| A04 不安全设计 | STRIDE 威胁建模 / Sprint |
| A05 安全配置错误 | CIS Docker Benchmark + K8s Pod Security Standards |
| A06 脆弱组件 | Dependabot + govulncheck + cargo-audit + npm audit |
| A07 认证失败 | WebAuthn + Rate Limit + Account Lockout |
| A08 供应链完整性 | SLSA L3 + cosign 签名 + lockfile 验证 |
| A09 日志监控失败 | Append-only 审计 + Loki + Grafana 安全面板 |
| A10 SSRF | HTTP client 黑名单 + 白名单代理 |

## Docker 沙箱安全

```yaml
# 每个任务独立容器
security:
  seccomp: default-deny + whitelist (~50 syscalls)
  apparmor: 限制文件系统写入 (/tmp only, 100MB)
  capabilities: drop ALL
  network: 默认隔离 (none), 出站白名单
  rootfs: read-only + tmpfs for /tmp
  limits:
    memory: 512MB
    cpu: 1.0 vCPU
    timeout: 30 min
  gvisor: 可选 (用户态内核)
```

## 安全配置检查清单

部署前确认：

- [ ] TLS 证书有效且为 1.3
- [ ] 默认管理员密码已修改
- [ ] API Keys 未硬编码在代码中
- [ ] 审计日志已开启
- [ ] Rate Limiting 已配置
- [ ] CORS 白名单已配置
- [ ] K8s Pod Security Standards 已启用
- [ ] 容器镜像已通过 Trivy 扫描
- [ ] 密钥已配置轮换策略

## 下一步

- [K8s 生产部署](/zh/advanced/k8s-deployment)
- [CI/CD 流水线](/zh/advanced/cicd)
