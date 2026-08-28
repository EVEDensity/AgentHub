[CmdletBinding()]
param([int]$Port = 18765, [string]$OutputDirectory = "")

$ErrorActionPreference = "Stop"
$desktopDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$uiDirectory = Join-Path $desktopDirectory "ui"
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) { $OutputDirectory = Join-Path (Split-Path -Parent $desktopDirectory) "output\playwright" }
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
if (-not (Get-Command npx -ErrorAction SilentlyContinue)) { throw "npx is required for the Playwright CLI smoke." }
$server = Start-Process -FilePath (Get-Command python -ErrorAction Stop).Source -ArgumentList @('-m','http.server',$Port,'--bind','127.0.0.1') -WorkingDirectory $uiDirectory -PassThru -WindowStyle Hidden
$cliOutput = Join-Path $OutputDirectory "webview2-gui-cli.log"
$succeeded = $false
function Invoke-Playwright([string[]]$Arguments) {
  $output = & npx --yes --package @playwright/cli playwright-cli @Arguments 2>&1
  Add-Content -LiteralPath $cliOutput -Value ($output -join [Environment]::NewLine)
  if ($LASTEXITCODE -ne 0) { throw "Playwright CLI failed: $($Arguments -join ' ')" }
  return ($output -join [Environment]::NewLine)
}
function Find-Ref([string]$Snapshot, [string]$Label) {
  $match = [regex]::Match($Snapshot, "(?m).*" + [regex]::Escape($Label) + ".*\[ref=(e\d+)\]")
  if (-not $match.Success) { throw "Unable to find Playwright ref for '$Label'." }
  return $match.Groups[1].Value
}
try {
  Invoke-Playwright @('open', "http://127.0.0.1:$Port/index.html") | Out-Null
  $snapshot = Invoke-Playwright @('snapshot')
  if ($snapshot -notmatch '今天要完成什么' -or $snapshot -notmatch '本地桌面') {
    Add-Content -LiteralPath $cliOutput -Value ($snapshot -join "`n")
    throw ('Initial desktop shell content was not rendered. snapshot head: ' + $snapshot.Substring(0, [Math]::Min(800, $snapshot.Length)))
  }
  # playwright-cli has no screenshot command; the snapshot text assertions
  # above and below are the actual verification surface.
  $settingsRef = Find-Ref $snapshot '设置'
  Invoke-Playwright @('click', $settingsRef) | Out-Null
  $settingsSnapshot = Invoke-Playwright @('snapshot')
  if ($settingsSnapshot -notmatch '常规' -or $settingsSnapshot -notmatch '本地数据目录') {
    Add-Content -LiteralPath $cliOutput -Value ($settingsSnapshot -join "`n")
    throw ('Settings view did not render. snapshot head: ' + $settingsSnapshot.Substring(0, [Math]::Min(800, $settingsSnapshot.Length)))
  }
  $monitorRef = Find-Ref $settingsSnapshot '监视器'
  Invoke-Playwright @('click', $monitorRef) | Out-Null
  $monitorSnapshot = Invoke-Playwright @('snapshot')
  if ($monitorSnapshot -notmatch '服务栈版本') {
    Add-Content -LiteralPath $cliOutput -Value ($monitorSnapshot -join "`n")
    throw ('Monitor panel did not render. snapshot head: ' + $monitorSnapshot.Substring(0, [Math]::Min(800, $monitorSnapshot.Length)))
  }
  $succeeded = $true
  Write-Output "WebView2 GUI smoke passed."
} finally {
  try { Invoke-Playwright @('close') | Out-Null } catch { }
  if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
  if (-not $succeeded) { Write-Output "Playwright diagnostics retained at $cliOutput" }
}
