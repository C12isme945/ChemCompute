param([string]$Python = 'python', [string]$ISCC = '')
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & $Python -m PyInstaller --noconfirm --clean --name ChemCompute --onedir --collect-all chemcompute --collect-all uvicorn launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
    if (-not $ISCC) {
        $ISCC = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
        $candidates = @(
            "E:\Research\InnoSetup\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
        )
        foreach ($c in $candidates) {
            if (Test-Path $c) { $ISCC = $c; break }
        }
    }
    if (-not (Test-Path $ISCC)) { throw 'Install Inno Setup 6 or provide -ISCC path.' }
    & $ISCC installer\ChemCompute.iss
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed' }
    Compress-Archive -Path examples\water-smoke\* -DestinationPath dist\ChemCompute-Water-Smoke.zip -Force
    $hash = (Get-FileHash dist\ChemCompute-Setup.exe -Algorithm SHA256).Hash.ToLower()
    "$hash  ChemCompute-Setup.exe" | Set-Content dist\SHA256SUMS.txt -Encoding ascii
    $sampleHash = (Get-FileHash dist\ChemCompute-Water-Smoke.zip -Algorithm SHA256).Hash.ToLower()
    "$sampleHash  ChemCompute-Water-Smoke.zip" | Add-Content dist\SHA256SUMS.txt -Encoding ascii
} finally { Pop-Location }
