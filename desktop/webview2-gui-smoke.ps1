[CmdletBinding()]
param([int]$Port = 18765, [string]$OutputDirectory = "")

$ErrorActionPreference = "Stop"
# playwright-cli emits UTF-8; without this the runner's default console
# encoding mangles the captured CJK snapshot text and every -notmatch
# against a Chinese literal is trivially true.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
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
try {
  # All assertions are ASCII DOM-state checks executed inside the page via
  # eval: captured native stdout is decoded with the runner's legacy
  # codepage, so matching Chinese snapshot text there is unreliable. DOM
  # clicks drive the same listeners the real UI uses.
  Invoke-Playwright @('open', "http://127.0.0.1:$Port/index.html") | Out-Null
  $homeState = Invoke-Playwright @('eval', "() => JSON.stringify({taskInput: !!document.getElementById('task-input'), serviceList: !!document.getElementById('service-list'), feedback: !!document.getElementById('feedback')})")
  if ($homeState -notmatch '"taskInput":true' -or $homeState -notmatch '"serviceList":true' -or $homeState -notmatch '"feedback":true') { throw "Initial desktop shell did not render: $homeState" }
  Invoke-Playwright @('eval', "document.getElementById('settings').click()") | Out-Null
  $settings = Invoke-Playwright @('eval', "() => JSON.stringify({settingsVisible: !document.getElementById('settings-view').hidden, generalPanel: !document.querySelector('[data-settings-panel=general]').hidden, generalActive: document.querySelector('[data-settings-section=general]').classList.contains('active')})")
  if ($settings -notmatch '"settingsVisible":true' -or $settings -notmatch '"generalPanel":true') { throw "Settings view did not render: $settings" }
  Invoke-Playwright @('eval', "document.querySelector('[data-settings-section=monitoring]').click()") | Out-Null
  $monitor = Invoke-Playwright @('eval', "() => JSON.stringify({monitorVisible: !document.querySelector('[data-settings-panel=monitoring]').hidden, stackCard: !!document.getElementById('monitor-stack'), stackState: !!document.getElementById('monitor-stack-state')})")
  if ($monitor -notmatch '"monitorVisible":true' -or $monitor -notmatch '"stackCard":true') { throw "Monitor panel did not render: $monitor" }
  $succeeded = $true
  Write-Output "WebView2 GUI smoke passed."
} finally {
  try { Invoke-Playwright @('close') | Out-Null } catch { }
  if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
  if (-not $succeeded) { Write-Output "Playwright diagnostics retained at $cliOutput" }
}
