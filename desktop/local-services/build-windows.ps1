[CmdletBinding()]
param(
    [string]$OutputDirectory = "$PSScriptRoot\..\src-tauri\local-services",
    [string]$PythonExecutable = 'python'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath "$PSScriptRoot\..\..").Path
$comparison = [StringComparison]::OrdinalIgnoreCase
$separator = [IO.Path]::DirectorySeparatorChar

function Get-VerifiedSourceCommit {
    $commit = & git -C $root rev-parse --verify HEAD
    if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$') { throw 'A full source HEAD is required to build the desktop stack.' }
    $changes = & git -C $root status --porcelain --untracked-files=normal
    if ($LASTEXITCODE -ne 0 -or $changes) { throw 'Desktop stack builds require a clean committed source checkout.' }
    return $commit.Trim()
}

function Assert-PlainPath([string]$Path) {
    $current = [IO.Path]::GetFullPath($Path)
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

function Assert-PlainTree([string]$Path) {
    Assert-PlainPath $Path
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($Path)
    while ($pending.Count) {
        foreach ($item in (Get-ChildItem -LiteralPath $pending.Pop() -Force)) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Build trees cannot contain a reparse point: $($item.FullName)" }
            if ($item.PSIsContainer) { $pending.Push($item.FullName) }
        }
    }
}

function Resolve-BuildOutput([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { throw 'OutputDirectory must name a dedicated staging directory.' }
    $path = if ([IO.Path]::IsPathRooted($Value)) { $Value } else { Join-Path (Get-Location).Path $Value }
    $target = [IO.Path]::GetFullPath($path).TrimEnd($separator)
    $repository = $root.TrimEnd($separator)
    $volume = [IO.Path]::GetPathRoot($target).TrimEnd($separator)
    if ($target -eq $volume -or $repository.Equals($target, $comparison) -or $repository.StartsWith($target + $separator, $comparison)) {
        throw 'OutputDirectory cannot be a filesystem root, repository root or repository ancestor.'
    }
    $default = [IO.Path]::GetFullPath((Join-Path $root 'desktop\src-tauri\local-services')).TrimEnd($separator)
    $temporary = [IO.Path]::GetFullPath((Join-Path $root '.tmp')).TrimEnd($separator)
    if ($target.StartsWith($repository + $separator, $comparison) -and
        -not $target.Equals($default, $comparison) -and -not $target.StartsWith($default + $separator, $comparison) -and
        -not $target.StartsWith($temporary + $separator, $comparison)) {
        throw 'Repository output must be inside desktop/src-tauri/local-services or a dedicated .tmp subdirectory.'
    }
    Assert-PlainPath $target
    if ((Test-Path -LiteralPath $target) -and -not (Test-Path -LiteralPath $target -PathType Container)) { throw 'OutputDirectory must be a directory.' }
    return $target
}

function Remove-CheckedTree([string]$Path, [string]$Boundary) {
    $target = [IO.Path]::GetFullPath($Path).TrimEnd($separator)
    $allowed = [IO.Path]::GetFullPath($Boundary).TrimEnd($separator)
    if (-not $target.Equals($allowed, $comparison) -and -not $target.StartsWith($allowed + $separator, $comparison)) {
        throw 'Refusing cleanup outside the validated build boundary.'
    }
    Assert-PlainTree $target
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
}

function Copy-TreeContents([string]$Source, [string]$Destination) {
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) { throw "Required build directory is missing: $Source" }
    Assert-PlainTree $Source
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    foreach ($item in (Get-ChildItem -LiteralPath $Source -Force)) {
        Copy-Item -LiteralPath $item.FullName -Destination $Destination -Recurse -Force
    }
}

function Get-TrackedPublicFiles {
    # Read NUL-delimited names without PowerShell's native line conversion.
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = (Get-Command git -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $start.Arguments = 'ls-files -z -- frontend/public'
    $start.WorkingDirectory = $root
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        $process.Start() | Out-Null
        $names = $process.StandardOutput.ReadToEnd()
        $errors = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "Cannot inventory tracked public files: $errors" }
    } finally { $process.Dispose() }
    foreach ($name in $names.Split([char[]]@([char]0), [StringSplitOptions]::RemoveEmptyEntries)) {
        if (-not $name.StartsWith('frontend/public/', [StringComparison]::Ordinal) -or $name -match '[\x00-\x1f\x7f]') {
            throw 'Invalid tracked public asset path.'
        }
        $source = [IO.Path]::GetFullPath((Join-Path $root $name))
        if (-not $source.StartsWith($frontendPublic + $separator, $comparison)) { throw 'Tracked public asset escaped its source directory.' }
        Assert-PlainPath $source
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Tracked public asset is missing: $name" }
        $source
    }
}

$commit = Get-VerifiedSourceCommit
$buildOutput = Resolve-BuildOutput $OutputDirectory
$frontendRoot = Join-Path $root 'frontend'
$frontendPublic = Join-Path $frontendRoot 'public'
$generatedPublic = @('monaco\0.55.1', 'xterm\6.0.0')
# Reject unsafe generated-source paths before deleting the previous stage.
foreach ($relative in $generatedPublic) { Assert-PlainTree (Join-Path $frontendPublic $relative) }
$trackedPublicFiles = @(Get-TrackedPublicFiles)
$nodeCommand = (Get-Command node.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$npmCommand = (Get-Command npm.cmd -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$goCommand = (Get-Command go -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$pythonCommand = (Get-Command $PythonExecutable -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$environmentNames = @('API_BACKEND', 'API_BACKEND_URL', 'MISSION_CONTROL_URL', 'GO_GATEWAY_URL', 'GOCACHE', 'NEXT_DIST_DIR', 'PYTHONPATH')
$savedEnvironment = @{}
foreach ($name in $environmentNames) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }

try {
    # This exact output was resolved and checked before recursive cleanup.
    Write-Output "Rebuilding the dedicated local service stage: $buildOutput"
    Remove-CheckedTree $buildOutput $buildOutput
    [IO.Directory]::CreateDirectory($buildOutput) | Out-Null
    & (Join-Path $PSScriptRoot 'build-mission-control.ps1') -OutputDirectory $buildOutput -PythonExecutable $pythonCommand
    if ($LASTEXITCODE -ne 0) { throw 'Mission Control freeze failed.' }

    $nextOutput = [IO.Path]::GetFullPath((Join-Path $frontendRoot '.next'))
    Remove-CheckedTree $nextOutput $frontendRoot
    # npm's build preparation recreates these exact pinned local asset trees.
    foreach ($relative in $generatedPublic) { Remove-CheckedTree (Join-Path $frontendPublic $relative) $frontendPublic }
    Push-Location $frontendRoot
    try {
        # Rewrites are baked into standalone output. The first desktop instance
        # owns this port group; concurrent instances remain unsupported here.
        $env:NEXT_DIST_DIR = '.next'
        $env:API_BACKEND = 'legacy'
        $env:API_BACKEND_URL = 'http://127.0.0.1:28000'
        $env:MISSION_CONTROL_URL = 'http://127.0.0.1:28000'
        $env:GO_GATEWAY_URL = 'http://127.0.0.1:28001'
        $env:PYTHONPATH = $root
        & $npmCommand run build
        if ($LASTEXITCODE -ne 0) { throw 'Next.js production build failed.' }
    } finally { Pop-Location }
    $standalone = Join-Path $nextOutput 'standalone'
    if (-not (Test-Path -LiteralPath (Join-Path $standalone 'server.js') -PathType Leaf)) { throw 'Next.js standalone server.js was not generated.' }
    $frontendOutput = Join-Path $buildOutput 'frontend'
    Copy-TreeContents $standalone $frontendOutput
    Copy-TreeContents (Join-Path $nextOutput 'static') (Join-Path $frontendOutput '.next\static')
    $publicOutput = Join-Path $frontendOutput 'public'
    # Standalone tracing must not bypass this source allowlist.
    Remove-CheckedTree $publicOutput $buildOutput
    [IO.Directory]::CreateDirectory($publicOutput) | Out-Null
    foreach ($source in $trackedPublicFiles) {
        Assert-PlainPath $source
        $relative = $source.Substring($frontendPublic.Length + 1)
        $destination = Join-Path $publicOutput $relative
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
    foreach ($relative in $generatedPublic) {
        Copy-TreeContents (Join-Path $frontendPublic $relative) (Join-Path $publicOutput $relative)
    }
    # Unused development overlay paths exceed Explorer ZIP extraction limits.
    $devOverlay = Join-Path $frontendOutput 'node_modules\next\dist\client\components\react-dev-overlay'
    Remove-CheckedTree $devOverlay $buildOutput
    Copy-Item -LiteralPath $nodeCommand -Destination (Join-Path $frontendOutput 'node.exe') -Force
    Write-Output "Next.js standalone frontend staged in $frontendOutput"

    $goCache = Join-Path $root '.tmp\go-cache'
    Assert-PlainPath $goCache
    [IO.Directory]::CreateDirectory($goCache) | Out-Null
    Push-Location (Join-Path $root 'services\go')
    try {
        $env:GOCACHE = $goCache
        & $goCommand build -trimpath -ldflags '-s -w' -o (Join-Path $buildOutput 'agenthub-gateway.exe') ./gateway-service/cmd/gateway-service
        if ($LASTEXITCODE -ne 0) { throw 'Gateway build failed.' }
        & $goCommand build -trimpath -ldflags '-s -w' -o (Join-Path $buildOutput 'agenthub-mcp-gateway.exe') ./mcp-gateway/cmd/mcp-gateway
        if ($LASTEXITCODE -ne 0) { throw 'MCP Gateway build failed.' }
    } finally { Pop-Location }
    if ((Get-VerifiedSourceCommit) -ne $commit) { throw 'Source HEAD changed during the stack build.' }
    $stackVersion = (Get-Content -LiteralPath (Join-Path $root 'desktop\src-tauri\tauri.conf.json') -Raw | ConvertFrom-Json).version
    if ([string]::IsNullOrWhiteSpace($stackVersion)) { throw 'Desktop stack version is missing.' }
    # Retain the schema consumed by the native supervisor. Package resource
    # inventory is produced separately after this fresh stage completes.
    $manifest = [ordered]@{
        schemaVersion = 1
        version = $stackVersion
        commit = $commit
        generatedAt = (Get-Date).ToUniversalTime().ToString('o')
        services = @('agenthub-mission-control.exe', 'agenthub-gateway.exe', 'agenthub-mcp-gateway.exe', 'frontend/server.js')
    } | ConvertTo-Json
    [IO.File]::WriteAllText((Join-Path $buildOutput 'stack-manifest.json'), $manifest, [Text.UTF8Encoding]::new($false))
    Write-Output "Local service binaries staged from $commit in $buildOutput"
} finally {
    foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
}
