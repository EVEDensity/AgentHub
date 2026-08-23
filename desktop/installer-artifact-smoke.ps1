[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TargetTriple
)

$ErrorActionPreference = "Stop"
$desktopDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$bundleDirectory = Join-Path $desktopDirectory "src-tauri\target\$TargetTriple\release\bundle"
$manifestPath = Join-Path $bundleDirectory "AgentHub-$TargetTriple-release.json"

if (-not (Test-Path -LiteralPath $bundleDirectory -PathType Container)) {
    throw "Installer bundle directory was not found at $bundleDirectory."
}

$installers = @(Get-ChildItem -LiteralPath $bundleDirectory -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension.ToLowerInvariant() -in @('.msi', '.exe') -and $_.Name -notlike '*-release.json' })
if ($installers.Count -eq 0) {
    throw "No MSI or NSIS installer was generated in $bundleDirectory."
}
foreach ($installer in $installers) {
    if ($installer.Length -le 0) {
        throw "Installer artifact is empty: $($installer.FullName)."
    }
}
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Release manifest was not generated beside installers: $manifestPath."
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$manifestInstallers = @($manifest.artifacts.installers)
if ($manifestInstallers.Count -ne $installers.Count) {
    throw "Release manifest lists $($manifestInstallers.Count) installers, but bundle contains $($installers.Count)."
}
foreach ($installer in $installers) {
    $digest = (Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $relativePath = [IO.Path]::GetRelativePath($desktopDirectory, $installer.FullName)
    $entry = @($manifestInstallers | Where-Object { $_.path -eq $relativePath }) | Select-Object -First 1
    if ($null -eq $entry -or $entry.sha256 -ne $digest) {
        throw "Installer manifest digest mismatch for $($installer.FullName)."
    }
}

Write-Output "Installer artifact smoke passed."
foreach ($installer in $installers) {
    Write-Output "Installer: $($installer.FullName) ($($installer.Length) bytes)"
}
