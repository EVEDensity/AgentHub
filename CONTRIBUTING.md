# Contributing to AgentHub

AgentHub is maintained by an individual developer. Contributions are appreciated, but please keep scope in mind.

## Before You Start

1. **Open an issue first** — discuss what you want to change before writing code. This prevents wasted effort.
2. **Check existing issues** — your idea may already be under discussion.

## What Makes a Good Contribution

| ✅ Welcome | ❌ Needs Discussion First |
|-----------|---------------------------|
| Bug fixes with clear reproduction | New major features |
| Documentation improvements | Architectural changes |
| New model/provider adapters | UI redesigns |
| Performance optimizations | Breaking API changes |
| Test coverage | Third-party service integrations |

## Development Setup

You need Docker, Go 1.22+, Node.js 20+, Python 3.11+, and Rust 1.78+.

```bash
# Start infrastructure
docker compose -f deploy/docker-compose.platform.yml up -d nats postgres redis

# Go services
cd services/go && go work sync

# Frontend
cd frontend && npm install && npm run dev
```

## Commit Style

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(scope): short description
fix(scope): short description
docs(scope): short description
```

**Scopes:** `gateway`, `orchestrator`, `session`, `stream`, `tool-perm`, `audit`, `iam`, `rust-core`, `model-adapter`, `frontend`, `docs`, `deploy`

## Pull Requests

- Reference an existing issue
- Keep changes focused (under ~400 lines)
- Explain why, not just what
- No compiled binaries, backups, or secrets

---

Thanks for contributing! 🚀
