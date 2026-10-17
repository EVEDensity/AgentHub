[CmdletBinding()]
param([int]$Port = 18765, [string]$OutputDirectory = "")

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$nodeCommand = Get-Command node -ErrorAction Stop
$runnerArguments = @((Join-Path $PSScriptRoot 'shell-browser-smoke.mjs'), '--port', [string]$Port)
if (-not [string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $runnerArguments += @('--output-directory', $OutputDirectory)
}
& $nodeCommand.Source @runnerArguments
$smokeExitCode = $LASTEXITCODE
exit $smokeExitCode
