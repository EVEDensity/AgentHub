[CmdletBinding()]
param(
    [string]$TargetTriple
)

$ErrorActionPreference = "Stop"
$sidecarDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$manifest = Join-Path $sidecarDir "Cargo.toml"
$releaseDir = Join-Path $sidecarDir "target\release"

if ([string]::IsNullOrWhiteSpace($TargetTriple)) {
    $rustcInfo = & rustc +1.88.0 -vV
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the pinned Rust toolchain."
    }
    $hostLine = $rustcInfo | Where-Object { $_ -like "host:*" } | Select-Object -First 1
    $TargetTriple = ($hostLine -split ":", 2)[1].Trim()
}

if ([string]::IsNullOrWhiteSpace($TargetTriple)) {
    throw "Target triple is missing."
}

& cargo +1.88.0 build --release --locked --offline --manifest-path $manifest
if ($LASTEXITCODE -ne 0) {
    throw "Runtime sidecar build failed."
}

$source = Join-Path $releaseDir "agenthub-runtime.exe"
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Built sidecar was not found at $source."
}

$staged = Join-Path $releaseDir "agenthub-runtime-$TargetTriple.exe"
Copy-Item -LiteralPath $source -Destination $staged -Force
Write-Output "Staged $staged for Tauri externalBin packaging."
