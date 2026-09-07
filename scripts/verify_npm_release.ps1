[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$TargetVersion,
    [Parameter(Mandatory = $true)][string]$PreviousVersion
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root 'artifacts\production\registry'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$env:AGENTHUB_PRODUCTION_EVIDENCE_DIR = Join-Path $root 'artifacts\production'

function Invoke-AgentHub([string]$label, [scriptblock]$action) {
    & $action 2>&1 | Tee-Object -FilePath (Join-Path $out "$label.log")
    if ($LASTEXITCODE -ne 0) { throw "$label failed with exit code $LASTEXITCODE" }
}

Invoke-AgentHub 'npm-view-target' { npm view "@agenthub/cli@$TargetVersion" version }
Invoke-AgentHub 'install-target' { npm install --global "@agenthub/cli@$TargetVersion" }
Invoke-AgentHub 'doctor-target' { agenthub doctor }
Invoke-AgentHub 'version-target' { agenthub --version }
Invoke-AgentHub 'install-previous' { npm install --global "@agenthub/cli@$PreviousVersion" }
Invoke-AgentHub 'version-previous' { agenthub --version }
Invoke-AgentHub 'install-target-again' { npm install --global "@agenthub/cli@$TargetVersion" }
Invoke-AgentHub 'version-target-again' { agenthub --version }
Invoke-AgentHub 'install-previous-rollback' { npm install --global "@agenthub/cli@$PreviousVersion" }
Invoke-AgentHub 'version-rollback' { agenthub --version }

Write-Output "Registry install/upgrade/rollback completed for $TargetVersion -> $PreviousVersion"
