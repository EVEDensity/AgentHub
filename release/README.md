# AgentHub Local Project Release

> Status: implemented  
> Profile: `local-project`

This directory is the operator-facing entry point for local-first AgentHub
releases. PostgreSQL is not required by this profile. The existing PostgreSQL
adapter and evidence tooling remain available for a future `distributed`
profile, but they do not block local project work or local release readiness.

## Release inputs

The repository never stores secrets here. Before collecting real evidence,
provide only the prerequisites needed by the gate being run:

| Gate | Required input | Missing behavior |
|---|---|---|
| Benchmark | Python dependencies and `benchmarks/cli_tasks.json` | `FAIL` on threshold breach |
| Provider | `AGENTHUB_CLI_MODEL_API_KEY`, provider base URL/model configuration | `SKIP` |
| SSE recovery | deployed endpoint, auth token, Mission ID, real fault injection | `SKIP` |
| TTY | a physical terminal at widths 40, 80, and 120 | `SKIP` |
| Registry | published npm package/version and clean install target | `MISSING` until recorded |

No `DATABASE_URL`, PostgreSQL service, or PostgreSQL credential is needed for
the `local-project` profile.

## Local sequence

```powershell
$releaseId = "release-v1.0.0"
python scripts/prepare_release_evidence.py $releaseId
$env:AGENTHUB_RELEASE_ID = $releaseId
$env:AGENTHUB_PRODUCTION_EVIDENCE_DIR = "artifacts/production/$releaseId"
python -m pytest -q
python scripts/cli_benchmark.py --task-file benchmarks/cli_tasks.json --task-id conversation-basic --check-thresholds
python scripts/verify_real_evidence.py
python scripts/generate_release_manifest.py --profile local-project --evidence-root "artifacts/production/$releaseId" --output release/release-manifest.json --report release/PRODUCTION_VERIFICATION.md
```

The manifest command returns exit code `1` until every required real-world
gate has a current `PASS` record for the expected commit. This is intentional;
do not replace `SKIP`, `MISSING`, or `FAIL` evidence manually.

Always use a fresh release ID. Scanning the shared evidence root mixes records
from older commits and correctly fails the commit-consistency gate.

Use `--profile distributed` only when PostgreSQL becomes a deployment
requirement again.
