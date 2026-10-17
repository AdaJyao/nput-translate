; NPU 翻译 —— 简体中文语言包（精简版）
; Inno Setup 对未在此定义的条目会回退到英文默认值，
; 因此这里只翻译向导中最常看到的文字。

[LangOptions]
LanguageName=简体中文
LanguageID=$0804
LanguageCodePage=936
DialogFontName=Microsoft YaHei UI
DialogFontSize=9
WelcomeFontName=Microsoft YaHei UI
WelcomeFontSize=12
TitleFontName=Microsoft YaHei UI
TitleFontSize=29
CopyrightFontName=Microsoft YaHei UI
CopyrightFontSize=8

[Messages]
SetupAppTitle=安装程序
SetupWindowTitle=安装 - %1
UninstallAppTitle=卸载
UninstallAppFullTitle=卸载 %1
SetupLdrStartupMessage=即将安装 %1。是否继续？
WelcomeLabel1=欢迎使用 [name] 安装向导
WelcomeLabel2=这将安装 [name/ver] 到你的电脑。%n%n建议关闭其他应用程序后继续。
WizardNext=下一步(&N) >
WizardBack=< 上一步(&B)
WizardCancel=取消
WizardClose=关闭(&C)
WizardBrowse=浏览(&R)...
WizardLicense=许可协议
WizardSelectDir=选择目标位置
WizardSelectProgramGroup=选择开始菜单文件夹
WizardSelectTasks=选择附加任务
WizardReady=准备安装
WizardInstalling=正在安装
WizardFinished=完成安装
LicenseLabel1=请阅读以下许可协议。
SelectDirDesc=将安装到哪个文件夹？
SelectDirLabel3=安装程序会把 [name] 安装到以下文件夹。
SelectDirBrowseLabel=点击「下一步」继续。如需更改文件夹，请点击「浏览」。
SelectDirNeedAdmin=你当前不是管理员，安装到该文件夹可能需要管理员权限。
DiskSpaceGBLabel=至少需要 [gb] GB 可用磁盘空间。
DiskSpaceMBLabel=至少需要 [mb] MB 可用磁盘空间。
SelectTasksDesc=需要执行哪些附加任务？
SelectTasksLabel2=请选择安装 [name] 时要执行的附加任务，然后点击「下一步」。
ReadyLabel1=准备开始安装 [name]。
ReadyLabel2a=点击「安装」开始安装，或点击「上一步」检查或修改设置。
ReadyLabel2b=点击「安装」开始安装。
ReadyMemoDir=目标位置：
ReadyMemoTasks=附加任务：
InstallingLabel=正在安装 [name]，请稍候...
FinishedHeadingLabel=[name] 安装完成
FinishedLabelNoIcons=安装程序已完成 [name] 的安装。
FinishedLabel=安装程序已完成 [name] 的安装。
ClickFinish=点击「完成」退出安装程序。
FinishedRestartLabel=需要重启电脑才能完成 [name] 的安装。现在重启吗？
FinishedRestartNote1=
RunEntryExec=运行 %1
RunEntryShellExec=打开 %1
ButtonInstall=安装(&I)
ButtonOK=确定
ButtonCancel=取消
ButtonYes=是(&Y)
ButtonNo=否(&N)
ButtonFinish=完成(&F)
ExitSetupMessage=安装尚未完成。现在退出将不会安装 [name]。%n%n确定要退出吗？
ConfirmUninstall=确定要完全卸载 %1 及其组件吗？
UninstalledAll=%1 已成功卸载。
UninstallAppRunningError=无法卸载，因为 %1 正在运行。请先退出后再试。
ErrorCreatingDir=创建文件夹「%1」失败，请检查是否有写入权限。
ErrorTooManyFiles=文件夹「%1」中的文件过多。
SetupFileCorrupt=安装文件损坏，请重新下载安装程序。
SetupFileCorruptOrWrongVer=安装文件损坏或版本不匹配，请重新下载安装程序。
InvalidParameter=命令行参数无效：%n%n%1
