# Real Evidence Runbook

> Status: target  
> Owner: CLI maintainers  
> Last reviewed: 2026-09-06  
> Scope: external provider, TTY, and GitHub Release acceptance; distributed SSE recovery

Run `python scripts/verify_real_evidence.py` first. `SKIP` is an honest result
when a secret, TTY, deployed SSE endpoint, or package manager is unavailable.

The CI workflows `.github/workflows/cli-provider-nightly.yml`,
`github-cli-release.yml`, and `cli-package-install.yml` are the authoritative places for
real provider and GitHub Release evidence. Attach their redacted artifacts and run
URLs before upgrading a capability to `production-verified`.

Required evidence includes DeepSeek v4-flash/v4-pro text streaming and native
tool-call, physical TTY widths 40/80/120, an injected SSE disconnect followed
by `Last-Event-ID` recovery, and clean Windows/macOS/Linux npm install,
upgrade, and rollback. The default `local-project` release profile does not
require PostgreSQL or deployed SSE recovery. Both remain compatibility gates
for the `distributed` profile and are not part of current local-first release
work.

## Evidence scripts

All gates use `scripts/production_evidence.py`. A real run writes one redacted
JSON file below `artifacts/production/<scope>/`; setting an explicit output
environment variable additionally writes a CI-upload mirror. Each record has
`runId`, `commit`, bounded `environment`, `evidenceLevel`, and `status`.

```text
python scripts/cli_provider_smoke.py
python scripts/cli_provider_mission_smoke.py
python scripts/cli_sse_recovery_evidence.py
python scripts/cli_tty_evidence.py
python scripts/cli_benchmark.py --task-file benchmarks/cli_tasks.json \
  --task-id conversation-basic --check-thresholds
```

The SSE gate requires a deployed Mission Control endpoint, an auth token, a mission ID,
and a proxy that closes the first stream after a durable event; it refuses to
claim recovery unless `AGENTHUB_CLI_SSE_FAULT_INJECTED=1` is present. The TTY
gate must be launched from a real terminal with
`AGENTHUB_CLI_TTY_WIDTH=40`, `80`, or `120`; redirected output is `SKIP`.

The scheduled workflow `.github/workflows/cli-production-evidence.yml` runs
the benchmark gate and runs SSE only when its endpoint and
secret variables are configured. The physical TTY gate remains a manual
cross-platform acceptance step because a hosted CI pipe is not a physical
terminal.
