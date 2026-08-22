[CmdletBinding()]
param(
    [string]$TargetTriple
)

$ErrorActionPreference = "Stop"
$sidecarDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$manifest = Join-Path $sidecarDir "Cargo.toml"

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
if ($TargetTriple -notlike "*-windows-*") {
    throw "This build helper only stages Windows sidecars; received $TargetTriple."
}

& cargo +1.88.0 build --release --locked --offline --manifest-path $manifest --target $TargetTriple
if ($LASTEXITCODE -ne 0) {
    throw "Runtime sidecar build failed."
}

$source = Join-Path $sidecarDir "target\$TargetTriple\release\agenthub-runtime.exe"
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Built sidecar was not found at $source."
}

$stagingDirectory = Join-Path $sidecarDir "target\release"
New-Item -ItemType Directory -Force -Path $stagingDirectory | Out-Null
$staged = Join-Path $stagingDirectory "agenthub-runtime-$TargetTriple.exe"
Copy-Item -LiteralPath $source -Destination $staged -Force
Write-Output "Staged $staged for Tauri externalBin packaging."
