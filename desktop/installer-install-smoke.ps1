[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TargetTriple,
    [string]$ArtifactRoot,
    [switch]$KeepInstall
)

$ErrorActionPreference = "Stop"
$desktopDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($ArtifactRoot)) {
    $ArtifactRoot = Join-Path $desktopDirectory "src-tauri\target\$TargetTriple\release\bundle"
}
$bundleDirectory = (Resolve-Path -LiteralPath $ArtifactRoot).Path
$installer = Get-ChildItem -LiteralPath $bundleDirectory -Recurse -File |
    Where-Object { $_.Extension -ieq '.msi' -or ($_.Extension -ieq '.exe' -and $_.Name -like '*-setup.exe') } |
    Sort-Object @{ Expression = { if ($_.Extension -ieq '.msi') { 0 } else { 1 } } }, FullName |
    Select-Object -First 1
if ($null -eq $installer) { throw "No MSI or NSIS installer found under $bundleDirectory." }

if ($installer.Extension -ieq '.msi') {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "MSI lifecycle smoke requires an elevated PowerShell window because the generated MSI installs for all users. Start PowerShell with 'Run as administrator' and rerun this command."
    }
}

$root = Join-Path ([IO.Path]::GetTempPath()) ("agenthub-installer-smoke-" + [guid]::NewGuid().ToString('N'))
$installRoot = Join-Path $root "installed"
$logPath = Join-Path $root "install.log"
New-Item -ItemType Directory -Force -Path $root, $installRoot | Out-Null
$installedDirectory = $null
$uninstallCommand = $null
$succeeded = $false
$dataDirectory = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'AgentHub'
$dataMarker = Join-Path $dataDirectory 'installer-smoke-marker.txt'
$hadDataMarker = Test-Path -LiteralPath $dataMarker -PathType Leaf
$previousDataMarker = if ($hadDataMarker) { Get-Content -LiteralPath $dataMarker -Raw } else { $null }

function Invoke-CheckedProcess([string]$FilePath, [string[]]$ArgumentList, [int]$TimeoutSeconds = 180) {
    $stdoutPath = "$logPath.stdout"
    $stderrPath = "$logPath.stderr"
    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -PassThru -Wait:$false -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $process.Kill(); throw "$FilePath timed out after $TimeoutSeconds seconds."
    }
    if ($process.ExitCode -ne 0) { throw "$FilePath failed with exit code $($process.ExitCode). See $logPath, $stdoutPath, and $stderrPath." }
}

function Install-AgentHub {
    if ($installer.Extension -ieq '.msi') {
        Invoke-CheckedProcess 'msiexec.exe' @('/i', "`"$($installer.FullName)`"", '/qn', '/norestart', '/L*v', "`"$logPath`"", "INSTALLDIR=`"$installRoot`"")
    } else {
        Invoke-CheckedProcess $installer.FullName @('/S', "/D=`"$installRoot`"")
    }
}

function Invoke-AgentHubUninstall([object]$Entry) {
    $command = $Entry.UninstallString
    if ([string]::IsNullOrWhiteSpace($command)) { throw "Uninstall command is missing from registry entry." }
    if ($command -match '^\s*"([^"]+)"\s*(.*)$') { $uninstaller = $Matches[1]; $arguments = $Matches[2] } else { $parts = $command.Split(' ', 2); $uninstaller = $parts[0]; $arguments = if ($parts.Count -gt 1) { $parts[1] } else { '' } }
    if ($uninstaller -match '(?i)msiexec') { $arguments = ($arguments -replace '(?i)^/I', '/X') + " /qn /norestart /L*v `"$logPath.uninstall.log`"" }
    elseif ($arguments -notmatch '(?i)(/S|/quiet)') { $arguments = "$arguments /S" }
    Invoke-CheckedProcess $uninstaller @($arguments)
}

function Find-UninstallEntry {
    $paths = @(
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    Get-ItemProperty -Path $paths -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like 'AgentHub*' } |
        Select-Object -First 1
}

function Find-UninstallEntries {
    $paths = @(
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    @(Get-ItemProperty -Path $paths -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like 'AgentHub*' })
}

function Remove-RemainingUninstallEntries {
    for ($pass = 0; $pass -lt 3; $pass++) {
        $entries = Find-UninstallEntries
        if ($entries.Count -eq 0) { return }
        foreach ($entry in $entries) { Invoke-AgentHubUninstall $entry }
        Start-Sleep -Seconds 1
    }
}

function Wait-ForUninstallRemoval {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $entry = Find-UninstallEntry
        if ($null -eq $entry) { return }
        Start-Sleep -Seconds 1
    }
    $entry = Find-UninstallEntry
    if ($null -ne $entry) {
        throw "AgentHub uninstall registry entry still exists after 60 seconds. InstallLocation='$($entry.InstallLocation)'; UninstallString='$($entry.UninstallString)'. See $logPath.uninstall.log."
    }
}

function Resolve-ShortcutTarget([string]$Path) {
    $shell = New-Object -ComObject WScript.Shell
    try { return $shell.CreateShortcut($Path).TargetPath } finally { [Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null }
}

try {
    Write-Output "Installing $($installer.FullName)"
    Install-AgentHub

    $entry = Find-UninstallEntry
    if ($null -eq $entry) { throw "AgentHub uninstall registry entry was not created." }
    $installedDirectory = if ($entry.InstallLocation) { ([string]$entry.InstallLocation).Trim('"') } else { $installRoot }
    $app = Join-Path $installedDirectory 'agenthub-desktop.exe'
    $sidecar = Join-Path $installedDirectory 'agenthub-runtime.exe'
    if (-not (Test-Path -LiteralPath $app -PathType Leaf)) { throw "Installed desktop executable was not found at $app." }
    if (-not (Test-Path -LiteralPath $sidecar -PathType Leaf)) { throw "Installed sidecar was not found beside desktop executable at $sidecar." }
    New-Item -ItemType Directory -Force -Path $dataDirectory | Out-Null
    Set-Content -LiteralPath $dataMarker -Value 'preserve-me' -Encoding UTF8

    $shortcutRoots = @([Environment]::GetFolderPath('CommonStartMenu'), [Environment]::GetFolderPath('StartMenu'), [Environment]::GetFolderPath('Desktop')) | Where-Object { $_ }
    $shortcuts = foreach ($shortcutRoot in ($shortcutRoots | Select-Object -Unique)) {
        if (Test-Path -LiteralPath $shortcutRoot) { Get-ChildItem -LiteralPath $shortcutRoot -Filter '*.lnk' -Recurse -File -ErrorAction SilentlyContinue }
    }
    $matchingShortcut = $shortcuts | Where-Object { (Resolve-ShortcutTarget $_.FullName) -eq $app } | Select-Object -First 1
    if ($null -eq $matchingShortcut) { throw "No Start Menu or Desktop shortcut targets $app." }

    $gui = Start-Process -FilePath $app -PassThru
    Start-Sleep -Seconds 8
    $gui.Refresh()
    if ($gui.HasExited) { throw "Installed desktop exited during startup with code $($gui.ExitCode). WebView2 or native startup may be unavailable." }
    Stop-Process -Id $gui.Id -Force -ErrorAction SilentlyContinue

    Invoke-AgentHubUninstall $entry
    Remove-RemainingUninstallEntries
    Wait-ForUninstallRemoval
    if (Test-Path -LiteralPath $app -PathType Leaf) { throw "Installed application still exists after uninstall: $app." }
    if (-not (Test-Path -LiteralPath $dataMarker -PathType Leaf)) { throw "User data marker was removed during uninstall: $dataMarker." }
    if ((Get-Content -LiteralPath $dataMarker -Raw).Trim() -ne 'preserve-me') { throw "User data marker changed during uninstall: $dataMarker." }

    Write-Output 'Reinstalling to verify user data survives an install cycle.'
    Install-AgentHub
    $reinstalledEntry = Find-UninstallEntry
    if ($null -eq $reinstalledEntry) { throw 'AgentHub uninstall registry entry was not recreated after reinstall.' }
    $reinstalledDirectory = if ($reinstalledEntry.InstallLocation) { ([string]$reinstalledEntry.InstallLocation).Trim('"') } else { $installRoot }
    $reinstalledApp = Join-Path $reinstalledDirectory 'agenthub-desktop.exe'
    if (-not (Test-Path -LiteralPath $reinstalledApp -PathType Leaf)) { throw "Reinstalled desktop executable was not found at $reinstalledApp." }
    if ((Get-Content -LiteralPath $dataMarker -Raw).Trim() -ne 'preserve-me') { throw 'User data was not preserved across reinstall.' }
    Invoke-AgentHubUninstall $reinstalledEntry
    Remove-RemainingUninstallEntries
    Wait-ForUninstallRemoval
    Write-Output "Installer install/startup/shortcut/uninstall smoke passed."
    $succeeded = $true
} finally {
    if ($hadDataMarker) {
        Set-Content -LiteralPath $dataMarker -Value $previousDataMarker -NoNewline -Encoding UTF8
    } elseif (Test-Path -LiteralPath $dataMarker) {
        Remove-Item -LiteralPath $dataMarker -Force -ErrorAction SilentlyContinue
    }
    if ($KeepInstall -or -not $succeeded) {
        Write-Output "Keeping installer smoke directory: $root"
    } else {
        Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    }
}
