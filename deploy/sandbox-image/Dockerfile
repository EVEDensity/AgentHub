# syntax=docker/dockerfile:1.4
# ─────────────────────────────────────────────────────────────────────
# AgentHub Sandbox Image — isolated code execution environment (P0.1-A)
# ─────────────────────────────────────────────────────────────────────
# This image is started as a one-shot container by sandbox-service when
# an Agent requests code_execute. It provides a minimal, non-root Python
# runtime with common data/HTTP libraries. Attack surface is minimized:
#   - non-root user (uid 1000)
#   - no compilers (gcc/g++ excluded by slim base)
#   - network policy enforced by sandbox-service (--network none)
#
# Build:
#   docker build -t agenthub/sandbox:latest \
#     -f deploy/sandbox-image/Dockerfile deploy/sandbox-image/
# ─────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# ── System packages (curl + jq for shell-based tooling) ──────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl jq \
    && rm -rf /var/lib/apt/lists/*

# ── Python packages (data + HTTP, no heavy ML stacks) ────────────────
RUN pip install --no-cache-dir \
        numpy==2.2.1 \
        pandas==2.2.3 \
        httpx==0.28.1 \
        requests==2.32.3

# ── Non-root user ────────────────────────────────────────────────────
WORKDIR /sandbox
RUN useradd -m -u 1000 sandbox && chown -R sandbox:sandbox /sandbox
USER sandbox

# Keep container alive so sandbox-service can exec into it
CMD ["sleep", "infinity"]
