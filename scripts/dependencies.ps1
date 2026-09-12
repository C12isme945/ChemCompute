param([switch]$WSL, [switch]$Gromacs, [switch]$Drivers, [switch]$PlanOnly,
      [string]$StatePath = "$env:LOCALAPPDATA\ChemComputeData\dependency-status.json")
$ErrorActionPreference = 'Stop'
$choices = @()
if ($WSL) { $choices += '-WSL' }
if ($Gromacs) { $choices += '-Gromacs' }
if ($Drivers) { $choices += '-Drivers' }
$state = [ordered]@{ selected=$choices; updated=(Get-Date).ToString('o'); status='planned'; components=@{}; message='' }
function Save-State {
    $state.updated = (Get-Date).ToString('o')
    New-Item -ItemType Directory -Path (Split-Path $StatePath) -Force | Out-Null
    $state | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StatePath -Encoding UTF8
}
if ($PlanOnly) { $state | ConvertTo-Json -Depth 6; exit 0 }
if (-not $choices.Count) { $state.status='skipped'; Save-State; exit 0 }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    $state.status='awaiting_elevation'; $state.message='Approve Windows administrator prompt to install selected components.'; Save-State
    try {
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" ' + ($choices -join ' ') + ' -StatePath "' + $StatePath + '"'
        $process = Start-Process powershell.exe -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru -Wait
        exit $process.ExitCode
    } catch { $state.status='needs_attention'; $state.message='Administrator request cancelled. Resume from desktop when ready.'; Save-State; exit 1 }
}
$mutex = New-Object Threading.Mutex($false, ('Local\ChemComputeDependencies-' + $identity.User.Value))
if (-not $mutex.WaitOne(0)) { exit 0 }
function Resume-AfterRestart {
    $command = 'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" ' + ($choices -join ' ') + ' -StatePath "' + $StatePath + '"'
    New-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -Force | Out-Null
    New-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -Name ChemComputeDependencies -Value $command -PropertyType String -Force | Out-Null
    $state.status='restart_required'; $state.message='Restart Windows, sign in and approve the continuation prompt. No automatic reboot.'; Save-State
}
try {
    $state.status='installing'; Save-State
    if ($WSL -or $Gromacs) {
        $distros = @(& wsl.exe --list --quiet 2>$null) -join "`n"
        $distros = $distros -replace "`0", ''
        if ($LASTEXITCODE -ne 0 -or -not $distros.Trim()) {
            if (-not $WSL) { throw 'GROMACS requires an existing WSL distribution, or select WSL installation.' }
            $state.components.wsl='installing'; Save-State
            & wsl.exe --install --distribution Ubuntu-24.04 --no-launch --web-download *> (Join-Path (Split-Path $StatePath) 'wsl-install.log')
            if ($LASTEXITCODE -notin @(0,3010,1641)) { throw 'WSL setup did not finish. See wsl-install.log; virtualization/restart may be required.' }
            & wsl.exe --exec true 2>$null
            if ($LASTEXITCODE -ne 0) { $state.components.wsl='restart_required'; Resume-AfterRestart; exit 3010 }
        }
        & wsl.exe --exec true 2>$null
        if ($LASTEXITCODE -ne 0) { $state.components.wsl='restart_required'; Resume-AfterRestart; exit 3010 }
        $state.components.wsl='verified'; Save-State
    }
    if ($Gromacs) {
        $state.components.gromacs='checking'; Save-State
        & wsl.exe --exec gmx --version *> (Join-Path (Split-Path $StatePath) 'gromacs-probe.log')
        if ($LASTEXITCODE -ne 0) {
            $state.components.gromacs='installing'; Save-State
            & wsl.exe -u root --exec apt-get update *> (Join-Path (Split-Path $StatePath) 'gromacs-install.log')
            if ($LASTEXITCODE -ne 0) { throw 'Default WSL distribution must support apt-get and access Ubuntu/Debian repositories.' }
            & wsl.exe -u root --exec env DEBIAN_FRONTEND=noninteractive apt-get install -y gromacs >> (Join-Path (Split-Path $StatePath) 'gromacs-install.log') 2>&1
            if ($LASTEXITCODE -ne 0) { throw 'GROMACS package installation failed; see gromacs-install.log.' }
        }
        & wsl.exe --exec gmx --version *> (Join-Path (Split-Path $StatePath) 'gromacs-probe.log')
        if ($LASTEXITCODE -ne 0) { throw 'GROMACS executable verification failed.' }
        $state.components.gromacs='verified'; Save-State
    }
    if ($Drivers) {
        # Use Windows' hardware-matched signed display drivers. Never install Linux display drivers in WSL.
        $state.components.drivers='checking_windows_update'; Save-State
        $session = New-Object -ComObject Microsoft.Update.Session
        $session.ClientApplicationID = 'ChemCompute optional display driver setup'
        $found = $session.CreateUpdateSearcher().Search("IsInstalled=0 and Type='Driver' and IsHidden=0")
        $updates = New-Object -ComObject Microsoft.Update.UpdateColl
        foreach ($update in $found.Updates) {
            if ($update.DriverClass -eq 'Display') {
                if (-not $update.EulaAccepted) { $update.AcceptEula() }
                $null = $updates.Add($update)
            }
        }
        if ($updates.Count) {
            $state.components.drivers='installing_hardware_matched_display_driver'; Save-State
            $download = $session.CreateUpdateDownloader(); $download.Updates=$updates
            $downloadResult = $download.Download()
            if ($downloadResult.ResultCode -ne 2) { throw 'Display driver download failed or partially completed.' }
            $installer = $session.CreateUpdateInstaller(); $installer.Updates=$updates
            $result = $installer.Install()
            if ($result.ResultCode -ne 2) { throw 'Display driver installation failed or partially completed.' }
            $state.components.drivers='installed'
            if ($result.RebootRequired) { Resume-AfterRestart; exit 3010 }
        } else { $state.components.drivers='no_applicable_update' }
    }
    $state.status='completed'; $state.message='Selected components checked. Repository GROMACS may be CPU-only; CUDA build is separate.'; Save-State
} catch {
    $state.status='needs_attention'; $state.message=$_.Exception.Message; Save-State; exit 1
} finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
