# 5 分钟快速部署

使用 Docker Compose 在 5 分钟内启动完整的 AgentHub 平台。

## 前置条件

- Docker Desktop 4.x+ 或 Docker Engine 24+
- 至少 8GB 可用内存
- 至少 20GB 可用磁盘空间

## 一键启动

```bash
# 1. 克隆仓库
git clone https://github.com/EVEDensity/AgentHub.git
cd platform

# 2. 启动所有服务（首次约 3-5 分钟拉取镜像）
docker compose -f deploy/docker-compose.platform.yml up -d

# 3. 等待所有容器就绪
docker compose -f deploy/docker-compose.platform.yml ps
# 确认所有 24 个容器状态为 "healthy"

# 4. 打开浏览器
#    管理后台: http://localhost:3000/admin
#    Grafana:   http://localhost:3001 (admin/admin)
#    默认账号:  admin@agenthub.dev / agenthub123
```

## 创建第一个 Agent

```bash
curl -X POST http://localhost:8080/api/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "HelloBot",
    "model": "claude-sonnet-4-6",
    "systemPrompt": "You are a helpful assistant.",
    "capabilities": ["doc_writing", "code_generation"]
  }'
```

## 启动的服务

| 服务 | 端口 | 说明 |
|------|------|------|
| Gateway | 8081 | API 入口 |
| Session | 8082 | 会话管理 |
| MCP Gateway | 8099 | Model Context Protocol |
| Knowledge | 8092 | 知识库服务 |
| Model Adapter | 8091 | 模型适配 |
| Frontend | 3000 | Next.js 前端 |
| Grafana | 3001 | 监控面板 |
| PostgreSQL | 5432 | 关系数据库 |
| Qdrant | 6333 | 向量数据库 |
| Redis | 6379 | 缓存 |
| NATS | 4222 | 事件总线 |

## 下一步

- 📖 阅读 [核心概念](/zh/guide/concepts)
- 🏗️ [创建第一个 Agent](/zh/guide/create-agent)
- 🔀 [构建工作流](/zh/guide/build-workflow)
