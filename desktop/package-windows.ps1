[CmdletBinding()]
param(
    [string]$TargetTriple
)

$ErrorActionPreference = "Stop"
$desktopDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$sidecarDirectory = Join-Path $desktopDirectory "runtime-sidecar"
$buildScript = Join-Path $sidecarDirectory "build-windows.ps1"
$preflightScript = Join-Path $sidecarDirectory "packaging-preflight.ps1"
$manifest = Join-Path $desktopDirectory "src-tauri\Cargo.toml"

if (-not (Test-Path -LiteralPath $buildScript -PathType Leaf)) {
    throw "Sidecar build script was not found at $buildScript."
}
if (-not (Test-Path -LiteralPath $preflightScript -PathType Leaf)) {
    throw "Packaging preflight was not found at $preflightScript."
}
if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
    throw "Tauri manifest was not found at $manifest."
}

if ([string]::IsNullOrWhiteSpace($TargetTriple)) {
    $rustcInfo = & rustc +1.88.0 -vV
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the pinned Rust toolchain."
    }
    $hostLine = $rustcInfo | Where-Object { $_ -like "host:*" } | Select-Object -First 1
    $TargetTriple = ($hostLine -split ":", 2)[1].Trim()
}

if ([string]::IsNullOrWhiteSpace($TargetTriple) -or $TargetTriple -notlike "*-windows-*") {
    throw "This packaging command supports Windows targets only; received '$TargetTriple'."
}

Write-Output "Building runtime sidecar for $TargetTriple."
& $buildScript -TargetTriple $TargetTriple
if ($LASTEXITCODE -ne 0) {
    throw "Runtime sidecar build failed."
}

Write-Output "Running packaging preflight."
& $preflightScript -TargetTriple $TargetTriple
if ($LASTEXITCODE -ne 0) {
    throw "Packaging preflight failed."
}

$tauriVersion = & cargo +1.88.0 tauri --version 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Tauri CLI is unavailable. Install it outside the product runtime with 'cargo install tauri-cli --version ^2' and rerun this command."
}
Write-Output "Using $($tauriVersion -join ' ')."

Write-Output "Building the Tauri bundle."
& cargo +1.88.0 tauri build --manifest-path $manifest --target $TargetTriple
if ($LASTEXITCODE -ne 0) {
    throw "Tauri bundle build failed."
}

Write-Output "Windows desktop bundle completed for $TargetTriple."
