[CmdletBinding()]
param([switch]$PublicRelease)

$ErrorActionPreference = "Stop"
if (-not $PublicRelease) {
    Write-Output "Internal release policy passed; unsigned artifacts are allowed for validation only."
    exit 0
}
$required = @(
    'AGENTHUB_WINDOWS_SIGNING_CERT_BASE64',
    'AGENTHUB_WINDOWS_SIGNING_PASSWORD',
    'AGENTHUB_UPDATE_PRIVATE_KEY'
)
$missing = @($required | Where-Object { [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_)) })
if ($missing.Count -gt 0) {
    throw "Public release is blocked; signing/updater secrets are missing: $($missing -join ', ')."
}
Write-Output "Public release signing prerequisites are present."
