# 生成 Windows 安装程序（Inno Setup）
#   前提：已跑过 .\build.ps1，dist\NPU翻译\ 存在
#   产物：dist\NPU翻译-安装程序-<版本>.exe
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Src  = Join-Path $Root 'dist\NPU翻译'
$Iss  = Join-Path $Root 'installer.iss'

$Iscc = @(
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $Iscc) {
    throw "找不到 Inno Setup 6 的 ISCC.exe。请先安装：winget install JRSoftware.InnoSetup"
}
if (-not (Test-Path (Join-Path $Src 'NPU翻译.exe'))) {
    throw "找不到 dist\NPU翻译\NPU翻译.exe，请先运行 .\build.ps1"
}
if (-not (Test-Path (Join-Path $Src 'models\opus-mt-zh-en-ov\openvino_encoder_model.xml'))) {
    throw "dist 里没有模型文件，安装包会不可用。请先运行 .\build.ps1"
}

# 安装器会覆盖目标程序，先停掉正在运行的实例
Get-Process 'NPU翻译', 'NPU翻译-服务' -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process -Filter "Name LIKE 'powershell%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*tray.ps1*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$size = (Get-ChildItem $Src -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("打包源目录: {0:N0} MB" -f ($size / 1MB))
Write-Host "压缩中（体积大，可能需要几分钟）..." -ForegroundColor Cyan
$t0 = Get-Date
& $Iscc $Iss
if ($LASTEXITCODE -ne 0) { throw "ISCC 编译失败 (exit $LASTEXITCODE)" }

$exe = Get-ChildItem (Join-Path $Root 'dist') -Filter 'NPU翻译-安装程序-*.exe' |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
$dt = (Get-Date) - $t0
Write-Host ""
Write-Host ("完成，用时 {0:N0} 秒" -f $dt.TotalSeconds) -ForegroundColor Green
Write-Host ("安装包: {0}" -f $exe.FullName) -ForegroundColor Green
Write-Host ("大小  : {0:N0} MB" -f ($exe.Length / 1MB))
