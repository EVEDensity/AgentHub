[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TargetTriple,
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$desktopDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $desktopDirectory "src-tauri\tauri.conf.json"
$releaseDirectory = Join-Path $desktopDirectory "src-tauri\target\$TargetTriple\release"
$bundleDirectory = Join-Path $releaseDirectory "bundle"

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Tauri configuration was not found at $configPath."
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $bundleDirectory "AgentHub-$TargetTriple-release.json"
}

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$applicationPath = Join-Path $releaseDirectory "agenthub-desktop.exe"
$sidecarPath = Join-Path $releaseDirectory "agenthub-runtime.exe"
foreach ($path in @($applicationPath, $sidecarPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Release artifact was not found at $path."
    }
}

try {
    $commit = (& git -C $desktopDirectory rev-parse HEAD 2>$null).Trim()
} catch {
    $commit = "unknown"
}
if ([string]::IsNullOrWhiteSpace($commit)) { $commit = "unknown" }

function Get-Artifact([string]$Path) {
    $item = Get-Item -LiteralPath $Path
    $relativePath = [Uri]::new($desktopDirectory.TrimEnd('\') + '\').MakeRelativeUri([Uri]::new($Path)).ToString().Replace('/', '\')
    [pscustomobject]@{
        path = $relativePath
        sizeBytes = [int64]$item.Length
        sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

$artifacts = [ordered]@{
    application = Get-Artifact $applicationPath
    sidecar = Get-Artifact $sidecarPath
}
$portable = Get-ChildItem -LiteralPath $bundleDirectory -Filter "AgentHub-$TargetTriple-portable.zip" -File -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $portable) { $artifacts.portableZip = Get-Artifact $portable.FullName }
$installers = @(Get-ChildItem -LiteralPath $bundleDirectory -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -ieq '.msi' -or ($_.Extension -ieq '.exe' -and $_.Name -like '*-setup.exe') })
if ($installers.Count -gt 0) { $artifacts.installers = @($installers | ForEach-Object { Get-Artifact $_.FullName }) }

$manifest = [ordered]@{
    productName = [string]$config.productName
    version = [string]$config.version
    target = $TargetTriple
    commit = $commit
    generatedAtUtc = [DateTime]::UtcNow.ToString("o")
    artifacts = [pscustomobject]$artifacts
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Output "Release manifest: $OutputPath"
