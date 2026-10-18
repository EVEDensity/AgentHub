[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ArtifactPath,
    [Parameter(Mandatory = $true)][ValidateSet('Portable', 'Msi')][string]$Kind,
    [Parameter(Mandatory = $true)][string]$ExpectedManifest,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{40}$')][string]$SourceCommit,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedApplicationSha256,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedSidecarSha256,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$runId = 'package-resources-' + $Kind.ToLowerInvariant() + '-' + [guid]::NewGuid().ToString('N')
$runDirectory = Join-Path ([IO.Path]::GetFullPath($OutputDirectory)) $runId
$extractDirectory = Join-Path ([IO.Path]::GetTempPath()) ('agenthub-resources-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $runDirectory, $extractDirectory | Out-Null
$reportPath = Join-Path $runDirectory 'report.json'
$report = [ordered]@{
    schemaVersion = 1; scope = 'desktop-package-resources'; status = 'running'
    sourceCommit = $SourceCommit; buildMode = 'local-services'; kind = $Kind
    packagedApplication = $true; nativeWebView2 = $false; realServicesVerified = $false
    artifact = $null; resourceReport = $null
}

function Save-Report {
    $temporary = Join-Path $runDirectory 'report.pending.json'
    $report | ConvertTo-Json -Depth 15 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $reportPath -Force
}

Save-Report
try {
    $artifact = Get-Item -LiteralPath $ArtifactPath
    if ($artifact.PSIsContainer -or $artifact.Length -le 0 -or ($artifact.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Artifact must be a nonempty regular file.' }
    $extension = if ($Kind -eq 'Portable') { '.zip' } else { '.msi' }
    if ($artifact.Extension -ine $extension) { throw 'Artifact extension does not match extraction kind.' }
    $artifactDigest = (Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $report.artifact = [ordered]@{ name = $artifact.Name; sizeBytes = $artifact.Length; sha256 = $artifactDigest }
    if ($Kind -eq 'Portable') {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [IO.Compression.ZipFile]::OpenRead($artifact.FullName)
        try {
            $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
            foreach ($entry in $archive.Entries) {
                $name = $entry.FullName.Replace('\', '/')
                if ($name -match '(^/|:|(^|/)\.\.?(/|$))' -or $name.Contains('//')) { throw "Unsafe ZIP path: $name" }
                if (-not $names.Add($name.TrimEnd('/'))) { throw "Duplicate ZIP entry: $name" }
                $unixType = ($entry.ExternalAttributes -shr 16) -band 0xF000
                if ($unixType -eq 0xA000) { throw "ZIP symbolic link is not accepted: $name" }
            }
        } finally { $archive.Dispose() }
        [IO.Compression.ZipFile]::ExtractToDirectory($artifact.FullName, $extractDirectory)
    } else {
        $logPath = Join-Path $runDirectory 'msi-extraction.log'
        # Administrative extraction does not install or launch the application.
        $arguments = @('/a', ('"' + $artifact.FullName + '"'), '/qn', '/norestart', ('TARGETDIR="' + $extractDirectory + '"'), '/L*v', ('"' + $logPath + '"'))
        $process = Start-Process -FilePath 'msiexec.exe' -ArgumentList $arguments -WindowStyle Hidden -PassThru
        if (-not $process.WaitForExit(300000)) { $process.Kill(); throw 'MSI administrative extraction timed out.' }
        if ($process.ExitCode -ne 0) { throw "MSI extraction failed ($($process.ExitCode)); see $logPath." }
    }
    $reparseEntries = @(Get-ChildItem -LiteralPath $extractDirectory -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })
    if ($reparseEntries.Count -ne 0) { throw 'Extracted resources contain a reparse point.' }
    $applications = @(Get-ChildItem -LiteralPath $extractDirectory -Filter 'agenthub-desktop.exe' -Recurse -File -Force)
    if ($applications.Count -ne 1) { throw 'Extracted package must contain exactly one desktop executable.' }
    $applicationRoot = $applications[0].Directory.FullName
    foreach ($item in @(@('agenthub-desktop.exe', $ExpectedApplicationSha256), @('agenthub-runtime.exe', $ExpectedSidecarSha256))) {
        $actual = (Get-FileHash -LiteralPath (Join-Path $applicationRoot $item[0]) -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -cne $item[1]) { throw "Packaged executable differs from this build: $($item[0]); expected SHA-256 $($item[1]), extracted SHA-256 $actual." }
    }
    $resourceReportPath = Join-Path $runDirectory 'resources.json'
    & node (Join-Path $PSScriptRoot 'package-resource-manifest.mjs') verify --root $applicationRoot --expected $ExpectedManifest --source-commit $SourceCommit --output $resourceReportPath
    if ($LASTEXITCODE -ne 0) { throw 'Extracted package resource verification failed.' }
    $report.resourceReport = Get-Content -LiteralPath $resourceReportPath -Raw | ConvertFrom-Json
    if ($report.resourceReport.status -ne 'passed') { throw 'Resource verifier did not produce a passed report.' }
    $digestAfter = (Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($digestAfter -cne $artifactDigest) { throw 'Package changed during extraction verification.' }
} catch {
    $report.status = 'failed'; $report.error = $_.Exception.Message
    throw
} finally {
    try {
        $resolvedExtract = [IO.Path]::GetFullPath($extractDirectory)
        $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
        if (-not $resolvedExtract.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase) -or (Split-Path -Leaf $resolvedExtract) -notlike 'agenthub-resources-*') { throw 'Unexpected extraction cleanup target.' }
        Remove-Item -LiteralPath $resolvedExtract -Recurse -Force
    } catch {
        $report.status = 'failed'; $report.error = $_.Exception.Message
        Save-Report
        throw
    }
    if ($report.status -eq 'running') { $report.status = 'passed' }
    Save-Report
}
Write-Output "Actual $Kind package resources verified: $reportPath"
