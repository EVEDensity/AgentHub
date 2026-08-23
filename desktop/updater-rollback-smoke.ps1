[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$TargetTriple)

$ErrorActionPreference = "Stop"
$desktopDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$releaseDirectory = Join-Path $desktopDirectory "src-tauri\target\$TargetTriple\release"
$bundleDirectory = Join-Path $releaseDirectory 'bundle'
$sidecar = Join-Path $releaseDirectory 'agenthub-runtime.exe'
$signatures = @(Get-ChildItem -LiteralPath $bundleDirectory -Recurse -File -Filter '*.sig' -ErrorAction SilentlyContinue)
if ($signatures.Count -eq 0) { throw "No signed updater artifacts found in $bundleDirectory." }
if (-not (Test-Path -LiteralPath $sidecar -PathType Leaf)) { throw "Packaged sidecar was not found at $sidecar." }

$root = Join-Path ([IO.Path]::GetTempPath()) ("agenthub-updater-rollback-" + [guid]::NewGuid().ToString('N'))
$oldPath = Join-Path $root 'old\agenthub-runtime.exe'
$candidatePath = Join-Path $root 'candidate\agenthub-runtime.exe'
New-Item -ItemType Directory -Force -Path (Split-Path $oldPath), (Split-Path $candidatePath) | Out-Null
Copy-Item -LiteralPath $sidecar -Destination $oldPath
Copy-Item -LiteralPath $sidecar -Destination $candidatePath
$oldDigest = (Get-FileHash -LiteralPath $oldPath -Algorithm SHA256).Hash
try {
    [IO.File]::WriteAllBytes($candidatePath, [Text.Encoding]::ASCII.GetBytes('invalid updater candidate'))
    try { Start-Process -FilePath $candidatePath -ArgumentList '--health-endpoint','http://127.0.0.1:18197/readyz' -Wait -WindowStyle Hidden | Out-Null } catch { }
    Copy-Item -LiteralPath $oldPath -Destination $candidatePath -Force
    if ((Get-FileHash -LiteralPath $candidatePath -Algorithm SHA256).Hash -ne $oldDigest) { throw 'Rollback digest mismatch.' }
    $process = Start-Process -FilePath $candidatePath -ArgumentList '--health-endpoint','http://127.0.0.1:18197/readyz' -PassThru -WindowStyle Hidden
    try {
        $ready = $false
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            try { $response = Invoke-RestMethod -Uri 'http://127.0.0.1:18197/readyz' -TimeoutSec 1; if ($response.status -eq 'ready' -and $response.protocolVersion -eq 1) { $ready = $true; break } } catch { Start-Sleep -Milliseconds 250 }
        }
        if (-not $ready) { throw 'Restored sidecar did not become ready.' }
    } finally { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    Write-Output 'Updater signature and rollback smoke passed.'
} finally { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue }
