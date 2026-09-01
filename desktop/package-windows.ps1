[CmdletBinding()]
param(
    [string]$TargetTriple,
    [switch]$NoInstaller,
    [switch]$Portable,
    [switch]$LocalServices
)

$ErrorActionPreference = "Stop"
$desktopDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$sidecarDirectory = Join-Path $desktopDirectory "runtime-sidecar"
$buildScript = Join-Path $sidecarDirectory "build-windows.ps1"
$preflightScript = Join-Path $sidecarDirectory "packaging-preflight.ps1"
$artifactSmokeScript = Join-Path $desktopDirectory "packaged-artifact-smoke.ps1"
$runtimeSmokeScript = Join-Path $desktopDirectory "packaged-runtime-smoke.ps1"
$installerSmokeScript = Join-Path $desktopDirectory "installer-artifact-smoke.ps1"
$installLifecycleSmokeScript = Join-Path $desktopDirectory "installer-install-smoke.ps1"
$releaseManifestScript = Join-Path $desktopDirectory "release-manifest.ps1"
$localServicesBuildScript = Join-Path $desktopDirectory "local-services\build-windows.ps1"
$updaterConfigTemplate = Join-Path $desktopDirectory "src-tauri\tauri.conf.json"
$manifest = Join-Path $desktopDirectory "src-tauri\Cargo.toml"

if (-not (Test-Path -LiteralPath $buildScript -PathType Leaf)) {
    throw "Sidecar build script was not found at $buildScript."
}
if (-not (Test-Path -LiteralPath $preflightScript -PathType Leaf)) {
    throw "Packaging preflight was not found at $preflightScript."
}
if (-not (Test-Path -LiteralPath $artifactSmokeScript -PathType Leaf)) {
    throw "Packaged artifact smoke was not found at $artifactSmokeScript."
}
if (-not (Test-Path -LiteralPath $runtimeSmokeScript -PathType Leaf)) {
    throw "Packaged runtime smoke was not found at $runtimeSmokeScript."
}
if (-not (Test-Path -LiteralPath $installerSmokeScript -PathType Leaf)) {
    throw "Installer artifact smoke was not found at $installerSmokeScript."
}
if (-not (Test-Path -LiteralPath $installLifecycleSmokeScript -PathType Leaf)) {
    throw "Installer lifecycle smoke was not found at $installLifecycleSmokeScript."
}
if (-not (Test-Path -LiteralPath $releaseManifestScript -PathType Leaf)) {
    throw "Release manifest script was not found at $releaseManifestScript."
}
if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
    throw "Tauri manifest was not found at $manifest."
}
if ($LocalServices) {
    if (-not (Test-Path -LiteralPath $localServicesBuildScript -PathType Leaf)) { throw "Local service build script was not found." }
    $stagedMissionControl = Join-Path $desktopDirectory "src-tauri\local-services\agenthub-mission-control.exe"
    if (Test-Path -LiteralPath $stagedMissionControl -PathType Leaf) {
        Write-Output "Reusing the staged Mission Control binary; skipping the PyInstaller freeze."
        & $localServicesBuildScript -MissionControlBinary $stagedMissionControl
    } else {
        & $localServicesBuildScript
    }
    if ($LASTEXITCODE -ne 0) { throw "Local service staging failed." }
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
$tauriDirectory = Split-Path -Parent $manifest
Push-Location $tauriDirectory
try {
    $buildArguments = @("tauri", "build", "--target", $TargetTriple, "--ci")
    $generatedUpdaterConfig = $null
    # One merged config pass: resource entries and the signed-updater overrides
    # MUST land in the same --config file — Tauri applies only the last
    # --config, so two separate passes would silently drop one of them.
    $resourceEntries = @()
    $readmeFirst = Join-Path $tauriDirectory 'README-first.txt'
    if (Test-Path -LiteralPath $readmeFirst -PathType Leaf) { $resourceEntries += 'README-first.txt' }
    if ($LocalServices) { $resourceEntries += 'local-services/**/*' }
    $updaterEnabled = $env:AGENTHUB_UPDATE_ENABLED -eq '1'
    if ($resourceEntries.Count -gt 0 -or $updaterEnabled) {
        $config = Get-Content -LiteralPath $updaterConfigTemplate -Raw | ConvertFrom-Json
        if ($resourceEntries.Count -gt 0) {
            $config.bundle | Add-Member -NotePropertyName resources -NotePropertyValue $resourceEntries -Force
        }
        if ($updaterEnabled) {
            $requiredUpdaterValues = @($env:AGENTHUB_UPDATE_PUBLIC_KEY, $env:AGENTHUB_UPDATE_ENDPOINT, $env:TAURI_SIGNING_PRIVATE_KEY)
            if ($requiredUpdaterValues | Where-Object { [string]::IsNullOrWhiteSpace($_) }) {
                throw "Signed updater build requires public key, endpoint, and TAURI_SIGNING_PRIVATE_KEY."
            }
            $config.bundle | Add-Member -NotePropertyName createUpdaterArtifacts -NotePropertyValue $true -Force
            $config | Add-Member -NotePropertyName plugins -NotePropertyValue ([pscustomobject]@{}) -Force
            $config.plugins | Add-Member -NotePropertyName updater -NotePropertyValue ([pscustomobject]@{}) -Force
            $config.plugins.updater | Add-Member -NotePropertyName pubkey -NotePropertyValue $null -Force
            $config.plugins.updater | Add-Member -NotePropertyName endpoints -NotePropertyValue @() -Force
            $config.plugins.updater.pubkey = $env:AGENTHUB_UPDATE_PUBLIC_KEY
            $config.plugins.updater.endpoints = @($env:AGENTHUB_UPDATE_ENDPOINT)
            Write-Output 'Signed updater artifact generation enabled.'
        }
        $generatedUpdaterConfig = Join-Path ([IO.Path]::GetTempPath()) ("agenthub-tauri-config-" + [guid]::NewGuid().ToString('N') + '.json')
        $config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $generatedUpdaterConfig -Encoding utf8
        $buildArguments += @('--config', $generatedUpdaterConfig)
    }
    if ($NoInstaller -or $Portable) {
        $buildArguments += "--no-bundle"
        if ($Portable) {
            Write-Output "Installer generation disabled; building a portable desktop package."
        } else {
            Write-Output "Installer generation disabled; validating the release application only."
        }
    }
    & cargo +1.88.0 @buildArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri bundle build failed."
    }
} finally {
    Pop-Location
    if ($generatedUpdaterConfig -and (Test-Path -LiteralPath $generatedUpdaterConfig)) {
        Remove-Item -LiteralPath $generatedUpdaterConfig -Force -ErrorAction SilentlyContinue
    }
}

Write-Output "Checking packaged artifacts."
& $artifactSmokeScript -TargetTriple $TargetTriple
if ($LASTEXITCODE -ne 0) {
    throw "Packaged artifact smoke failed."
}

Write-Output "Checking packaged runtime readiness."
& $runtimeSmokeScript -TargetTriple $TargetTriple
if ($LASTEXITCODE -ne 0) {
    throw "Packaged runtime smoke failed."
}

if ($NoInstaller -or $Portable) {
    Write-Output "Windows desktop application build completed for $TargetTriple."
} else {
    Write-Output "Windows desktop bundle completed for $TargetTriple."
}

$releaseDirectory = Join-Path $desktopDirectory "src-tauri\target\$TargetTriple\release"
$installerDirectory = Join-Path $releaseDirectory "bundle"
if ($Portable) {
    New-Item -ItemType Directory -Force -Path $installerDirectory | Out-Null
    $portableArchive = Join-Path $installerDirectory "AgentHub-$TargetTriple-portable.zip"
    $application = Join-Path $releaseDirectory "agenthub-desktop.exe"
    $packagedSidecar = Join-Path $releaseDirectory "agenthub-runtime.exe"
    $portableEntries = @($application, $packagedSidecar)
    if ($LocalServices) {
        $stagedServices = Join-Path $tauriDirectory 'local-services'
        $releaseServices = Join-Path $releaseDirectory 'local-services'
        if (-not (Test-Path -LiteralPath $stagedServices -PathType Container)) { throw "Local service resources were not staged." }
        if (Test-Path -LiteralPath $releaseServices) { Remove-Item -LiteralPath $releaseServices -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $releaseServices | Out-Null
        Get-ChildItem -LiteralPath $stagedServices -Force | Copy-Item -Destination $releaseServices -Recurse -Force
        $portableEntries += $releaseServices
    }
    $portableStage = Join-Path ([IO.Path]::GetTempPath()) ("agenthub-portable-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $portableStage | Out-Null
    try {
        Copy-Item -LiteralPath $application -Destination (Join-Path $portableStage 'agenthub-desktop.exe') -Force
        Copy-Item -LiteralPath $packagedSidecar -Destination (Join-Path $portableStage 'agenthub-runtime.exe') -Force
        if ($LocalServices) { Copy-Item -LiteralPath $releaseServices -Destination (Join-Path $portableStage 'local-services') -Recurse -Force }
        # Delivery plan item 5: AgentHub.exe is the only user action in the
        # portable package; bundled service binaries are not user commands.
        Set-Content -LiteralPath (Join-Path $portableStage 'START-HERE.txt') -Encoding utf8 -Value @(
            'AgentHub portable package.',
            'Run agenthub-desktop.exe - it is the only entry point and starts all local services itself.',
            'Do not run node.exe, server.js, or any binary under local-services directly.',
            'Local data lives under %LOCALAPPDATA%\AgentHub.'
        )
        $portableFiles = @(Get-ChildItem -LiteralPath $portableStage -Force)
        if ($portableFiles.Count -eq 0) { throw "Portable staging directory is empty: $portableStage" }
        Compress-Archive -LiteralPath $portableFiles.FullName -DestinationPath $portableArchive -Force
        $archiveEntries = @(tar.exe -tf $portableArchive)
        $longestEntry = ($archiveEntries | Sort-Object Length -Descending | Select-Object -First 1)
        if ($longestEntry -and $longestEntry.Length -gt 180) {
            throw "Portable archive contains a path of $($longestEntry.Length) characters. Extract it to a short directory such as C:\AgentHub."
        }
    } finally {
        if (Test-Path -LiteralPath $portableStage) { Remove-Item -LiteralPath $portableStage -Recurse -Force -ErrorAction SilentlyContinue }
    }
    Write-Output "Portable package: $portableArchive"
}
$releaseManifestPath = Join-Path $installerDirectory "AgentHub-$TargetTriple-release.json"
# Public release path: code-sign every distributable artifact with the
# CI-injected certificate (ADR-0099). The release policy gates on secret
# presence; this is where the certificate is actually applied.
if ($env:AGENTHUB_UPDATE_ENABLED -eq '1') {
    $signingScript = Join-Path $desktopDirectory "sign-windows-artifacts.ps1"
    Write-Output "Signing distributable artifacts."
    & $signingScript -Path $installerDirectory, (Join-Path $releaseDirectory 'agenthub-desktop.exe'), (Join-Path $releaseDirectory 'agenthub-runtime.exe')
    if ($LASTEXITCODE -ne 0) {
        throw "Artifact signing failed."
    }
}
& $releaseManifestScript -TargetTriple $TargetTriple -OutputPath $releaseManifestPath
if ($LASTEXITCODE -ne 0) {
    throw "Release manifest generation failed."
}
Write-Output "Release application: $(Join-Path $releaseDirectory 'agenthub-desktop.exe')"
Write-Output "Packaged sidecar: $(Join-Path $releaseDirectory 'agenthub-runtime.exe')"
if (Test-Path -LiteralPath $installerDirectory -PathType Container) {
    $installers = @(Get-ChildItem -LiteralPath $installerDirectory -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -ieq '.msi' -or ($_.Extension -ieq '.exe' -and $_.Name -like '*-setup.exe') })
    if ($installers.Count -gt 0) {
        foreach ($installer in $installers) {
            Write-Output "Installer: $($installer.FullName)"
        }
    } else {
        Write-Output "Installer directory: $installerDirectory (no MSI/NSIS artifact found)"
    }
} else {
    Write-Output "Installer directory: $installerDirectory (not created)"
}
if (-not $NoInstaller -and -not $Portable) {
    & $installerSmokeScript -TargetTriple $TargetTriple
    if ($LASTEXITCODE -ne 0) {
        throw "Installer artifact smoke failed."
    }
    if ($env:AGENTHUB_INSTALLER_LIFECYCLE_SMOKE -eq '1') {
        & $installLifecycleSmokeScript -TargetTriple $TargetTriple
        if ($LASTEXITCODE -ne 0) { throw "Installer lifecycle smoke failed." }
    } else {
        Write-Output "Installer lifecycle smoke skipped; set AGENTHUB_INSTALLER_LIFECYCLE_SMOKE=1 on an isolated Windows runner to install/uninstall."
    }
}
