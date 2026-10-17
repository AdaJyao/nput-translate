; NPU 翻译 —— Inno Setup 安装脚本
; 构建：ISCC.exe installer.iss
; 产物：dist\NPU翻译-安装程序-<版本>.exe

#define MyAppName "NPU 翻译"
#define MyAppVersion "1.2.0"
#define MyAppPublisher "AdaJyao"
#define MyAppURL "https://github.com/AdaJyao/npu-translate"
#define MyAppExeName "NPU翻译.exe"
#define SrcDir "dist\NPU翻译"

[Setup]
; AppId 必须稳定不变，否则升级会装成两个程序
AppId={{7D2A4F51-3C8B-4E62-9A17-5F0E2B6C8D34}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; 默认装到用户目录，无需管理员；安装时仍可选择其他目录
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest

; 体积大，用最高压缩
Compression=lzma2/max
SolidCompression=yes
; 不要开 LZMAUseSeparateProcess —— 会额外派生进程，受限环境下会导致安装器启动失败

WizardStyle=modern
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
LicenseFile=LICENSE
; 不用 Restart Manager：本程序有界面+服务两个进程，且部分环境会报致命错误。
; 改为 [Code] 里 taskkill。
CloseApplications=no
RestartApplications=no
DirExistsWarning=auto
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir=dist
OutputBaseFilename=NPU翻译-安装程序-{#MyAppVersion}

[Languages]
; 精简中文语言包随项目提供（Inno 对未定义条目自动回退英文）
Name: "chinese"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce
Name: "autostart"; Description: "开机自动启动翻译服务（后台常驻）"; GroupDescription: "附加任务："

[Files]
; 整个运行目录（界面 + 服务 + 模型 + 脚本）
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 运行时产生的文件（配置、日志、后来下载的模型）安装器本来不会删，这里一并清掉
Type: filesandordirs; Name: "{app}"

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  RunValue = 'NPUTranslate';

{ 安装/卸载前结束正在运行的实例，避免文件占用 }
procedure KillRunning();
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/IM "NPU翻译.exe" /F /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/IM "NPU翻译-服务.exe" /F /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

{ 用 WScript.Shell 建快捷方式；失败只记录，不中断安装 }
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
    { 受限环境（无 AppData 写权限）下忽略，安装继续 }
  end;
end;

{ 建立开始菜单 + 桌面快捷方式 }
procedure CreateShortcuts();
var
  Exe, WorkDir, StartMenuDir, Lnk: String;
begin
  Exe := ExpandConstant('{app}\{#MyAppExeName}');
  WorkDir := ExpandConstant('{app}');

  StartMenuDir := ExpandConstant('{userprograms}\{#MyAppName}');
  Lnk := StartMenuDir + '\{#MyAppName}.lnk';
  TryCreateShortcut(Lnk, Exe, WorkDir, '{#MyAppName}');
  TryCreateShortcut(StartMenuDir + '\打开模型目录.lnk',
    ExpandConstant('{app}\models'), WorkDir, '模型目录');
  TryCreateShortcut(StartMenuDir + '\卸载 {#MyAppName}.lnk',
    ExpandConstant('{uninstallexe}'), WorkDir, '卸载');

  if WizardIsTaskSelected('desktopicon') then
    TryCreateShortcut(ExpandConstant('{userdesktop}\{#MyAppName}.lnk'),
      Exe, WorkDir, '{#MyAppName}');
end;

{ 开机自启：写 HKCU Run；失败只记录 }
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

{ 清理快捷方式与自启项（卸载时） }
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

