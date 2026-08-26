<p align="center">
  <img alt="AgentHub" src="assets/logo/AH-logo.png" width="96">
</p>

<h3 align="center">AgentHub</h3>

<p align="center">
  构建 AI 智能体<b>团队</b>，而非聊天机器人。<br>
  自托管 · 多智能体协作 · 全程可观测。
</p>

<p align="center">
  <a href="https://github.com/EVEDensity/AgentHub/stargazers"><img src="https://img.shields.io/github/stars/EVEDensity/AgentHub?style=flat-square&color=yellow" alt="Stars"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square" alt="License"></a>
  <a href="https://github.com/EVEDensity/AgentHub/releases"><img src="https://img.shields.io/github/v/release/EVEDensity/AgentHub?style=flat-square&color=purple" alt="Release"></a>
  <a href="https://github.com/EVEDensity/AgentHub/issues"><img src="https://img.shields.io/github/issues/EVEDensity/AgentHub?style=flat-square&color=red" alt="Issues"></a>
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README_CN.md">中文</a>
</p>

---

## 这是什么？

AgentHub 让你一键拉起一个真正协作的 AI 智能体团队 — Orchestrator 拆解任务并调度领域角色，代码审查与验证智能体把关，Implement/Deploy 落盘交付。不是那种"给聊天机器人挂几个工具"的玩具，是实打实的分工协作。

每个智能体在受控、可观测的执行循环中运行，你可以实时看到它们在干什么。一切流式输出，一切留日志，一切跑在你自己的机器上。

## 为什么选它？

大多数 AI 平台的做法：把 LLM 包进对话框，然后告诉你这叫"智能体"。你想搞多智能体协作？自己写胶水代码去吧，出问题了再慢慢排查。

AgentHub 把整个编排循环给你做好了 — 任务规划、执行、审查、汇总，外加权限管理、沙盒执行、深度搜索、可观测性。你只需要一把模型 Key，剩下都是现成的。

## 快速开始

```bash
git clone https://github.com/EVEDensity/AgentHub.git
cd AgentHub
start.bat
```

完事。会自动拉起 PostgreSQL（Docker）、后端和前端。不需要 API Key — 内置 mock 提供商可以直接离线体验。

接你自己的模型：

```bash
export OPENAI_API_KEY=sk-...
# 或者
export ANTHROPIC_API_KEY=sk-ant-...
# 或者任意 OpenAI 兼容接口 (Ollama / LiteLLM / vLLM)
export OPENAI_COMPATIBLE_BASE_URL=http://localhost:11434/v1
```

可选：把全部供应商统一收敛到 new-api 网关（多 key 故障切换、配额与用量统计）：

```bash
export AGENTHUB_LLM_GATEWAY=newapi
export AGENTHUB_NEWAPI_BASE_URL=http://127.0.0.1:3000/v1
export AGENTHUB_NEWAPI_API_KEY=sk-agenthub-xxxx
```

部署、迁移与验证见 [deploy/newapi/README.md](deploy/newapi/README.md)（ADR-0104）。

然后：

```bash
docker compose -f deploy/docker-compose.platform.yml up --build
```

| 服务 | 地址 |
|------|------|
| Web 界面 | `http://localhost:3000` |
| API 网关 | `http://localhost:8081` |
| Grafana | `http://localhost:3001` |

## 有什么能力

**智能体编排** — 7 个领域角色（Orchestrator、Architect、CodeGen、Review、Test、Implement、Deploy），受控工具调用循环，状态持久化于 PostgreSQL，重启不丢。默认流式输出，WebSocket + SSE 加回放。角色清单见 `app/services/agent_service.py`（`DEFAULT_AGENTS`）。

**安全** — JWT 认证、RBAC + ABAC、敏感工具二次确认、租户数据隔离。正经生产环境用的，不是 demo。

**搜索** — BM25 + 稠密向量 + 重排序一条龙。向量库挂了自动降级，不炸。嵌入模型可换。

**沙盒** — 容器隔离执行，CPU/内存限制、网络策略、输出脱敏都可配。

**可观测** — Prometheus + Grafana + OTLP 链路追踪。每个智能体在想什么、在做什么，一目了然。

## 技术栈

Go 服务跑在 NATS JetStream 上。Rust 扛性能关键路径（流处理、检索引擎、扇出）。Python 负责离线/异步任务（模型适配、文档管线、评估）。前端 Next.js。底层 PostgreSQL + Redis + Qdrant + MinIO。

## 参与贡献

PR 欢迎。从 [`good first issue`](https://github.com/EVEDensity/AgentHub/issues?q=label%3A%22good+first+issue%22) 入手最轻松。

```bash
# 开发环境 — 只拉基础设施
cd deploy
docker compose -f docker-compose.platform.yml up -d nats postgres redis

# Go 服务
cd services/go && go work sync

# 前端
cd frontend && npm install && npm run dev
```

提 bug、改文档、加模型提供商 — 都是贡献。详见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

## ⭐ Star 趋势

[![Star History Chart](https://api.star-history.com/svg?repos=EVEDensity/AgentHub&type=Date)](https://star-history.com/#EVEDensity/AgentHub&Date)

## 贡献者

感谢每一位为 AgentHub 付出过的人 — 代码、文档、Bug 报告、想法建议，都很珍贵。

<a href="https://github.com/EVEDensity/AgentHub/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=EVEDensity/AgentHub" />
</a>


## 协议

Apache 2.0。

---

<p align="center">
  <sub>由 <a href="https://github.com/EVEDensity">Density</a> 和社区贡献者构建</sub>
</p>
