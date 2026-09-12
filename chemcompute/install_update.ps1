param([Parameter(Mandatory=$true)][string]$RequestPath)
$ErrorActionPreference = 'Stop'
$env:PSModulePath = (Join-Path $PSHOME 'Modules') + [IO.Path]::PathSeparator + $env:PSModulePath
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ChemComputeArgs {
    [DllImport("shell32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr CommandLineToArgvW(string command, out int count);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr pointer);
    public static string[] Parse(string command) {
        int count; IntPtr pointer = CommandLineToArgvW(command, out count);
        if (pointer == IntPtr.Zero) throw new InvalidOperationException("Cannot read command arguments");
        try { var values = new string[count]; for (int i=0; i<count; i++) values[i] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(pointer,i*IntPtr.Size)); return values; }
        finally { LocalFree(pointer); }
    }
}
'@
$request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$statusPath = Join-Path (Split-Path -Parent $RequestPath) 'status.json'
function Write-Status([string]$state, [string]$message) {
    @{state=$state; message=$message; version=$request.version; time=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8
}
function Get-MatchingProcess($record) {
    if ($null -eq $record) { return $null }
    $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    $created = ($process.StartTime.ToUniversalTime() - [DateTime]'1970-01-01').TotalSeconds
    if ([Math]::Abs($created - $record.created) -gt 0.05 -or $process.Path -ne $record.exe) { throw 'Process identity changed; update cancelled.' }
    $info = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)"
    # The GUI can exit between Get-Process and the CIM query. Never parse a
    # missing command line: CommandLineToArgvW(null) describes this helper.
    if ($null -eq $info -or [string]::IsNullOrWhiteSpace($info.CommandLine)) {
        if ($process.HasExited) { return $null }
        throw 'Unable to verify process arguments; update cancelled.'
    }
    $actual = [ChemComputeArgs]::Parse($info.CommandLine)
    if ($actual.Count -ne $record.argv.Count) { throw 'Process arguments changed; update cancelled.' }
    if ([IO.Path]::GetFullPath($actual[0]) -ne [IO.Path]::GetFullPath($record.argv[0])) { throw 'Process launch path changed; update cancelled.' }
    for ($i=1; $i -lt $actual.Count; $i++) {
        if ($actual[$i] -cne $record.argv[$i]) { throw 'Process arguments changed; update cancelled.' }
    }
    return $process
}
$backendStopped = $false
$installerStarted = $false
try {
    if (-not (Test-Path -LiteralPath $request.executable)) { throw 'Installed program not found.' }
    if ((Get-FileHash -LiteralPath $request.installer -Algorithm SHA256).Hash.ToLower() -ne $request.sha256) { throw 'Installer checksum mismatch.' }
    Write-Status 'waiting' 'Waiting for desktop to close.'
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while ($null -ne (Get-MatchingProcess $request.gui)) {
        if ([DateTime]::UtcNow -gt $deadline) { throw 'Desktop did not close; update cancelled.' }
        Start-Sleep -Milliseconds 300
    }
    if ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -gt $request.ticket.expires_at - 1800) { throw 'Update reservation expired; check again.' }
    $backend = Get-MatchingProcess $request.backend
    if ($null -ne $backend) {
        Stop-Process -Id $backend.Id -ErrorAction Stop
        if (-not $backend.WaitForExit(15000)) { throw 'Background process did not exit.' }
        $backendStopped = $true
    }
    # Abort if another GUI/backend instance has opened the same installed program.
    $others = Get-Process -Name ChemCompute -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $request.executable }
    if ($others) { throw 'Another ChemCompute window is open. Close it and retry.' }
    Write-Status 'installing' 'Installing verified update; system dependencies are unchanged.'
    $installDir = Split-Path -Parent $request.executable
    $installerStarted = $true
    $args = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOCLOSEAPPLICATIONS','/NORESTARTAPPLICATIONS','/UPDATE=1',('/DIR="'+$installDir+'"'))
    if ($request.test_install -eq $true) { $args += '/TESTINSTALL=1' }
    $setup = Start-Process -FilePath $request.installer -ArgumentList $args -WindowStyle Hidden -Wait -PassThru
    if ($setup.ExitCode -ne 0) { throw "Installer returned $($setup.ExitCode). Reinstall manually if needed; data was retained." }
    $env:CHEMCOMPUTE_HOME = $request.home
    $finish = Start-Process -FilePath $request.executable -ArgumentList @('update-finish',('"'+$RequestPath+'"')) -WindowStyle Hidden -Wait -PassThru
    if ($finish.ExitCode -ne 0) { throw 'Could not release maintenance lock; it expires automatically.' }
    Start-Process -FilePath $request.executable -ArgumentList 'desktop-run' -WindowStyle Hidden
    Start-Process -FilePath $request.executable -ArgumentList 'console' -WindowStyle Hidden
    Write-Status 'completed' 'Installation completed. Desktop and background restarted.'
} catch {
    Write-Status 'failed' $_.Exception.Message
    $env:CHEMCOMPUTE_HOME = $request.home
    if (Test-Path -LiteralPath $request.executable) {
        Start-Process -FilePath $request.executable -ArgumentList @('update-finish',('"'+$RequestPath+'"')) -WindowStyle Hidden -Wait
        if ($backendStopped -or $installerStarted) { Start-Process -FilePath $request.executable -ArgumentList 'desktop-run' -WindowStyle Hidden }
        Start-Process -FilePath $request.executable -ArgumentList 'console' -WindowStyle Hidden
    }
    exit 1
}
