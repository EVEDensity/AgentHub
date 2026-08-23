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
    Where-Object { $_.Extension.ToLowerInvariant() -in @('.msi', '.exe') -and $_.Name -notlike '*-release.json' } |
    Sort-Object @{ Expression = { if ($_.Extension -ieq '.msi') { 0 } else { 1 } } }, FullName |
    Select-Object -First 1
if ($null -eq $installer) { throw "No MSI or NSIS installer found under $bundleDirectory." }

$root = Join-Path ([IO.Path]::GetTempPath()) ("agenthub-installer-smoke-" + [guid]::NewGuid().ToString('N'))
$installRoot = Join-Path $root "installed"
$logPath = Join-Path $root "install.log"
New-Item -ItemType Directory -Force -Path $root, $installRoot | Out-Null
$installedDirectory = $null
$uninstallCommand = $null
$succeeded = $false

function Invoke-CheckedProcess([string]$FilePath, [string[]]$ArgumentList, [int]$TimeoutSeconds = 180) {
    $stdoutPath = "$logPath.stdout"
    $stderrPath = "$logPath.stderr"
    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -PassThru -Wait:$false -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $process.Kill(); throw "$FilePath timed out after $TimeoutSeconds seconds."
    }
    if ($process.ExitCode -ne 0) { throw "$FilePath failed with exit code $($process.ExitCode). See $logPath, $stdoutPath, and $stderrPath." }
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

function Resolve-ShortcutTarget([string]$Path) {
    $shell = New-Object -ComObject WScript.Shell
    try { return $shell.CreateShortcut($Path).TargetPath } finally { [Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null }
}

try {
    Write-Output "Installing $($installer.FullName)"
    if ($installer.Extension -ieq '.msi') {
        Invoke-CheckedProcess 'msiexec.exe' @('/i', "`"$($installer.FullName)`"", '/qn', '/norestart', '/L*v', "`"$logPath`"", "INSTALLDIR=`"$installRoot`"")
    } else {
        Invoke-CheckedProcess $installer.FullName @('/S', "/D=`"$installRoot`"")
    }

    $entry = Find-UninstallEntry
    if ($null -eq $entry) { throw "AgentHub uninstall registry entry was not created." }
    $installedDirectory = if ($entry.InstallLocation) { $entry.InstallLocation } else { $installRoot }
    $app = Join-Path $installedDirectory 'agenthub-desktop.exe'
    $sidecar = Join-Path $installedDirectory 'agenthub-runtime.exe'
    if (-not (Test-Path -LiteralPath $app -PathType Leaf)) { throw "Installed desktop executable was not found at $app." }
    if (-not (Test-Path -LiteralPath $sidecar -PathType Leaf)) { throw "Installed sidecar was not found beside desktop executable at $sidecar." }

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

    $uninstallCommand = $entry.UninstallString
    if ([string]::IsNullOrWhiteSpace($uninstallCommand)) { throw "Uninstall command is missing from registry entry." }
    if ($uninstallCommand -match '^\s*"([^"]+)"\s*(.*)$') { $uninstaller = $Matches[1]; $uninstallArgs = $Matches[2] } else { $parts = $uninstallCommand.Split(' ', 2); $uninstaller = $parts[0]; $uninstallArgs = if ($parts.Count -gt 1) { $parts[1] } else { '' } }
    if ($uninstaller -match '(?i)msiexec') { $uninstallArgs = ($uninstallArgs -replace '(?i)^/I', '/X') + ' /qn /norestart' }
    elseif ($uninstallArgs -notmatch '(?i)(/S|/quiet)') { $uninstallArgs = "$uninstallArgs /S" }
    Invoke-CheckedProcess $uninstaller @($uninstallArgs)
    Start-Sleep -Seconds 2
    if (Test-Path -LiteralPath $app -PathType Leaf) { throw "Installed application still exists after uninstall: $app." }
    if (Find-UninstallEntry) { throw "AgentHub uninstall registry entry still exists after uninstall." }
    Write-Output "Installer install/startup/shortcut/uninstall smoke passed."
    $succeeded = $true
} finally {
    if ($KeepInstall -or -not $succeeded) {
        Write-Output "Keeping installer smoke directory: $root"
    } else {
        Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    }
}
