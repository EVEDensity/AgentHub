[CmdletBinding()]
param([int]$Port = 18765, [string]$OutputDirectory = "")

# Compatibility entry point. This checks the browser-preview shell only;
# it does not launch a packaged application or a native WebView2 instance.
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'shell-browser-smoke.ps1') -Port $Port -OutputDirectory $OutputDirectory
$smokeExitCode = $LASTEXITCODE
exit $smokeExitCode
