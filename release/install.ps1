[CmdletBinding()]
param(
    [string]$Version = 'latest',
    [string]$Repository = 'EVEDensity/AgentHub',
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'AgentHub\bin'),
    [string]$ArchivePath = '',
    [string]$ChecksumsPath = '',
    [switch]$NoPathUpdate
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$assetName = 'agenthub-windows-x64.zip'
$tag = if ($Version -eq 'latest') { $null } elseif ($Version -match '^cli-v') { $Version } else { "cli-v$Version" }
$releaseBase = if ($tag) {
    "https://github.com/$Repository/releases/download/$tag"
} else {
    "https://github.com/$Repository/releases/latest/download"
}
$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ("agenthub-install-" + [guid]::NewGuid().ToString('N'))

function Invoke-AgentHubDownload([string]$Uri, [string]$OutFile) {
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source --ssl-revoke-best-effort -L --fail --silent --show-error `
            --connect-timeout 20 --max-time 600 --output $OutFile $Uri
        if ($LASTEXITCODE -ne 0) {
            throw "Release download failed with curl exit code $LASTEXITCODE."
        }
        return
    }
    Invoke-WebRequest -Uri $Uri -OutFile $OutFile -TimeoutSec 600
}

try {
    New-Item -ItemType Directory -Path $temporary | Out-Null
    $archive = if ($ArchivePath) { (Resolve-Path -LiteralPath $ArchivePath).Path } else { Join-Path $temporary $assetName }
    $checksums = if ($ChecksumsPath) { (Resolve-Path -LiteralPath $ChecksumsPath).Path } else { Join-Path $temporary 'checksums.txt' }
    if ([bool]$ArchivePath -ne [bool]$ChecksumsPath) {
        throw 'ArchivePath and ChecksumsPath must be supplied together.'
    }
    if (-not $ArchivePath) {
        Invoke-AgentHubDownload "$releaseBase/$assetName" $archive
        Invoke-AgentHubDownload "$releaseBase/checksums.txt" $checksums
    }

    $checksumPattern = '^([A-Fa-f0-9]{64})\s+\*?' + [regex]::Escape($assetName) + '$'
    $checksumLine = Get-Content -LiteralPath $checksums | Where-Object {
        $_ -match $checksumPattern
    } | Select-Object -First 1
    if (-not $checksumLine) { throw "Checksum for $assetName is missing." }
    $expected = ([regex]::Match($checksumLine, '^[A-Fa-f0-9]{64}')).Value.ToLowerInvariant()
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw 'Downloaded archive failed SHA-256 verification.' }

    $expanded = Join-Path $temporary 'expanded'
    Expand-Archive -LiteralPath $archive -DestinationPath $expanded
    $source = Join-Path $expanded 'agenthub.exe'
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw 'Release archive does not contain agenthub.exe.'
    }

    New-Item -ItemType Directory -Force -Path $InstallDirectory | Out-Null
    $destination = Join-Path $InstallDirectory 'agenthub.exe'
    $staged = Join-Path $InstallDirectory ('.agenthub-' + [guid]::NewGuid().ToString('N') + '.exe')
    Copy-Item -LiteralPath $source -Destination $staged
    Move-Item -LiteralPath $staged -Destination $destination -Force

    if (-not $NoPathUpdate) {
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        $entries = @($userPath -split ';' | Where-Object { $_ })
        if ($entries -notcontains $InstallDirectory) {
            $updated = (@($entries) + $InstallDirectory) -join ';'
            [Environment]::SetEnvironmentVariable('Path', $updated, 'User')
        }
        if (($env:Path -split ';') -notcontains $InstallDirectory) {
            $env:Path = "$InstallDirectory;$env:Path"
        }
    }

    & $destination --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Installed AgentHub CLI failed its startup check.' }
    Write-Output "AgentHub CLI installed at $destination"
} finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}
