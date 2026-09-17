#!/usr/bin/env bash
# ── CI Smoke Test ────────────────────────────────────────────────────────
# Starts docker-compose.ci.yml, waits for services to pass healthchecks,
# curls /healthz on each, tears down. Exits non-zero on any failure.
#
# Usage: bash scripts/ci-smoke-test.sh
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

COMPOSE_FILE="deploy/docker-compose.ci.yml"
TIMEOUT=120

echo "::group::Starting CI infrastructure + services"
docker compose -f "$COMPOSE_FILE" up -d --build --wait --wait-timeout "$TIMEOUT"
echo "::endgroup::"

# ── Verify infrastructure health ────────────────────────────────────────
echo "::group::Infrastructure health checks"

echo -n "  postgres ... "
docker compose -f "$COMPOSE_FILE" exec -T postgres pg_isready -U agenthub
echo "OK"

echo -n "  redis ... "
docker compose -f "$COMPOSE_FILE" exec -T redis redis-cli ping | grep -q PONG
echo "OK"

echo -n "  nats ... "
curl -sf http://localhost:8222/healthz > /dev/null
echo "OK"
echo "::endgroup::"

# ── Verify service health endpoints ─────────────────────────────────────
echo "::group::Service /healthz checks"

check_health() {
  local name="$1"
  local port="$2"
  local attempts=15
  local sleep_secs=2

  echo -n "  $name (:$port) ... "
  # `docker compose up --wait` returns as soon as a container runs when it
  # has no HEALTHCHECK, but uvicorn binds its port only after NATS connect
  # and stream creation (~1s). Probe with retries to absorb that race.
  local resp=""
  for _ in $(seq 1 "$attempts"); do
    if resp=$(curl -sf "http://localhost:$port/healthz" 2>&1); then
      break
    fi
    resp=""
    sleep "$sleep_secs"
  done

  if [ -z "$resp" ]; then
    echo "FAILED — no response after $attempts attempts"
    return 1
  fi

  # Services return {"status":"ok"} or {"status":"degraded"}.
  # "degraded" is acceptable in CI when optional deps are unavailable.
  if echo "$resp" | grep -qiE '"ok"|"degraded"|^ok$'; then
    echo "OK — $resp"
  else
    echo "FAILED — unexpected response: $resp"
    return 1
  fi
}

check_health "gateway"       8081
check_health "summarization" 8093
echo "::endgroup::"

# ── Teardown ────────────────────────────────────────────────────────────
docker compose -f "$COMPOSE_FILE" down -v 2>/dev/null || true

echo ""
echo "✓ All smoke tests passed."
