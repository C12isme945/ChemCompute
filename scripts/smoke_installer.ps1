param([string]$Installer = '.\dist\ChemCompute-Setup.exe', [string]$TestRoot, [string]$Python = 'python')
$ErrorActionPreference = 'Stop'
if (-not $TestRoot) { throw 'Provide an empty isolated -TestRoot directory.' }
$resolvedTestRoot = [IO.Path]::GetFullPath($TestRoot)
if (Test-Path $resolvedTestRoot) { throw 'TestRoot must not exist; use a fresh test directory.' }
New-Item -ItemType Directory -Path $resolvedTestRoot | Out-Null
$priorHome = $env:CHEMCOMPUTE_HOME
try {
    $env:CHEMCOMPUTE_HOME = Join-Path $resolvedTestRoot 'runtime'
    $installPath = Join-Path $resolvedTestRoot 'app'
    $proc = Start-Process -FilePath (Resolve-Path $Installer) -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/DIR="'+$installPath+'"')) -WindowStyle Hidden -Wait -PassThru
    if ($proc.ExitCode -ne 0) { throw "Install failed: $($proc.ExitCode)" }
    if (-not (Test-Path "$env:CHEMCOMPUTE_HOME\config\node.yaml")) { throw 'Installer onboarding failed' }
    & $Python "$PSScriptRoot\smoke_exe.py" "$installPath\ChemCompute.exe"
    if ($LASTEXITCODE -ne 0) { throw 'Installed executable test failed' }
    $uninstaller = Join-Path $installPath 'unins000.exe'
    $proc = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -WindowStyle Hidden -Wait -PassThru
    if ($proc.ExitCode -ne 0) { throw "Uninstall failed: $($proc.ExitCode)" }
    if (Test-Path "$installPath\ChemCompute.exe") { throw 'Executable remains after uninstall' }
    if (-not (Test-Path "$env:CHEMCOMPUTE_HOME\config\node.yaml")) { throw 'Uninstall removed retained configuration' }
    Write-Output 'PASS: silent install, onboarding, installed EXE heartbeat, uninstall, retained configuration'
} finally {
    $env:CHEMCOMPUTE_HOME = $priorHome
}
