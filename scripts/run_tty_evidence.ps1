[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
if (-not [Environment]::UserInteractive) { throw 'TTY evidence requires an interactive terminal' }
$root = Split-Path -Parent $PSScriptRoot
$releaseId = if ($env:AGENTHUB_RELEASE_ID) { $env:AGENTHUB_RELEASE_ID } else { 'release-local' }
$outputRoot = Join-Path $root ("artifacts\production\" + $releaseId + "\tty")
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
$env:AGENTHUB_PRODUCTION_EVIDENCE_DIR = Join-Path $root ("artifacts\production\" + $releaseId)

foreach ($width in @(40, 80, 120)) {
    $env:AGENTHUB_CLI_TTY_WIDTH = [string]$width
    python (Join-Path $root 'scripts\cli_tty_evidence.py') | Set-Content -Encoding utf8 (Join-Path $outputRoot "tty-$width.json")
    if ($LASTEXITCODE -ne 0) { throw "TTY evidence failed at width $width" }
}
