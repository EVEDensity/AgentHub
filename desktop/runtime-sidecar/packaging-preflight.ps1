[CmdletBinding()]
param(
    [string]$TargetTriple
)

$ErrorActionPreference = "Stop"
$sidecarDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktopDirectory = Split-Path -Parent $sidecarDirectory
$tauriConfigPath = Join-Path $desktopDirectory "src-tauri\tauri.conf.json"

if (-not (Test-Path -LiteralPath $tauriConfigPath -PathType Leaf)) {
    throw "Tauri configuration was not found at $tauriConfigPath."
}

try {
    $tauriConfig = Get-Content -LiteralPath $tauriConfigPath -Raw | ConvertFrom-Json
} catch {
    throw "Tauri configuration is not valid JSON: $($_.Exception.Message)"
}

if ($tauriConfig.bundle.active -ne $true) {
    throw "Tauri bundling is disabled; set bundle.active to true before packaging."
}

$externalBins = @($tauriConfig.bundle.externalBin)
if ($externalBins.Count -ne 1 -or [string]::IsNullOrWhiteSpace([string]$externalBins[0])) {
    throw "Tauri configuration must declare exactly one non-empty externalBin."
}

$externalBin = [string]$externalBins[0]
if ([IO.Path]::IsPathRooted($externalBin)) {
    throw "externalBin must be relative to the Tauri configuration directory."
}

$externalBinBase = [IO.Path]::GetFileName($externalBin)
if ($externalBinBase -ne "agenthub-runtime") {
    throw "Unexpected runtime sidecar name '$externalBinBase'."
}

$configDirectory = Split-Path -Parent $tauriConfigPath
$externalBinPath = [IO.Path]::GetFullPath((Join-Path $configDirectory $externalBin))
$stagingDirectory = Split-Path -Parent $externalBinPath
if (-not (Test-Path -LiteralPath $stagingDirectory -PathType Container)) {
    throw "Sidecar staging directory was not found at $stagingDirectory. Run build-windows.ps1 first."
}

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
    throw "This preflight currently supports Windows sidecars only; received $TargetTriple."
}

$stagedSidecar = Join-Path $stagingDirectory "agenthub-runtime-$TargetTriple.exe"
if (-not (Test-Path -LiteralPath $stagedSidecar -PathType Leaf)) {
    throw "Target-specific sidecar was not staged at $stagedSidecar."
}

$fileInfo = Get-Item -LiteralPath $stagedSidecar
if ($fileInfo.Length -le 0) {
    throw "Target-specific sidecar is empty: $stagedSidecar."
}

Write-Output "Packaging preflight passed."
Write-Output "Tauri config: $tauriConfigPath"
Write-Output "Target: $TargetTriple"
Write-Output "Sidecar: $stagedSidecar ($($fileInfo.Length) bytes)"
