param([switch]$InstallTailscale, [switch]$EnableOpenSSH, [switch]$ConnectTailscale)
$ErrorActionPreference = 'Stop'
Get-Command tailscale, ssh -ErrorAction SilentlyContinue | Select-Object Name, Source
Get-Service Tailscale, sshd -ErrorAction SilentlyContinue | Select-Object Name, Status
if ($InstallTailscale) {
    winget install --id Tailscale.Tailscale --exact --source winget
    if ($LASTEXITCODE -ne 0) { throw 'Tailscale installation failed' }
}
if ($EnableOpenSSH) {
    # Elevated PowerShell required. Firewall is narrowed to the Tailscale range.
    Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
    Get-NetFirewallRule -Name OpenSSH-Server-In-TCP -ErrorAction SilentlyContinue | Disable-NetFirewallRule
    if (-not (Get-NetFirewallRule -Name ChemCompute-SSH-Tailscale -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name ChemCompute-SSH-Tailscale -DisplayName 'ChemCompute SSH via Tailscale' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -RemoteAddress 100.64.0.0/10
    }
    Set-Service sshd -StartupType Automatic
    Start-Service sshd
}
if ($ConnectTailscale) {
    $tailscale = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
    if (-not $tailscale) { $tailscale = "$env:ProgramFiles\Tailscale\tailscale.exe" }
    & $tailscale up --unattended=true
    if ($LASTEXITCODE -ne 0) { throw 'Complete Tailscale login and retry' }
}
