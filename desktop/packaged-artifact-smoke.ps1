[CmdletBinding()]
param(
    [string]$TargetTriple
)

$ErrorActionPreference = "Stop"
$desktopDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$sidecarDirectory = Join-Path $desktopDirectory "runtime-sidecar"

if ([string]::IsNullOrWhiteSpace($TargetTriple)) {
    $rustcInfo = & rustc +1.88.0 -vV
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the pinned Rust toolchain."
    }
    $hostLine = $rustcInfo | Where-Object { $_ -like "host:*" } | Select-Object -First 1
    $TargetTriple = ($hostLine -split ":", 2)[1].Trim()
}

if ([string]::IsNullOrWhiteSpace($TargetTriple) -or $TargetTriple -notlike "*-windows-*") {
    throw "This artifact smoke supports Windows targets only; received '$TargetTriple'."
}

$releaseDirectory = Join-Path $desktopDirectory "src-tauri\target\$TargetTriple\release"
$application = Join-Path $releaseDirectory "agenthub-desktop.exe"
$bundledSidecar = Join-Path $releaseDirectory "agenthub-runtime.exe"
$stagedSidecar = Join-Path $sidecarDirectory "target\release\agenthub-runtime-$TargetTriple.exe"

foreach ($path in @($application, $bundledSidecar, $stagedSidecar)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Expected packaged artifact was not found at $path."
    }
    $item = Get-Item -LiteralPath $path
    if ($item.Length -le 0) {
        throw "Packaged artifact is empty at $path."
    }
}

$bundledHash = (Get-FileHash -LiteralPath $bundledSidecar -Algorithm SHA256).Hash
$stagedHash = (Get-FileHash -LiteralPath $stagedSidecar -Algorithm SHA256).Hash
if ($bundledHash -ne $stagedHash) {
    throw "Bundled sidecar hash $bundledHash does not match staged hash $stagedHash."
}

$applicationSize = (Get-Item -LiteralPath $application).Length
$sidecarSize = (Get-Item -LiteralPath $bundledSidecar).Length
Write-Output "Packaged artifact smoke passed."
Write-Output "Application: $application ($applicationSize bytes)"
Write-Output "Sidecar: $bundledSidecar ($sidecarSize bytes, SHA-256 $bundledHash)"
