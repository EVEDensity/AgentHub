<p align="center">
  <img alt="AgentHub" src="assets/logo/AH-logo.png" width="96">
</p>

<h3 align="center">AgentHub</h3>

<p align="center">
  Build AI agent <b>teams</b>, not chatbots.<br>
  Self-hosted. Multi-agent. Observable.
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

## What is this?

AgentHub lets you spin up a team of AI agents that actually work together — the Orchestrator decomposes your goal and dispatches roles, code-review and verification agents check the work, and deploy/implementation agents finish it. Not a single-agent-with-tools trick. A real team.

Each agent runs under a bounded, observable loop that you can watch in real time. Everything streams. Everything logs. Everything's on your hardware.

## Why?

Most AI platforms wrap an LLM in a chat box and call it an agent. Then you spend weeks wiring together "multi-agent" flows that break the moment something unexpected happens.

AgentHub gives you the full loop out of the box — orchestration, IAM, sandbox execution, search, observability. You bring a model key. It brings the rest.

## Quick start

```bash
git clone https://github.com/EVEDensity/AgentHub.git
cd AgentHub
start.bat
```

That's it. PostgreSQL spins up via Docker, the backend starts, the frontend opens. No API key needed — the built-in mock provider lets you kick the tires offline.

Want your own model?

```bash
export OPENAI_API_KEY=sk-...
# or
export ANTHROPIC_API_KEY=sk-ant-...
# or any OpenAI-compatible endpoint
export OPENAI_COMPATIBLE_BASE_URL=http://localhost:11434/v1
```

Then:

```bash
docker compose -f deploy/docker-compose.platform.yml up --build
```

| Service | URL |
|----------|-----|
| Web UI | `http://localhost:3000` |
| API Gateway | `http://localhost:8081` |
| Grafana | `http://localhost:3001` |

## What's inside

**Agent orchestration** — 7 domain roles (Orchestrator, Architect, CodeGen, Review, Test, Implement, Deploy) running a bounded tool-call loop. State is persisted in PostgreSQL; streaming is delivered via WebSocket + SSE. Role roster is defined in `app/services/agent_service.py` (`DEFAULT_AGENTS`).

**Security** — JWT auth, RBAC + ABAC, sensitive-tool gating, per-tenant data isolation. Built for production, not just demos.

**Search** — BM25 + dense vectors + reranking in one pipeline. Graceful fallback when vector stores are down. Pluggable embeddings.

**Sandbox** — Code execution in isolated containers. Configurable CPU/memory limits, network policies, output sanitization.

**Observability** — Prometheus + Grafana + OTLP tracing. See what every agent is doing in real time.

## Stack

Go services over NATS JetStream. Rust for performance-critical paths (stream processing, retrieval, fanout). Python for offline/async tasks (model adaptation, document pipelines, evaluation). Next.js frontend. PostgreSQL, Redis, Qdrant, MinIO underneath.

## Contributing

PRs welcome. Check [`good first issues`](https://github.com/EVEDensity/AgentHub/issues?q=label%3A%22good+first+issue%22) for a place to start.

```bash
# Dev setup — infra only
cd deploy
docker compose -f docker-compose.platform.yml up -d nats postgres redis

# Go services
cd services/go && go work sync

# Frontend
cd frontend && npm install && npm run dev
```

Bug reports, docs, new model providers — all count. [`CONTRIBUTING.md`](CONTRIBUTING.md) has the details.

## ⭐ Star History


<a href="https://www.star-history.com/?type=date&repos=EVEDensity%2FAgentHub">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=EVEDensity/AgentHub&type=date&theme=dark&legend=top-left&sealed_token=VgBpx74x_AENROSkwv9xsAKeMy89DIenqGq6nEM65HGnj2Ki2HV5MfNkJLxl5qafaHVcJueF-kUm3eGsTVUatiMUWtyuUaYtgk4dto6j0x1FKyRjt9JGDbiQMiOZnpGIpSDP7D7VIJu5W7dj2nUeDGDBDPqMrvFYWcKqRdk4cTzrAHc4-DT4f8qCizpb" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=EVEDensity/AgentHub&type=date&legend=top-left&sealed_token=VgBpx74x_AENROSkwv9xsAKeMy89DIenqGq6nEM65HGnj2Ki2HV5MfNkJLxl5qafaHVcJueF-kUm3eGsTVUatiMUWtyuUaYtgk4dto6j0x1FKyRjt9JGDbiQMiOZnpGIpSDP7D7VIJu5W7dj2nUeDGDBDPqMrvFYWcKqRdk4cTzrAHc4-DT4f8qCizpb" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=EVEDensity/AgentHub&type=date&legend=top-left&sealed_token=VgBpx74x_AENROSkwv9xsAKeMy89DIenqGq6nEM65HGnj2Ki2HV5MfNkJLxl5qafaHVcJueF-kUm3eGsTVUatiMUWtyuUaYtgk4dto6j0x1FKyRjt9JGDbiQMiOZnpGIpSDP7D7VIJu5W7dj2nUeDGDBDPqMrvFYWcKqRdk4cTzrAHc4-DT4f8qCizpb" />
 </picture>
</a>

## Contributors

Thanks to everyone who's contributed to AgentHub — code, docs, bug reports, ideas. It all matters.

<a href="https://github.com/EVEDensity/AgentHub/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=EVEDensity/AgentHub" />
</a>

## License

Apache 2.0. 

---

<p align="center">
  <sub>Built by <a href="https://github.com/EVEDensity">Density</a> and contributors.</sub>
</p>
