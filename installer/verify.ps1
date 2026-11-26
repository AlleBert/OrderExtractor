param(
    [Parameter(Mandatory=$true)][string]$ReleaseVersion,
    [string]$ClientPath,
    [string]$InstallerPath,
    [string]$PublisherThumbprint,
    [string]$ReportPath
)
$ErrorActionPreference = "Stop"
if ($ReleaseVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') { throw "Versione non valida" }
if ($PublisherThumbprint -and $PublisherThumbprint -notmatch '^[0-9a-fA-F]{40}$') { throw "Thumbprint non valido" }
if (!$ClientPath) {
    $ClientPath = Join-Path $env:LOCALAPPDATA "Programs\Order Extractor\versions\$ReleaseVersion\OrderExtractor.exe"
}
$client = (Resolve-Path -LiteralPath $ClientPath).Path

function Check-Signature([string]$File) {
    $signature = Get-AuthenticodeSignature -LiteralPath $File
    if ($PublisherThumbprint -and ($signature.Status -ne "Valid" -or
            $signature.SignerCertificate.Thumbprint -ne $PublisherThumbprint)) {
        throw "Firma Authenticode o certificato del firmatario non corrispondente"
    }
    return $signature.Status.ToString()
}

$report = [ordered]@{
    version = $ReleaseVersion
    system_build = [Environment]::OSVersion.Version.ToString()
    client_signature = (Check-Signature $client)
    installer_sha256 = $null
    installer_signature = $null
    startup_probe = "pending"
}
if ($InstallerPath) {
    $installer = (Resolve-Path -LiteralPath $InstallerPath).Path
    $hashes = Join-Path (Split-Path -Parent $installer) "SHA256SUMS.txt"
    $name = Split-Path -Leaf $installer
    $expected = @(Get-Content -LiteralPath $hashes | ForEach-Object {
        if ($_ -match '^([a-fA-F0-9]{64})\s+\*?(.*)$' -and $Matches[2] -ceq $name) { $Matches[1] }
    })
    $actual = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
    if ($expected.Count -ne 1 -or $actual -ne $expected[0]) { throw "Checksum installer non corrispondente" }
    $report.installer_sha256 = $actual.ToLowerInvariant()
    $report.installer_signature = Check-Signature $installer
}
$data = Join-Path $env:TEMP ("OrderExtractor-verify-" + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $data | Out-Null
try {
    $trial = Start-Process -FilePath $client -ArgumentList @(
        "--probe-version", $ReleaseVersion, "--data-dir", ('"' + $data + '"')
    ) -WorkingDirectory (Split-Path -Parent $client) -PassThru
    try {
        if (!$trial.WaitForExit(90000)) {
            $trial.Kill()
            $trial.WaitForExit()
            throw "Tempo massimo della prova del client superato"
        }
        if ($trial.ExitCode -ne 0) { throw "Prova del pacchetto non riuscita: codice $($trial.ExitCode)" }
        $report.startup_probe = "passed"
    } finally {
        $trial.Dispose()
    }
} finally {
    Remove-Item -LiteralPath $data -Recurse -Force -ErrorAction SilentlyContinue
}
$json = $report | ConvertTo-Json
if ($ReportPath) { $json | Set-Content -LiteralPath $ReportPath -Encoding utf8 }
$json
