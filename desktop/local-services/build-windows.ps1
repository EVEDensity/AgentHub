[CmdletBinding()]
param(
    [string]$OutputDirectory = "$PSScriptRoot\..\src-tauri\local-services",
    [string]$MissionControlBinary = $env:AGENTHUB_MISSION_CONTROL_BINARY,
    [switch]$BuildMissionControl
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..\..\").Path
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$goCache = Join-Path $root '.tmp\go-cache'
New-Item -ItemType Directory -Force -Path $goCache | Out-Null

if ($BuildMissionControl) {
    & (Join-Path $PSScriptRoot 'build-mission-control.ps1') -OutputDirectory $OutputDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Mission Control freeze failed.' }
    $MissionControlBinary = Join-Path $OutputDirectory 'agenthub-mission-control.exe'
}

if ([string]::IsNullOrWhiteSpace($MissionControlBinary) -or -not (Test-Path -LiteralPath $MissionControlBinary -PathType Leaf)) {
    & (Join-Path $PSScriptRoot 'build-mission-control.ps1') -OutputDirectory $OutputDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Mission Control freeze failed.' }
    $MissionControlBinary = Join-Path $OutputDirectory 'agenthub-mission-control.exe'
}

Copy-Item -LiteralPath $MissionControlBinary -Destination (Join-Path $OutputDirectory 'agenthub-mission-control.exe') -Force
$frontendRoot = Join-Path $root 'frontend'
Push-Location $frontendRoot
try {
    # The standalone bundle bakes next.config.js rewrites at build time
    # (_originalRewrites in .next/required-server-files.json), so the desktop
    # local stack endpoints MUST be baked here; runtime env cannot change
    # them. Anchors rely on ServiceSupervisor::allocate_ports handing the
    # first desktop instance the 28000 group. Concurrent second instances get
    # their own port groups while the bundled frontend still targets
    # 28000/28001 — a documented single-instance limitation.
    $env:API_BACKEND = 'legacy'
    $env:API_BACKEND_URL = 'http://127.0.0.1:28000'
    $env:GO_GATEWAY_URL = 'http://127.0.0.1:28001'
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Next.js production build failed.' }
} finally {
    Remove-Item Env:API_BACKEND, Env:API_BACKEND_URL, Env:GO_GATEWAY_URL -ErrorAction SilentlyContinue
    Pop-Location
}
$standalone = Join-Path $frontendRoot '.next\standalone'
if (-not (Test-Path -LiteralPath (Join-Path $standalone 'server.js') -PathType Leaf)) { throw 'Next.js standalone server.js was not generated.' }
$frontendOutput = Join-Path $OutputDirectory 'frontend'
if (Test-Path -LiteralPath $frontendOutput) { Remove-Item -LiteralPath $frontendOutput -Recurse -Force }
New-Item -ItemType Directory -Force -Path $frontendOutput | Out-Null
Get-ChildItem -LiteralPath $standalone -Force | Copy-Item -Destination $frontendOutput -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $frontendOutput '.next') | Out-Null
Copy-Item -LiteralPath (Join-Path $frontendRoot '.next\static') -Destination (Join-Path $frontendOutput '.next\static') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $frontendRoot 'public') -Destination (Join-Path $frontendOutput 'public') -Recurse -Force
# Next's development error overlay is never loaded by a production server and
# contains very deep paths that break Windows Explorer ZIP extraction.
$devOverlay = Join-Path $frontendOutput 'node_modules\next\dist\client\components\react-dev-overlay'
if (Test-Path -LiteralPath $devOverlay) { Remove-Item -LiteralPath $devOverlay -Recurse -Force }
Get-ChildItem -LiteralPath $frontendOutput -Recurse -File -Include '*.text.js','*.map' -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue
$nodeCommand = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
if ([string]::IsNullOrWhiteSpace($nodeCommand)) { throw 'node.exe is required to package the standalone frontend.' }
Copy-Item -LiteralPath $nodeCommand -Destination (Join-Path $frontendOutput 'node.exe') -Force
Write-Output "Next.js standalone frontend staged in $frontendOutput"
Push-Location "$root\services\go"
try {
    $env:GOCACHE = $goCache
    go build -trimpath -ldflags '-s -w' -o (Join-Path $OutputDirectory 'agenthub-gateway.exe') ./gateway-service/cmd/gateway-service
    if ($LASTEXITCODE -ne 0) { throw 'Gateway build failed.' }
    go build -trimpath -ldflags '-s -w' -o (Join-Path $OutputDirectory 'agenthub-mcp-gateway.exe') ./mcp-gateway/cmd/mcp-gateway
    if ($LASTEXITCODE -ne 0) { throw 'MCP Gateway build failed.' }
} finally { Pop-Location }

Write-Output "Local service binaries staged in $((Resolve-Path $OutputDirectory).Path)"
