<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/agenthub/platform/main/frontend/public/logo.svg">
    <img alt="AgentHub" src="frontend/public/logo.svg" width="120">
  </picture>
</p>

<h1 align="center">AgentHub</h1>

<p align="center">
  <b>构建 AI 智能体团队，而非聊天机器人</b><br>
  自托管多智能体协作平台。编排、部署、观测<br>
  协作式 AI 智能体 — 仅需一条 Docker 命令。
</p>

<p align="center">
  <a href="https://github.com/agenthub/platform/stargazers">
    <img src="https://img.shields.io/github/stars/agenthub/platform?style=flat-square&color=yellow" alt="Stars">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square" alt="Apache 2.0">
  </a>
  <a href="https://github.com/agenthub/platform/releases">
    <img src="https://img.shields.io/github/v/release/agenthub/platform?style=flat-square&color=purple" alt="Release">
  </a>
  <a href="CONTRIBUTING.md">
    <img src="https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square" alt="PRs Welcome">
  </a>
  <a href="https://github.com/agenthub/platform/issues">
    <img src="https://img.shields.io/github/issues/agenthub/platform?style=flat-square&color=red" alt="Issues">
  </a>
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README_CN.md">中文</a>
</p>

<p align="center">
  <a href="#-快速开始">快速开始</a> ·
  <a href="#-特性">特性</a> ·
  <a href="#-为什么选择-agenthub">为什么选择 AgentHub</a> ·
  <a href="#-参与贡献">参与贡献</a> ·
  <a href="#-开源协议">开源协议</a>
</p>

---

## ✨ 为什么选择 AgentHub？

大多数 AI 平台不过把智能体当成**加了工具调用的聊天机器人**。AgentHub 走了一条不同的路：

| ❌ 典型平台 | ✅ AgentHub |
|---|---|
| 单智能体提示词链 | **多智能体团队**，角色分工明确 |
| 黑盒执行 | **可观测** ReAct 状态机（11 态） |
| 厂商锁定 | **自带模型** — 任意 OpenAI 兼容端点 |
| 无安全护栏 | **内置 IAM** — RBAC + ABAC + 敏感工具门控 |
| 仅限云端 | **自托管** — 数据留在你自己的基础设施上 |

> **AgentHub 不只是调用 LLM，它编排智能体团队。** — Router 规划、Executor 执行、Critic 审查、Summarizer 总结。平台掌控整个循环。

---

## 🚀 快速开始

```bash
# 克隆即启动 — 一条命令搞定
git clone https://github.com/agenthub/platform.git
cd platform/deploy
docker compose -f docker-compose.platform.yml up -d
```

| 服务 | 地址 |
|---------|-----|
| 🖥️ Web 界面 | `http://localhost:3000` |
| 🔌 API 网关 | `http://localhost:8081` |
| 📊 Grafana | `http://localhost:3001` (admin / admin) |
| 📈 Prometheus | `http://localhost:9090` |

> 💡 **本地开发无需 API Key** — 内置 mock 提供商，离线即可测试全部功能。

### 想用自己的模型？

```bash
# OpenAI
export OPENAI_API_KEY=sk-...
# Anthropic Claude
export ANTHROPIC_API_KEY=sk-ant-...
# 或任意 OpenAI 兼容接口（Ollama、LiteLLM、vLLM）
export OPENAI_COMPATIBLE_BASE_URL=http://localhost:11434/v1
```

详见 [`.env.example`](.env.example) 查看全部可配置项。

---

## 🎯 特性

<table>
<tr>
<td width="50%">

### 🧠 智能体编排
- **ReAct 状态机** — 11 态循环，预算感知执行
- **6 个内置角色** — Router、Planner、Executor、Critic、Summarizer、Search
- **Redis 持久化状态** — 智能体重启后可恢复
- **默认流式输出** — WebSocket + SSE，支持回放

### 🔍 深度搜索
- **BM25 + 稠密向量 + 重排序** 融合流水线
- **优雅降级** — 向量库不可用时自动回退
- **可插拔嵌入** — OpenAI、BGE、TEI 兼容

</td>
<td width="50%">

### 🔐 安全与多租户
- **JWT 身份认证**（HS256）
- **RBAC**（4 角色）+ **ABAC** 策略引擎
- **敏感工具门控** — 高风险操作需服务端二次确认
- **租户隔离** — 数据库级数据范围控制

### 📡 平台能力
- **多提供商** — OpenAI、Anthropic、BGE、Ollama、vLLM
- **文档流水线** — PDF、DOCX、PPTX → 分块 → 嵌入 → 搜索
- **可观测性** — Prometheus + Grafana + OTLP 链路追踪
- **一条 Compose 命令**拉起全部服务

</td>
</tr>
</table>

---

## 🏗️ 架构

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────────┐
│  Next.js UI │────▶│ Go 网关      │────▶│ Go 编排引擎         │
│  (端口 3000)│     │ (端口 8081)  │     │ (8 个微服务)        │
└─────────────┘     │ WebSocket+JWT│     │ NATS JetStream      │
                    └──────┬───────┘     └──────────┬──────────┘
                           │                        │
                           ▼                        ▼
                    ┌──────────────┐     ┌─────────────────────┐
                    │ Rust 核心    │     │ Python 离线服务      │
                    │ stream       │     │ model-adapter       │
                    │ retrieval    │     │ knowledge-pipeline  │
                    │ fanout       │     │ document-pipeline   │
                    │ patch-merge  │     │ summarization       │
                    │ memory-seg   │     │ evaluation-batch    │
                    └──────────────┘     └─────────────────────┘
```

> **技术栈：** Go · Rust · Python · Next.js · NATS JetStream · PostgreSQL · Redis · Qdrant · MinIO · OpenSearch

---

## 🤝 参与贡献

AgentHub 是社区驱动的项目 — **你的贡献会让它变得更好**。无论是修复拼写错误、添加模型提供商，还是改进文档，我们都欢迎你的帮助！

### 贡献方式

| 🐛 [报告 Bug](https://github.com/agenthub/platform/issues/new) | 💡 [建议功能](https://github.com/agenthub/platform/issues/new) | 📝 [改进文档](https://github.com/agenthub/platform) | 🔌 [添加提供商](CONTRIBUTING.md) | ⭐ [Star 项目](https://github.com/agenthub/platform) |
|---|---|---|---|---|

### 贡献者入门指南

1. **阅读** [`CONTRIBUTING.md`](CONTRIBUTING.md) — 了解 Issue 优先策略、提交规范、开发环境搭建
2. **找一个**带有 [`good first issue`](https://github.com/agenthub/platform/issues?q=label%3A%22good+first+issue%22) 标签的 Issue
3. **评论** Issue，让其他人知道你在做
4. **提交 PR** — 保持聚焦、引用 Issue，我们会尽快 Review！

```bash
# 开发环境搭建
git clone https://github.com/agenthub/platform.git
cd platform
docker compose -f deploy/docker-compose.platform.yml up -d nats postgres redis

# Go 服务
cd services/go && go work sync

# 前端
cd frontend && npm install && npm run dev
```

> 🏅 **所有贡献者都会被认可** — 无论是代码、文档还是 Bug 报告。详见下方[贡献者](#-贡献者)列表。

---

## 🌟 支持项目

如果 AgentHub 对你有帮助，不妨：

- ⭐ **Star 项目** — 帮助更多人发现它
- 🐦 **分享**你的使用场景到 [Discussions](https://github.com/agenthub/platform/discussions)
- 🔧 **贡献**代码、文档或反馈建议
- 📣 **告诉**你的朋友和同事

---

## 📄 开源协议

AgentHub 基于 [Apache License 2.0](LICENSE) 开源 — 个人和商业使用均免费。

---

## 🙏 致谢

AgentHub 站在巨人的肩膀上：

- [NATS](https://nats.io/) — 云原生消息队列
- [Qdrant](https://qdrant.tech/) — 向量检索引擎
- [OpenSearch](https://opensearch.org/) — 全文搜索
- 以及每一位让这个生态成为可能的开源贡献者 ❤️

---

<p align="center">
  <sub>由 <a href="https://github.com/Density">Density</a> 与 AgentHub 社区用 ❤️ 构建</sub>
</p>
