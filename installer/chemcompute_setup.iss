; Inno Setup Script for ChemCompute Node
; Compatible with Inno Setup 6.x and 7.x

#define MyAppName "ChemCompute Node"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "ChemCompute Distributed Grid"
#define MyAppExeName "ChemComputeAgent.exe"

[Setup]
AppId={{D81561BE-38D5-4CE3-B9E4-ChemCompute01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\ChemCompute
DefaultGroupName=ChemCompute
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=ChemCompute-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[Files]
; Dist directory containing ChemComputeAgent executable & python environment
Source: "..\dist\ChemComputeAgent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ChemCompute Agent"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--controller ""{code:GetControllerUrl}"" --invite ""{code:GetInviteCode}"" --name ""{code:GetNodeName}"""; Description: "启动 ChemCompute 计算节点"; Flags: nowait postinstall skipifsilent

[Code]
var
  ConfigPage: TInputQueryWizardPage;
  ControllerUrlInput: String;
  InviteCodeInput: String;
  NodeNameInput: String;

procedure InitializeWizard;
begin
  ConfigPage := CreateInputQueryPage(wpSelectDir,
    '加入 ChemCompute 计算集群',
    '请输入主控端分配的信息',
    '将这台电脑接入您的私人科研算力池：');

  ConfigPage.Add('主控端地址 (Controller URL):', False);
  ConfigPage.Add('集群邀请码 (Invite Code, 如 CC-7F4A9K):', False);
  ConfigPage.Add('节点名称 (Node Display Name):', False);

  ConfigPage.Values[0] := 'http://127.0.0.1:8000';
  ConfigPage.Values[1] := 'CC-';
  ConfigPage.Values[2] := GetComputerNameString;
end;

function GetControllerUrl(Param: String): String;
begin
  Result := ConfigPage.Values[0];
end;

function GetInviteCode(Param: String): String;
begin
  Result := ConfigPage.Values[1];
end;

function GetNodeName(Param: String): String;
begin
  Result := ConfigPage.Values[2];
end;
