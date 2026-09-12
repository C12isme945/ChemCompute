# End-to-end build script for ChemCompute-Setup.exe and Portable Release

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectRoot = Split-Path -Parent $scriptRoot

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "         ChemCompute Installer & Release Build Pipeline         " -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Cyan

# Step 1: Ensure ChemComputeAgent.exe is compiled
$agentExe = "$projectRoot\dist\ChemComputeAgent\ChemComputeAgent.exe"
if (-not (Test-Path $agentExe)) {
    Write-Host "[1/3] Building ChemComputeAgent.exe..." -ForegroundColor Yellow
    python "$projectRoot\scripts\build_agent_exe.py"
    if (-not (Test-Path $agentExe)) {
        Write-Host "[FAIL] ChemComputeAgent.exe build failed." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[1/3] ChemComputeAgent.exe is ready: $agentExe" -ForegroundColor Green
}

# Step 2: Search for Inno Setup Compiler
Write-Host "[2/3] Searching for Inno Setup compiler (ISCC.exe)..." -ForegroundColor Yellow
$isccCandidates = @(
    "iscc",
    "C:\Program Files\Inno Setup 7\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)

$isccPath = $null
foreach ($cand in $isccCandidates) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) {
        $isccPath = $cand
        break
    } elseif (Test-Path $cand) {
        $isccPath = $cand
        break
    }
}

if ($isccPath) {
    Write-Host "  [OK] Found Inno Setup Compiler: $isccPath" -ForegroundColor Green
    Write-Host "  Compiling Inno Setup package ChemCompute-Setup-0.1.0.exe..." -ForegroundColor Cyan
    & $isccPath "$projectRoot\installer\chemcompute_setup.iss"
    $setupExe = "$projectRoot\dist\ChemCompute-Setup-0.1.0.exe"
    if (Test-Path $setupExe) {
        Write-Host "  [OK] Installer built successfully: $setupExe" -ForegroundColor Green
    }
} else {
    Write-Host "  [WARN] Inno Setup compiler (ISCC.exe) not found." -ForegroundColor Yellow
}

# Step 3: Produce portable release bundle
Write-Host "[3/3] Generating portable release bundle (Portable Release)..." -ForegroundColor Yellow
$portableDir = "$projectRoot\dist\ChemCompute-Portable"
if (Test-Path $portableDir) { Remove-Item -Recurse -Force $portableDir }
New-Item -ItemType Directory -Force -Path $portableDir | Out-Null

Copy-Item -Recurse "$projectRoot\dist\ChemComputeAgent\*" "$portableDir\"
Copy-Item "$projectRoot\installer\bootstrapper.ps1" "$portableDir\"

$readmeContent = "ChemCompute Node Portable`r`n`r`nQuick Start:`r`n1. Run bootstrapper.ps1 in PowerShell`r`n2. Or run ChemComputeAgent.exe --controller <URL> --invite <CODE>`r`n"
Set-Content -Path "$portableDir\README.txt" -Value $readmeContent -Encoding ASCII

$portableZip = "$projectRoot\dist\ChemCompute-Node-Portable-v0.1.0.zip"
if (Test-Path $portableZip) { Remove-Item -Force $portableZip }
Compress-Archive -Path "$portableDir\*" -DestinationPath $portableZip -Force

Write-Host "  [OK] Portable bundle packaged: $portableZip" -ForegroundColor Green
Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "                 Build Pipeline Finished!                       " -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Cyan
