[CmdletBinding()]
param(
    [string]$OutputDirectory = "$PSScriptRoot"
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$sitePackages = Join-Path $root '.venv\Lib\site-packages'
if (-not (Test-Path -LiteralPath $sitePackages -PathType Container)) {
    throw "Python dependencies were not found at $sitePackages. Create the project .venv first."
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$buildRoot = Join-Path $root '.tmp\pyinstaller-mission-control'
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
$env:PYTHONPATH = $sitePackages

python -m PyInstaller --noconfirm --clean --onefile `
    --name agenthub-mission-control `
    --distpath $OutputDirectory `
    --workpath (Join-Path $buildRoot 'build') `
    --specpath (Join-Path $buildRoot 'spec') `
    --paths $sitePackages `
    --hidden-import main `
    --exclude-module PyQt5 --exclude-module PySide6 --exclude-module tkinter `
    --exclude-module win32api --exclude-module pywintypes --exclude-module pythoncom `
    --exclude-module IPython --exclude-module pytest --exclude-module torch --exclude-module torchvision --exclude-module torchaudio `
    --exclude-module torch --exclude-module torchvision --exclude-module torchaudio `
    (Join-Path $root 'mission_control_entrypoint.py')
if ($LASTEXITCODE -ne 0) { throw 'Mission Control freeze failed.' }

Write-Output "Mission Control binary: $(Join-Path $OutputDirectory 'agenthub-mission-control.exe')"
