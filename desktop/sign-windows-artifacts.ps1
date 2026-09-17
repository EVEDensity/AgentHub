# Code-sign every distributable Windows artifact with the certificate
# injected by CI (AGENTHUB_WINDOWS_SIGNING_CERT_BASE64 +
# AGENTHUB_WINDOWS_SIGNING_PASSWORD). Used by package-windows.ps1 on the
# tag path so public releases actually carry a signature — the release
# policy only gates on secret presence; this script applies them.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string[]]$Path
)

$ErrorActionPreference = 'Stop'

function Write-Signed {
    param([string]$File)

    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) {
        Write-Output "sign: skip (missing) $File"
        return
    }

    $certB64 = [Environment]::GetEnvironmentVariable('AGENTHUB_WINDOWS_SIGNING_CERT_BASE64')
    $certPassword = [Environment]::GetEnvironmentVariable('AGENTHUB_WINDOWS_SIGNING_PASSWORD')
    if ([string]::IsNullOrWhiteSpace($certB64) -or [string]::IsNullOrWhiteSpace($certPassword)) {
        Write-Output "sign: skip (no certificate secrets) $File"
        return
    }

    $certPath = Join-Path ([IO.Path]::GetTempPath()) ("agenthub-codesign-" + [guid]::NewGuid().ToString('N') + '.pfx')
    $imported = $null
    try {
        [IO.File]::WriteAllBytes($certPath, [Convert]::FromBase64String($certB64))
        $securePassword = ConvertTo-SecureString -String $certPassword -AsPlainText -Force
        $imported = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($certPath, $securePassword)
        if (-not $imported.HasPrivateKey) {
            throw 'the imported certificate has no private key; cannot sign.'
        }

        $sig = Get-AuthenticodeSignature -FilePath $File
        if ($sig.Status -eq 'Valid' -and $sig.SignerCertificate.Thumbprint -eq $imported.Thumbprint) {
            Write-Output "sign: already signed with the release certificate: $File"
            return
        }

        Set-AuthenticodeSignature -FilePath $File -Certificate $imported -TimestampServer 'http://timestamp.digicert.com' | Out-Null
        $check = Get-AuthenticodeSignature -FilePath $File
        if ($check.Status -ne 'Valid') {
            throw "signing $File failed: $($check.Status) ($($check.StatusMessage))"
        }
        Write-Output "sign: signed $File ($($check.SignerCertificate.Subject))"
    } finally {
        if ($imported) { $imported.Reset() }
        Remove-Item -LiteralPath $certPath -Force -ErrorAction SilentlyContinue
    }
}

foreach ($target in $Path) {
    foreach ($file in Get-ChildItem -Path $target -Recurse -File -ErrorAction SilentlyContinue) {
        if ($file.Extension -in '.exe', '.msi', '.dll') {
            Write-Signed $file.FullName
        }
    }
}
Write-Output 'sign: complete.'
