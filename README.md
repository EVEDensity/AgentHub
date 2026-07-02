<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/agenthub/platform/main/frontend/public/logo.svg">
    <img alt="AgentHub" src="frontend/public/logo.svg" width="120">
  </picture>
</p>

<h1 align="center">AgentHub</h1>

<p align="center">
  <b>Build AI Agent Teams, Not Just Chatbots</b><br>
  A self-hosted multi-agent orchestration platform. Compose, deploy, and observe<br>
  collaborative AI agents — with a single Docker command.
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
  <a href="#-quick-start">Quick Start</a> ·
  <a href="#-features">Features</a> ·
  <a href="#-why-agenthub">Why AgentHub</a> ·
  <a href="#-contributing">Contributing</a> ·
  <a href="#-license">License</a>
</p>

---

## ✨ Why AgentHub?

Most AI platforms treat agents as **glorified chatbots with tool access**. AgentHub takes a different approach:

| ❌ Typical Platform | ✅ AgentHub |
|---|---|
| Single-agent prompt chains | **Multi-agent teams** with defined roles |
| Opaque execution | **Observable** ReAct state machine (11 states) |
| Vendor lock-in | **Bring your own model** — any OpenAI-compatible endpoint |
| No safety guardrails | **Built-in IAM** — RBAC + ABAC + sensitive tool gating |
| Cloud-only | **Self-hosted** — your data stays on your infrastructure |

> **AgentHub doesn't call LLMs. It orchestrates agent teams.** — Router plans, Executor acts, Critic reviews, Summarizer synthesizes. The platform owns the loop.

---

## 🚀 Quick Start

```bash
# Clone and start everything — one command
git clone https://github.com/agenthub/platform.git
cd platform/deploy
docker compose -f docker-compose.platform.yml up -d
```

| Service | URL |
|---------|-----|
| 🖥️ Web UI | `http://localhost:3000` |
| 🔌 API Gateway | `http://localhost:8081` |
| 📊 Grafana | `http://localhost:3001` (admin / admin) |
| 📈 Prometheus | `http://localhost:9090` |

> 💡 **No API key needed for local dev** — a built-in mock provider lets you test everything offline.

### Want to use your own model?

```bash
# OpenAI
export OPENAI_API_KEY=sk-...
# Anthropic Claude
export ANTHROPIC_API_KEY=sk-ant-...
# Or any OpenAI-compatible (Ollama, LiteLLM, vLLM)
export OPENAI_COMPATIBLE_BASE_URL=http://localhost:11434/v1
```

See [`.env.example`](.env.example) for all available options.

---

## 🎯 Features

<table>
<tr>
<td width="50%">

### 🧠 Agent Orchestration
- **ReAct state machine** — 11-state loop with budget-aware execution
- **6 built-in roles** — Router, Planner, Executor, Critic, Summarizer, Search
- **Redis-persisted state** — agents survive restarts
- **Streaming by default** — WebSocket + SSE with replay

### 🔍 Deep Search
- **BM25 + Dense + Rerank** fusion pipeline
- **Graceful degradation** when vector stores are unavailable
- **Pluggable embeddings** — OpenAI, BGE, TEI-compatible

</td>
<td width="50%">

### 🔐 Security & Multi-Tenancy
- **JWT authentication** with HS256
- **RBAC** (4 roles) + **ABAC** policy engine
- **Sensitive tool gating** — server-side confirmation for risky operations
- **Tenant isolation** — data scoping at the database level

### 📡 Platform
- **Multi-provider** — OpenAI, Anthropic, BGE, Ollama, vLLM
- **Document pipeline** — PDF, DOCX, PPTX → chunk → embed → search
- **Observability** — Prometheus + Grafana + OTLP tracing
- **27+ containers** via a single Docker Compose file

</td>
</tr>
</table>

---

## 🏗️ Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────────┐
│  Next.js UI │────▶│ Go Gateway   │────▶│ Go Orchestration    │
│  (port 3000)│     │ (port 8081)  │     │ (8 microservices)   │
└─────────────┘     │ WebSocket+JWT│     │ NATS JetStream      │
                    └──────┬───────┘     └──────────┬──────────┘
                           │                        │
                           ▼                        ▼
                    ┌──────────────┐     ┌─────────────────────┐
                    │ Rust Cores   │     │ Python Offline      │
                    │ stream       │     │ model-adapter       │
                    │ retrieval    │     │ knowledge-pipeline  │
                    │ fanout       │     │ document-pipeline   │
                    │ patch-merge  │     │ summarization       │
                    │ memory-seg   │     │ evaluation-batch    │
                    └──────────────┘     └─────────────────────┘
```

> **Tech Stack:** Go · Rust · Python · Next.js · NATS JetStream · PostgreSQL · Redis · Qdrant · MinIO · OpenSearch

---

## 🤝 Contributing

AgentHub is a community-driven project — **your contributions make it better**. Whether you're fixing a typo, adding a model provider, or improving documentation, we'd love your help!

### Ways to Contribute

| 🐛 [Report Bugs](https://github.com/agenthub/platform/issues/new) | 💡 [Suggest Features](https://github.com/agenthub/platform/issues/new) | 📝 [Improve Docs](https://github.com/agenthub/platform) | 🔌 [Add Providers](CONTRIBUTING.md) | ⭐ [Star the Repo](https://github.com/agenthub/platform) |
|---|---|---|---|---|

### Getting Started as a Contributor

1. **Read** [`CONTRIBUTING.md`](CONTRIBUTING.md) — issue-first policy, commit conventions, dev setup
2. **Find an issue** tagged [`good first issue`](https://github.com/agenthub/platform/issues?q=label%3A%22good+first+issue%22)
3. **Comment** on the issue to let others know you're working on it
4. **Open a PR** — keep it focused, reference the issue, and we'll review!

```bash
# Development setup
git clone https://github.com/agenthub/platform.git
cd platform
docker compose -f deploy/docker-compose.platform.yml up -d nats postgres redis

# Go services
cd services/go && go work sync

# Frontend
cd frontend && npm install && npm run dev
```

> 🏅 **All contributors are recognized** — from code to docs to bug reports. See our [contributors](#-contributors) section below.

---

## 🌟 Support the Project

If AgentHub helps you, consider:

- ⭐ **Starring the repo** — it helps others discover the project
- 🐦 **Sharing** your use case in [Discussions](https://github.com/agenthub/platform/discussions)
- 🔧 **Contributing** code, docs, or feedback
- 📣 **Telling** your friends and colleagues

---

## 📄 License

AgentHub is licensed under the [Apache License 2.0](LICENSE) — free for personal and commercial use.

---

## 🙏 Acknowledgments

AgentHub stands on the shoulders of giants:

- [NATS](https://nats.io/) — Cloud-native messaging
- [Qdrant](https://qdrant.tech/) — Vector search engine
- [OpenSearch](https://opensearch.org/) — Full-text search
- And every open-source contributor who makes this ecosystem possible ❤️

---

<p align="center">
  <sub>Built with ❤️ by <a href="https://github.com/Density">Density</a> and the AgentHub community</sub>
</p>
