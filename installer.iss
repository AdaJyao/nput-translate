; NPU 翻译 —— Inno Setup 安装脚本
; 构建：ISCC.exe installer.iss
; 产物：dist\NPU翻译-安装程序-<版本>.exe

#define MyAppName "NPU 翻译"
#define MyAppVersion "1.1.0"
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
; 注意：不要开 LZMAUseSeparateProcess —— 它会额外派生一个自身进程做压缩解压，
; 在受限环境里会导致安装器启动即退出（Inno 退出码 1）。

WizardStyle=modern
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
LicenseFile=LICENSE
; 关闭正在运行的实例再覆盖文件。
; 不用 Inno 的 Restart Manager（CloseApplications），改用 [Code] 里的 taskkill，
; 因为本程序有两个进程（界面 + 服务），Restart Manager 处理不了服务，且部分环境会报致命错误。
CloseApplications=no
RestartApplications=no
; 允许用户改安装目录（默认就允许，这里显式声明）
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

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\打开模型目录"; Filename: "{app}\models"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; 只在勾选时写开机自启；卸载时自动清除
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "NPUTranslate"; \
    ValueData: """{app}\启动服务.bat"""; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 运行时产生的文件（配置、日志、后来下载的模型）安装器本来不会删，这里一并清掉
Type: filesandordirs; Name: "{app}"

[Code]
{ 安装/卸载前先结束正在运行的实例，避免文件占用导致覆盖失败 }
procedure KillRunning();
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/IM "NPU翻译.exe" /F /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/IM "NPU翻译-服务.exe" /F /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillRunning();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    KillRunning();
end;
