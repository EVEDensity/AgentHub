<#
.SYNOPSIS
Runs the real PostgreSQL LISTEN/NOTIFY production evidence gate.

.DESCRIPTION
Starts the disposable PostgreSQL 16 compose service, waits for its health
check, runs the cross-process evidence script, and always tears down the
container. Credentials are local disposable compose values and are never
written to the evidence JSON.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$compose = Join-Path $root 'docker-compose.evidence.yml'
$releaseId = if ($env:AGENTHUB_RELEASE_ID) { $env:AGENTHUB_RELEASE_ID } else { 'release-local' }
$evidenceRoot = Join-Path $root ("artifacts\production\" + $releaseId)
$env:DATABASE_URL = 'postgresql://agenthub:agenthub@127.0.0.1:55432/agenthub'
$env:AGENTHUB_PRODUCTION_EVIDENCE_DIR = $evidenceRoot

try {
    docker compose -f $compose up -d --wait
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL evidence compose startup failed' }
    python (Join-Path $root 'scripts\cli_postgres_evidence.py')
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL production evidence gate failed' }
} finally {
    docker compose -f $compose down --volumes --remove-orphans
}
