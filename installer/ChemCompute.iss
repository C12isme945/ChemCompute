#define AppVersion "0.4.0"
[Setup]
AppId={code:GetAppId}
AppName=ChemCompute
AppVersion={#AppVersion}
AppPublisher=ChemCompute Contributors
DefaultDirName={localappdata}\Programs\ChemCompute
DefaultGroupName=ChemCompute
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=ChemCompute-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
CloseApplications=yes
SetupLogging=no
UsePreviousLanguage=no

[Files]
Source: "..\dist\ChemCompute\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "launch.vbs"; DestDir: "{app}"
Source: "launch-console.vbs"; DestDir: "{app}"
Source: "..\scripts\network.ps1"; DestDir: "{app}\scripts"
Source: "..\scripts\dependencies.ps1"; DestDir: "{app}\scripts"
Source: "..\README.md"; DestDir: "{app}"
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: recursesubdirs createallsubdirs
Source: "..\examples\water-smoke\*"; DestDir: "{app}\examples\water-smoke"

[Tasks]
Name: wsl; Description: "Install WSL / Ubuntu if missing (administrator permission; restart may be required)"
Name: gromacs; Description: "Install GROMACS in WSL if missing (repository CPU build)"
Name: drivers; Description: "Install matching display driver updates from Windows Update"
Name: startup; Description: "Start ChemCompute in background when I sign in"; Flags: unchecked

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: ChemCompute; ValueData: "wscript.exe ""{app}\launch.vbs"""; Tasks: startup; Flags: uninsdeletevalue

[Icons]
Name: "{userdesktop}\{code:GetShortcutName}"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\launch-console.vbs"""; WorkingDir: "{app}"; Comment: "ChemCompute desktop control console"
Name: "{group}\ChemCompute Console"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\launch-console.vbs"""; WorkingDir: "{app}"
Name: "{group}\Start ChemCompute"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\launch.vbs"""
Name: "{group}\Web Console"; Filename: "http://127.0.0.1:8000"
Name: "{group}\Configuration and logs"; Filename: "{localappdata}\ChemComputeData"
Name: "{group}\Uninstall ChemCompute"; Filename: "{uninstallexe}"

[Run]
Filename: "{sys}\wscript.exe"; Parameters: """{app}\launch-console.vbs"""; Description: "Open ChemCompute desktop console"; Flags: postinstall skipifsilent nowait

[Code]
var
  RolePage: TInputOptionWizardPage;
  NodePage: TInputQueryWizardPage;
  HostPage: TInputQueryWizardPage;

function GetAppId(Param: String): String;
begin
  Result := '{58D6F106-68F0-48A8-B1F3-D40716888B80}';
  if ExpandConstant('{param:TESTINSTALL|0}') = '1' then Result := Result + '-SmokeTest';
end;

function GetShortcutName(Param: String): String;
begin
  Result := 'ChemCompute Console';
  if ExpandConstant('{param:TESTINSTALL|0}') = '1' then Result := Result + ' Test';
end;

procedure InitializeWizard;
begin
  RolePage := CreateInputOptionPage(wpSelectDir, 'Machine role', 'Choose the role for this computer', 'Controller manages nodes. Both also registers this computer as a local node.', True, False);
  RolePage.Add('Controller');
  RolePage.Add('Compute node');
  RolePage.Add('Controller and compute node');
  RolePage.SelectedValueIndex := 2;
  HostPage := CreateInputQueryPage(RolePage.ID, 'Controller address', 'Listen address', 'Keep 127.0.0.1 for local use. Enter this machine''s Tailscale IP for remote nodes. No firewall ports are opened automatically.');
  HostPage.Add('Listen IP:', False);
  HostPage.Values[0] := '127.0.0.1';
  NodePage := CreateInputQueryPage(HostPage.ID, 'Join controller', 'Node enrollment', 'For compute-node role, enter the controller URL and one-time invite. For Both, enrollment is automatic.');
  NodePage.Add('Controller URL:', False);
  NodePage.Add('One-time invite:', True);
  NodePage.Add('Node name:', False);
  NodePage.Values[0] := 'https://chemcompute.666945726.xyz';
  NodePage.Values[2] := GetComputerNameString;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = NodePage.ID) and (RolePage.SelectedValueIndex = 1) and (Trim(NodePage.Values[1]) = '') then begin
    MsgBox('A one-time invite is required for a compute node.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  IniPath, Role, DependencyArgs: String;
  ExitCode: Integer;
begin
  if CurStep = ssPostInstall then begin
    { Online updates preserve role, enrollment, dependencies and existing runtime. }
    if ExpandConstant('{param:UPDATE|0}') = '1' then Exit;
    Role := 'both';
    if RolePage.SelectedValueIndex = 0 then Role := 'controller';
    if RolePage.SelectedValueIndex = 1 then Role := 'node';
    IniPath := ExpandConstant('{tmp}\chemcompute-onboard.ini');
    SetIniString('setup', 'role', Role, IniPath);
    SetIniString('setup', 'host', HostPage.Values[0], IniPath);
    SetIniString('setup', 'url', NodePage.Values[0], IniPath);
    SetIniString('setup', 'invite', NodePage.Values[1], IniPath);
    SetIniString('setup', 'name', NodePage.Values[2], IniPath);
    if not Exec(ExpandConstant('{app}\ChemCompute.exe'), 'onboard "' + IniPath + '"', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then ExitCode := 1;
    DeleteFile(IniPath);
    if ExitCode <> 0 then RaiseException('Configuration failed. See README for manual setup.');
    if ExpandConstant('{param:TESTINSTALL|0}') <> '1' then begin
      DependencyArgs := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\scripts\dependencies.ps1') + '"';
      if WizardIsTaskSelected('wsl') then DependencyArgs := DependencyArgs + ' -WSL';
      if WizardIsTaskSelected('gromacs') then DependencyArgs := DependencyArgs + ' -Gromacs';
      if WizardIsTaskSelected('drivers') then DependencyArgs := DependencyArgs + ' -Drivers';
      Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), DependencyArgs, '', SW_HIDE, ewNoWait, ExitCode);
    end;
  end;
end;
