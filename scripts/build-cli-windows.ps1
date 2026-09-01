# Freeze the AgentHub developer CLI into a single onefile Windows binary
# (north-star M3 / I-2: `npm i -g @agenthub/cli` distribution).
#
# Usage:
#   scripts\build-cli-windows.ps1 [-OutputDirectory <dir>]   # default: dist
#
# Prerequisites: the project .venv with requirements.txt + pyinstaller
# installed (CI creates it; locally: python -m venv .venv; ...).
# The frozen binary boots its mission-control subprocess by re-invoking
# itself with the hidden `_serve` subcommand, so `main` (mission
# control) must be collected as a hidden import — same contract as
# desktop\local-services\build-mission-control.ps1.

[CmdletBinding()]
param(
    [string]$OutputDirectory = "$PSScriptRoot\..\dist"
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Python venv not found at $venvPython. Create the project .venv first (see CI: python -m venv .venv; pip install -r requirements.txt pyinstaller)."
}

$OutputDirectory = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory
} else {
    Join-Path $root $OutputDirectory
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$buildRoot = Join-Path $root '.tmp\pyinstaller-cli'
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null

& $venvPython -m PyInstaller --noconfirm --clean --onefile `
    --name agenthub `
    --distpath $OutputDirectory `
    --workpath (Join-Path $buildRoot 'build') `
    --specpath (Join-Path $buildRoot 'spec') `
    --hidden-import main `
    --exclude-module PyQt5 --exclude-module PySide6 --exclude-module tkinter `
    --exclude-module win32api --exclude-module pywintypes --exclude-module pythoncom `
    --exclude-module IPython --exclude-module pytest --exclude-module torch --exclude-module torchvision --exclude-module torchaudio `
    (Join-Path $root 'cli_entrypoint.py')
if ($LASTEXITCODE -ne 0) { throw 'CLI freeze failed.' }

$binary = Join-Path $OutputDirectory 'agenthub.exe'
if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) {
    throw "freeze reported success but $binary is missing."
}

# Smoke: the binary must run without any Python installed on the host.
& $binary --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'frozen CLI --help smoke failed.' }

Write-Output "CLI binary: $binary"
