[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Directory,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{40}$')][string]$SourceCommit
)
$ErrorActionPreference = 'Stop'
$expectedPath = Join-Path $Directory 'expected-resources.json'
$inventory = Get-Content -LiteralPath $expectedPath -Raw | ConvertFrom-Json
if ($inventory.sourceCommit -cne $SourceCommit -or $inventory.buildMode -cne 'local-services' -or $inventory.schemaVersion -ne 1 -or $inventory.scope -cne 'desktop-package-resources' -or @($inventory.files).Count -eq 0) { throw 'Missing current external resource inventory.' }
$inventoryDigest = (Get-FileHash -LiteralPath $expectedPath -Algorithm SHA256).Hash.ToLowerInvariant()
$inventoryBytes = ($inventory.files | Measure-Object -Property sizeBytes -Sum).Sum
$reports = @(Get-ChildItem -LiteralPath $Directory -Directory -Filter 'package-resources-*' | ForEach-Object {
    Get-Content -LiteralPath (Join-Path $_.FullName 'report.json') -Raw | ConvertFrom-Json
})
if ($reports.Count -ne 2) { throw 'Expected exactly two reports from this package build.' }
foreach ($kind in @('Portable', 'Msi')) {
    $selected = @($reports | Where-Object { $_.kind -ceq $kind })
    if ($selected.Count -ne 1) { throw "Expected one actual $kind extraction report." }
    $report = $selected[0]
    if ($report.schemaVersion -ne 1 -or $report.status -cne 'passed' -or $report.scope -cne 'desktop-package-resources' -or $report.sourceCommit -cne $SourceCommit -or $report.buildMode -cne 'local-services') { throw 'Wrong or incomplete package report.' }
    if ($report.packagedApplication -cne $true -or $report.nativeWebView2 -cne $false -or $report.realServicesVerified -cne $false) { throw 'Incorrect verification scope.' }
    foreach ($flag in @($report.packagedApplication, $report.nativeWebView2, $report.realServicesVerified)) {
        if ($flag -isnot [bool]) { throw 'Package verification scope flags must be booleans.' }
    }
    if ($report.artifact.sha256 -cnotmatch '^[a-f0-9]{64}$' -or $report.artifact.sizeBytes -le 0) { throw 'Missing actual package digest.' }
    $resources = $report.resourceReport
    if ($resources.schemaVersion -ne 1 -or $resources.status -cne 'passed' -or $resources.sourceCommit -cne $SourceCommit -or $resources.scope -cne 'desktop-package-resources' -or $resources.buildMode -cne 'local-services' -or $resources.evidence -cne 'directory-resource-files-only') { throw 'Missing successful same-source resource verification.' }
    if ($resources.packagedApplication -cne $false -or $resources.nativeWebView2 -cne $false -or $resources.realServicesVerified -cne $false) { throw 'Incorrect resource-only verification scope.' }
    foreach ($flag in @($resources.packagedApplication, $resources.nativeWebView2, $resources.realServicesVerified)) {
        if ($flag -isnot [bool]) { throw 'Resource verification scope flags must be booleans.' }
    }
    foreach ($count in @($resources.filesVerified, $resources.bytesVerified)) {
        if ($null -eq $count -or $count -is [bool] -or $count -is [string] -or $count -le 0 -or $count -gt 9007199254740991 -or [Math]::Floor($count) -ne $count) { throw 'Resource verification must contain positive safe integer counts.' }
    }
    if ($resources.expectedManifestSha256 -cnotmatch '^[a-f0-9]{64}$') { throw 'Missing external inventory digest.' }
    if ($resources.expectedManifestSha256 -cne $inventoryDigest -or $resources.filesVerified -ne @($inventory.files).Count -or $resources.bytesVerified -ne $inventoryBytes) { throw 'Resource report does not match the retained external inventory.' }
    if ($kind -eq 'Portable') { $expectedDigest = $resources.expectedManifestSha256 }
    elseif ($resources.expectedManifestSha256 -cne $expectedDigest) { throw 'Packages were not checked against the same external inventory.' }
}
Write-Output "Both actual Portable and MSI resource reports passed for $SourceCommit. Native WebView2 and live services remain separate."
