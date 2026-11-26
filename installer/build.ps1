param(
    [string]$ControlUrl,
    [string]$LeasePublicKey,
    [Parameter(Mandatory=$true)][string]$TesseractDirectory,
    [switch]$LocalPreview,
    [ValidateSet("eng", "ita", "eng+ita", "ita+eng")][string]$OcrLanguages = "eng",
    [string]$UpdateTrustedRoot,
    [string]$PublisherThumbprint,
    [string]$TimestampUrl,
    [string]$SignToolPath = "signtool.exe",
    [string]$InnoCompiler = "ISCC.exe"
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if ($LocalPreview) {
    if ($ControlUrl -or $LeasePublicKey -or $UpdateTrustedRoot) {
        throw "L’anteprima locale usa un servizio licenze temporaneo sul dispositivo"
    }
} elseif (!$ControlUrl -or !$LeasePublicKey -or ([uri]$ControlUrl).Scheme -ne "https") {
    throw "Specifica l’URL HTTPS del control plane e la sua chiave pubblica"
}
if ($UpdateTrustedRoot -and !$PublisherThumbprint) { throw "Gli aggiornamenti integrati richiedono una firma Authenticode" }
if ($PublisherThumbprint -and ($PublisherThumbprint -notmatch '^[0-9A-Fa-f]{40}$' -or !$TimestampUrl)) {
    throw "Specifica il thumbprint del certificato e il servizio di timestamp"
}
if ($PublisherThumbprint) {
    Get-Command $SignToolPath -ErrorAction Stop | Out-Null
    $certificates = @(Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
        Where-Object { $_.Thumbprint -eq $PublisherThumbprint -and $_.HasPrivateKey -and
            $_.NotBefore -le (Get-Date) -and $_.NotAfter -gt (Get-Date) })
    if ($certificates.Count -ne 1) {
        throw "Certificato Code Signing valido con chiave utilizzabile non trovato in CurrentUser\My"
    }
}
function Sign-ReleaseFile([string]$File) {
    if ($PublisherThumbprint) {
        & $SignToolPath sign /sha1 $PublisherThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $File
        if ($LASTEXITCODE) { throw "Firma Authenticode non riuscita: $File" }
        & $SignToolPath verify /pa $File
        if ($LASTEXITCODE) { throw "Verifica Authenticode non riuscita: $File" }
    }
}
$ocr = (Resolve-Path $TesseractDirectory).Path
foreach ($needed in @("tesseract.exe", "tessdata\ita.traineddata", "tessdata\eng.traineddata")) {
    if (!(Test-Path (Join-Path $ocr $needed) -PathType Leaf)) { throw "Tesseract incompleto: $needed" }
}
$modelManifest = Get-Content "installer\ocr-models.json" -Raw | ConvertFrom-Json
foreach ($model in $modelManifest.files.PSObject.Properties) {
    $modelPath = Join-Path (Join-Path $ocr "tessdata") $model.Name
    if ((Get-FileHash $modelPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $model.Value) {
        throw "Il modello OCR non corrisponde alla versione verificata: $($model.Name)"
    }
}
$stage = Join-Path (Get-Location) "build\desktop-config"
New-Item -ItemType Directory -Force $stage | Out-Null
$config = Join-Path $stage "desktop-config.json"
$modeData = if ($LocalPreview) {
    @("--add-data", "order_extractor/sample.pdf;order_extractor")
} else {
    $key = Join-Path $stage "lease-public.pem"
    Copy-Item $LeasePublicKey $key -Force
    @("--add-data", "$key;order_extractor")
}
if ($UpdateTrustedRoot) {
    $updateRoot = Join-Path $stage "update-root.json"
    Copy-Item $UpdateTrustedRoot $updateRoot -Force
    $modeData += @("--add-data", "$updateRoot;order_extractor")
}
@{local_preview = [bool]$LocalPreview; control_url = $ControlUrl; enable_updates = [bool]$UpdateTrustedRoot; publisher_thumbprint = $PublisherThumbprint; ocr_languages = $OcrLanguages} | ConvertTo-Json | Set-Content -Path $config -Encoding utf8
uv sync --locked --group windows
if ($LASTEXITCODE) { throw "Installazione dipendenze Python non riuscita" }
$workerProbe = Join-Path $stage "worker-probe.pdf"
uv run --locked python -c "import sys; from pathlib import Path; from scripts.fixtures import build; build(Path(sys.argv[1]), scan=True)" $workerProbe
if ($LASTEXITCODE) { throw "Creazione del PDF sintetico di collaudo non riuscita" }
Push-Location frontend
try {
    npm ci
    if ($LASTEXITCODE) { throw "Installazione dipendenze frontend non riuscita" }
    npm run build
    if ($LASTEXITCODE) { throw "Build frontend non riuscita" }
} finally { Pop-Location }
uv run --locked --group windows pyinstaller --noconfirm --clean --onedir --windowed --name OrderExtractor --icon installer/order-extractor.ico --paths . --collect-all pypdfium2 --collect-all pypdfium2_raw --collect-all webview --collect-data pdfminer --add-data "order_extractor/web;order_extractor/web" --add-data "$config;order_extractor" --add-data "$ocr;order_extractor/ocr" --add-data "$workerProbe;order_extractor" @modeData installer/desktop.py
if ($LASTEXITCODE) { throw "Packaging PyInstaller non riuscito" }
uv run --locked --group windows pyinstaller --noconfirm --onedir --windowed --name OrderExtractorLauncher --icon installer/order-extractor.ico --paths . installer/supervisor.py
if ($LASTEXITCODE) { throw "Build launcher non riuscita" }
Sign-ReleaseFile "dist\OrderExtractor\OrderExtractor.exe"
Sign-ReleaseFile "dist\OrderExtractorLauncher\OrderExtractorLauncher.exe"
$version = (uv run --locked python -c "from order_extractor import VERSION; import tomllib; assert VERSION == tomllib.load(open('pyproject.toml','rb'))['project']['version']; print(VERSION)").Trim()
if ($LASTEXITCODE -or !$version) { throw "Versione progetto non valida" }
$installerDefines = @("/DReleaseVersion=$version")
if ($LocalPreview) { $installerDefines += "/DLocalPreview=1" }
& $InnoCompiler @installerDefines "installer\order-extractor.iss"
if ($LASTEXITCODE) { throw "Compilazione Inno Setup non riuscita" }
$installer = "dist\installer\OrderExtractor-$version-x64.exe"
Sign-ReleaseFile $installer
$hash = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  OrderExtractor-$version-x64.exe" | Set-Content "dist\installer\SHA256SUMS.txt" -Encoding ascii
uv run --locked python -m scripts.package_desktop "dist\OrderExtractor" "dist\desktop-$version.zip"
if ($LASTEXITCODE) { throw "Creazione pacchetto aggiornamento non riuscita" }
