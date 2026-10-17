[CmdletBinding()]
param(
    [string]$OutputDirectory = "$PSScriptRoot\..\src-tauri\local-services",
    [string]$PythonExecutable = 'python'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath "$PSScriptRoot\..\..").Path
$comparison = [StringComparison]::OrdinalIgnoreCase
$separator = [IO.Path]::DirectorySeparatorChar
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) { throw 'OutputDirectory must name a dedicated staging directory.' }
$path = if ([IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory } else { Join-Path (Get-Location).Path $OutputDirectory }
$output = [IO.Path]::GetFullPath($path).TrimEnd($separator)
$repository = $root.TrimEnd($separator)
$volume = [IO.Path]::GetPathRoot($output).TrimEnd($separator)
if ($output -eq $volume -or $repository.Equals($output, $comparison) -or $repository.StartsWith($output + $separator, $comparison)) {
    throw 'OutputDirectory cannot be a filesystem root, repository root or repository ancestor.'
}
$default = [IO.Path]::GetFullPath((Join-Path $root 'desktop\src-tauri\local-services')).TrimEnd($separator)
$temporary = [IO.Path]::GetFullPath((Join-Path $root '.tmp')).TrimEnd($separator)
if ($output.StartsWith($repository + $separator, $comparison) -and
    -not $output.Equals($default, $comparison) -and -not $output.StartsWith($default + $separator, $comparison) -and
    -not $output.StartsWith($temporary + $separator, $comparison)) {
    throw 'Repository output must be inside desktop/src-tauri/local-services or a dedicated .tmp subdirectory.'
}
function Assert-PlainPath([string]$Value) {
    $current = [IO.Path]::GetFullPath($Value)
    while ($current) {
        $item = $null
        try { $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop }
        catch { if ($_.CategoryInfo.Category -ne [Management.Automation.ErrorCategory]::ObjectNotFound) { throw } }
        if ($null -ne $item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Build paths cannot contain a reparse point: $current" }
        $parent = [IO.Directory]::GetParent($current)
        if ($null -eq $parent) { break }
        $current = $parent.FullName
    }
}
Assert-PlainPath $output
$commit = & git -C $root rev-parse --verify HEAD
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$') { throw 'A full source HEAD is required to freeze Mission Control.' }
$changes = & git -C $root status --porcelain --untracked-files=normal
if ($LASTEXITCODE -ne 0 -or $changes) { throw 'Mission Control freezes require a clean committed source checkout.' }
$pythonCommand = (Get-Command $PythonExecutable -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$previousPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
try {
    # The selected interpreter supplies its own environment's site-packages.
    # Only this checkout is injected; no unrelated .venv or inherited import path.
    $env:PYTHONPATH = $root
    & $pythonCommand -c 'import PyInstaller'
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller is required in the selected Python environment.' }
    [IO.Directory]::CreateDirectory($output) | Out-Null
    $binary = Join-Path $output 'agenthub-mission-control.exe'
    Assert-PlainPath $binary
    if (Test-Path -LiteralPath $binary) { Remove-Item -LiteralPath $binary -Force }
    $buildRoot = Join-Path $root ('.tmp\pyinstaller-mission-control-' + [guid]::NewGuid().ToString('N'))
    Assert-PlainPath $buildRoot
    [IO.Directory]::CreateDirectory($buildRoot) | Out-Null
    $freezeArguments = @(
        '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
        '--name', 'agenthub-mission-control', '--distpath', $output,
        '--workpath', (Join-Path $buildRoot 'build'), '--specpath', (Join-Path $buildRoot 'spec'),
        '--paths', $root, '--hidden-import', 'main',
        '--exclude-module', 'PyQt5', '--exclude-module', 'PySide6', '--exclude-module', 'tkinter',
        '--exclude-module', 'win32api', '--exclude-module', 'pywintypes', '--exclude-module', 'pythoncom',
        '--exclude-module', 'IPython', '--exclude-module', 'pytest', '--exclude-module', 'torch',
        '--exclude-module', 'torchvision', '--exclude-module', 'torchaudio',
        (Join-Path $root 'mission_control_entrypoint.py')
    )
    Push-Location $root
    try {
        & $pythonCommand @freezeArguments
        if ($LASTEXITCODE -ne 0) { throw 'Mission Control freeze failed.' }
    } finally { Pop-Location }
    if (-not (Test-Path -LiteralPath $binary -PathType Leaf) -or (Get-Item -LiteralPath $binary).Length -le 0) { throw 'Mission Control freeze did not produce a nonempty executable.' }
    Write-Output "Mission Control binary rebuilt from $($commit.Trim()): $binary"
} finally {
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $previousPythonPath, 'Process')
}
