[CmdletBinding()]
param(
    [string]$TargetTriple,
    [int]$TimeoutMilliseconds = 5000
)

$ErrorActionPreference = "Stop"
$desktopDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path

if ([string]::IsNullOrWhiteSpace($TargetTriple)) {
    # rust-toolchain.toml pins the compiler; avoid a second toolchain selector here.
    $rustcInfo = & rustc -vV
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the pinned Rust toolchain."
    }
    $hostLine = $rustcInfo | Where-Object { $_ -like "host:*" } | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($hostLine)) {
        throw "Rust compiler output did not contain a host target."
    }
    $TargetTriple = ($hostLine -split ":", 2)[1].Trim()
}

if ([string]::IsNullOrWhiteSpace($TargetTriple) -or $TargetTriple -notlike "*-windows-*") {
    throw "This runtime smoke supports Windows targets only; received '$TargetTriple'."
}
if ($TimeoutMilliseconds -lt 500 -or $TimeoutMilliseconds -gt 30000) {
    throw "TimeoutMilliseconds must be between 500 and 30000."
}

$releaseDirectory = Join-Path $desktopDirectory "src-tauri\target\$TargetTriple\release"
$sidecar = Join-Path $releaseDirectory "agenthub-runtime.exe"
if (-not (Test-Path -LiteralPath $sidecar -PathType Leaf)) {
    throw "Packaged sidecar was not found at $sidecar."
}

$endpoint = "http://127.0.0.1:18097/readyz"
$artifactRoot = Join-Path ([IO.Path]::GetTempPath()) ("agenthub-runtime-smoke-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $artifactRoot -Force | Out-Null
$startInfo = [Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $sidecar
$startInfo.WorkingDirectory = $releaseDirectory
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardError = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.ArgumentList.Add("--health-endpoint")
$startInfo.ArgumentList.Add($endpoint)
$startInfo.ArgumentList.Add("--artifact-root")
$startInfo.ArgumentList.Add($artifactRoot)

$process = [Diagnostics.Process]::new()
$process.StartInfo = $startInfo
$handler = [Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$client = [Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromMilliseconds(250)
$ready = $false
$lastError = "readiness endpoint did not respond"

try {
    if (-not $process.Start()) {
        throw "Unable to start packaged sidecar."
    }

    $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMilliseconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($process.HasExited) {
            $stderr = $process.StandardError.ReadToEnd().Trim()
            throw "Packaged sidecar exited with code $($process.ExitCode): $stderr"
        }

        try {
            $response = $client.GetAsync($endpoint).GetAwaiter().GetResult()
            $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            if (-not $response.IsSuccessStatusCode) {
                $lastError = "HTTP status $([int]$response.StatusCode)"
            } else {
                $payload = $body | ConvertFrom-Json
                if ($payload.protocolVersion -ne 1 -or $payload.status -ne "ready") {
                    throw "Unexpected readiness payload: $body"
                }
                if ($payload.artifactRootStatus -ne "ready") {
                    throw "Artifact root was not ready: $body"
                }
                $ready = $true
                break
            }
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 50
    }

    if (-not $ready) {
        throw "Packaged sidecar did not become ready within ${TimeoutMilliseconds}ms: $lastError"
    }

    Write-Output "Packaged runtime smoke passed."
    Write-Output "Sidecar: $sidecar"
    Write-Output "Endpoint: $endpoint"
    Write-Output "Artifact root: $artifactRoot"
} finally {
    if (Test-Path -LiteralPath $artifactRoot -PathType Container) {
        Remove-Item -LiteralPath $artifactRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($process -and -not $process.HasExited) {
        $process.Kill()
        $process.WaitForExit()
    }
    $client.Dispose()
    $handler.Dispose()
    $process.Dispose()
}
