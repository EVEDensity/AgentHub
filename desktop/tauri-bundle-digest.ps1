[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][ValidateSet('msi', 'nsis')][string]$BundleType
)

$ErrorActionPreference = 'Stop'
$item = Get-Item -LiteralPath $Path -Force
if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Tauri bundle digest input must be a regular file.'
}

# Tauri 2.11.4 patches this exact fixed-width marker in a copy for each
# installer, then restores the unpatched build output. Mirror that byte-only
# transformation so MSI verification has the hash of the EXE actually bundled.
$marker = [Text.Encoding]::ASCII.GetBytes('__TAURI_BUNDLE_TYPE_VAR_UNK')
$replacement = switch ($BundleType) {
    'msi' { [Text.Encoding]::ASCII.GetBytes('__TAURI_BUNDLE_TYPE_VAR_MSI') }
    'nsis' { [Text.Encoding]::ASCII.GetBytes('__TAURI_BUNDLE_TYPE_VAR_NSS') }
}
if ($marker.Length -ne $replacement.Length) { throw 'Tauri bundle marker replacement must preserve executable length.' }
$bytes = [IO.File]::ReadAllBytes($item.FullName)
$markerIndex = -1
for ($offset = 0; $offset -le $bytes.Length - $marker.Length; $offset++) {
    $matches = $true
    for ($index = 0; $index -lt $marker.Length; $index++) {
        if ($bytes[$offset + $index] -ne $marker[$index]) { $matches = $false; break }
    }
    if ($matches) {
        if ($markerIndex -ne -1) { throw 'Tauri bundle marker appears more than once in the executable.' }
        $markerIndex = $offset
    }
}
if ($markerIndex -eq -1) { throw 'Tauri bundle marker was not found in the executable.' }
[Array]::Copy($replacement, 0, $bytes, $markerIndex, $replacement.Length)
$sha = [Security.Cryptography.SHA256]::Create()
try { [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant() }
finally { $sha.Dispose() }
