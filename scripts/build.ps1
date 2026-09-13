param([string]$Python = 'python', [string]$ISCC = '', [string]$CertificateThumbprint = '')
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & $Python -m PyInstaller --noconfirm --clean --name ChemCompute --icon chemcompute/assets/chemcompute.ico --onedir --collect-all chemcompute --collect-all uvicorn launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
    if ($CertificateThumbprint) {
        & "$PSScriptRoot/sign-file.ps1" -Path dist/ChemCompute/ChemCompute.exe -CertificateThumbprint $CertificateThumbprint
        Export-Certificate -Cert "Cert:\CurrentUser\My\$CertificateThumbprint" -FilePath dist/ChemCompute-SelfSigned.cer | Out-Null
    }
    if (-not $ISCC) {
        $ISCC = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
        if (-not $ISCC) { $ISCC = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
    }
    if (-not (Test-Path $ISCC)) { throw 'Install Inno Setup 6 or provide -ISCC path.' }
    & $ISCC installer\ChemCompute.iss
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed' }
    if ($CertificateThumbprint) {
        & "$PSScriptRoot/sign-file.ps1" -Path dist/ChemCompute-Setup.exe -CertificateThumbprint $CertificateThumbprint
    }
    Compress-Archive -Path examples\water-smoke\* -DestinationPath dist\ChemCompute-Water-Smoke.zip -Force
    $hash = (Get-FileHash dist\ChemCompute-Setup.exe -Algorithm SHA256).Hash.ToLower()
    "$hash  ChemCompute-Setup.exe" | Set-Content dist\SHA256SUMS.txt -Encoding ascii
    $sampleHash = (Get-FileHash dist\ChemCompute-Water-Smoke.zip -Algorithm SHA256).Hash.ToLower()
    "$sampleHash  ChemCompute-Water-Smoke.zip" | Add-Content dist\SHA256SUMS.txt -Encoding ascii
    if ($CertificateThumbprint) {
        $certHash = (Get-FileHash dist/ChemCompute-SelfSigned.cer -Algorithm SHA256).Hash.ToLower()
        "$certHash  ChemCompute-SelfSigned.cer" | Add-Content dist/SHA256SUMS.txt -Encoding ascii
    }
} finally { Pop-Location }
