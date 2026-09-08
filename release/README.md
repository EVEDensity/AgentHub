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
| TTY | a physical terminal at widths 40, 80, and 120 | `SKIP` |
| GitHub Release | two published `cli-v*` releases for install/upgrade/rollback | `MISSING` until recorded |

No deployed SSE endpoint, `DATABASE_URL`, PostgreSQL service, or PostgreSQL
credential is needed for the `local-project` profile.

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

Use `--profile distributed` only when deployed SSE recovery and PostgreSQL
become release requirements.

## Personal developer distribution

Windows x64 is the only currently supported binary target. Push a tag such as
`cli-v0.2.0`; GitHub Actions builds and verifies `agenthub.exe`, publishes a
ZIP, `checksums.txt`, and `install.ps1` to GitHub Releases using the repository
`GITHUB_TOKEN`. No npm account or additional publishing token is required.

Install the latest release after reviewing the script:

```powershell
$script = Join-Path $env:TEMP 'agenthub-install.ps1'
Invoke-WebRequest https://github.com/EVEDensity/AgentHub/releases/latest/download/install.ps1 -OutFile $script
Get-Content $script
& $script
```

The installer downloads the selected release, verifies its SHA-256 from the
release checksum file, and atomically replaces the user-local executable.
