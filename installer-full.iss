; NPU 翻译 —— 完整版安装脚本（内置 FP16 模型，装完即用）
; 构建：ISCC.exe installer-full.iss
; 产物：dist\NPU翻译-完整版-<版本>.exe
;
; 与 installer.iss 的区别：
;   - 输出文件名不同（-完整版-）
;   - AppId 不同（可与精简版并存/独立卸载）
;   - 源目录里包含 models\opus-mt-zh-en-ov（FP16，约 212 MB）

#define MyAppName "NPU 翻译"
#define MyAppVersion "1.2.0"
#define MyAppPublisher "AdaJyao"
#define MyAppURL "https://github.com/AdaJyao/npu-translate"
#define MyAppExeName "NPU翻译.exe"
#define SrcDir "dist\NPU翻译"

[Setup]
; 完整版用独立 AppId，避免和精简版互相覆盖
AppId={{7D2A4F51-3C8B-4E62-9A17-5F0E2B6C8D35}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}（完整版）
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest

Compression=lzma2/max
SolidCompression=yes

WizardStyle=modern
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
LicenseFile=LICENSE
CloseApplications=no
RestartApplications=no
DirExistsWarning=auto
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir=dist
OutputBaseFilename=NPU翻译-完整版-{#MyAppVersion}

[Languages]
Name: "chinese"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce
Name: "autostart"; Description: "开机自动启动翻译服务（后台常驻）"; GroupDescription: "附加任务："

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  RunValue = 'NPUTranslate';

procedure KillRunning();
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/IM "NPU翻译.exe" /F /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/IM "NPU翻译-服务.exe" /F /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure TryCreateShortcut(const LnkPath, Target, WorkDir, Desc: String);
var
  Wsh: Variant;
  Lnk: Variant;
begin
  try
    ForceDirectories(ExtractFileDir(LnkPath));
    Wsh := CreateOleObject('WScript.Shell');
    Lnk := Wsh.CreateShortcut(LnkPath);
    Lnk.TargetPath := Target;
    Lnk.WorkingDirectory := WorkDir;
    if Desc <> '' then
      Lnk.Description := Desc;
    Lnk.Save;
    Lnk := Unassigned;
    Wsh := Unassigned;
  except
  end;
end;

procedure CreateShortcuts();
var
  Exe, WorkDir, StartMenuDir: String;
begin
  Exe := ExpandConstant('{app}\{#MyAppExeName}');
  WorkDir := ExpandConstant('{app}');
  StartMenuDir := ExpandConstant('{userprograms}\{#MyAppName}');
  TryCreateShortcut(StartMenuDir + '\{#MyAppName}.lnk', Exe, WorkDir, '{#MyAppName}');
  TryCreateShortcut(StartMenuDir + '\打开模型目录.lnk',
    ExpandConstant('{app}\models'), WorkDir, '模型目录');
  TryCreateShortcut(StartMenuDir + '\卸载 {#MyAppName}.lnk',
    ExpandConstant('{uninstallexe}'), WorkDir, '卸载');
  if WizardIsTaskSelected('desktopicon') then
    TryCreateShortcut(ExpandConstant('{userdesktop}\{#MyAppName}.lnk'),
      Exe, WorkDir, '{#MyAppName}');
end;

procedure TrySetAutostart();
var
  Cmd: String;
begin
  if not WizardIsTaskSelected('autostart') then
    exit;
  Cmd := '"' + ExpandConstant('{app}\启动服务.bat') + '"';
  try
    RegWriteStringValue(HKEY_CURRENT_USER, RunKey, RunValue, Cmd);
  except
  end;
end;

procedure CleanupShortcuts();
var
  StartMenuDir, DesktopLnk: String;
begin
  StartMenuDir := ExpandConstant('{userprograms}\{#MyAppName}');
  DelTree(StartMenuDir, True, True, True);
  DesktopLnk := ExpandConstant('{userdesktop}\{#MyAppName}.lnk');
  if FileExists(DesktopLnk) then
    DeleteFile(DesktopLnk);
  try
    RegDeleteValue(HKEY_CURRENT_USER, RunKey, RunValue);
  except
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillRunning();
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    CreateShortcuts();
    TrySetAutostart();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    KillRunning();
    CleanupShortcuts();
  end;
end;
