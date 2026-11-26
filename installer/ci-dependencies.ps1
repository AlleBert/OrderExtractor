param([Parameter(Mandatory=$true)][string]$ToolDirectory)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force $ToolDirectory | Out-Null

function Get-VerifiedInstaller($Url, $Checksum) {
    $path = Join-Path $ToolDirectory ([uri]$Url).Segments[-1]
    Invoke-WebRequest -Uri $Url -OutFile $path
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $Checksum) {
        throw "Checksum non valido per $path"
    }
    return $path
}

function Invoke-Installer($Path, $Arguments) {
    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Installazione fallita: $Path ($($process.ExitCode))" }
}

$tesseractSetup = Get-VerifiedInstaller `
    'https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-5.5.3.20260724.exe' `
    'bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4'
$tesseract = Join-Path $ToolDirectory 'Tesseract-OCR'
# NSIS requires its unquoted /D option at the end of the command line.
Invoke-Installer $tesseractSetup "/S /D=$tesseract"
$distribution = Join-Path $tesseract 'distribution'
New-Item -ItemType Directory -Force (Join-Path $distribution 'tessdata') | Out-Null
Copy-Item (Join-Path $tesseract '*.dll') $distribution
Copy-Item (Join-Path $tesseract 'tesseract.exe') $distribution
Get-ChildItem -LiteralPath $tesseract -File | Where-Object {
    $_.Name -match '^(COPYING|LICENSE|NOTICE|AUTHORS|README)'
} | Copy-Item -Destination $distribution
$modelManifest = Get-Content (Join-Path $PSScriptRoot 'ocr-models.json') -Raw | ConvertFrom-Json
$modelSource = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/$($modelManifest.release)"
foreach ($model in $modelManifest.files.PSObject.Properties) {
    Invoke-WebRequest "$modelSource/$($model.Name)" -OutFile (Join-Path $distribution "tessdata/$($model.Name)")
}
Invoke-WebRequest "$modelSource/LICENSE" -OutFile (Join-Path $distribution 'tessdata/LICENSE')

$innoSetup = Get-VerifiedInstaller `
    'https://github.com/jrsoftware/issrc/releases/download/is-6_7_0/innosetup-6.7.0.exe' `
    'f45c7d68d1e660cf13877ec36738a5179ce72a33414f9959d35e99b68c52a697'
$inno = Join-Path $ToolDirectory 'Inno Setup'
Invoke-Installer $innoSetup ('/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="' + $inno + '"')

$ghZip = Get-VerifiedInstaller `
    'https://github.com/cli/cli/releases/download/v2.106.0/gh_2.106.0_windows_amd64.zip' `
    'd406f442ca0baa3d3e9c695e9cded1aa48e67dad3294b8947e5ab84f980b4844'
$ghDirectory = Join-Path $ToolDirectory 'github-cli'
Expand-Archive -LiteralPath $ghZip -DestinationPath $ghDirectory
"OE_CI_GITHUB_CLI=$ghDirectory" >> $env:GITHUB_ENV

"OE_CI_TESSERACT=$distribution" >> $env:GITHUB_ENV
"OE_CI_INNO=$(Join-Path $inno 'ISCC.exe')" >> $env:GITHUB_ENV
