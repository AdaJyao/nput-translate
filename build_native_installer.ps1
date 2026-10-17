# 生成原生安装器（不依赖 Inno Setup / 不使用 %TEMP%）
#   前提：已跑过 .\build.ps1，dist\NPU翻译\ 存在
#   产物：dist\NPU安装程序-<版本>.exe
#
# 原理：用 csc.exe 编译一个 WinForms 安装器外壳，然后把整包 zip 直接附加到 exe 末尾，
# 再写 24 字节尾部（offset + length + 魔数）。安装时外壳用 ZipArchive 从自身读取数据，
# 流式解压到用户选择的目录 —— 全程不写临时文件，避开安全软件对安装器自解压的拦截。
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Src  = Join-Path $Root 'dist\NPU翻译'
$Ver  = '1.2.0'

$Csc = @(
    'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe',
    'C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Csc) { throw "找不到 csc.exe（需要 .NET Framework 4.x）" }

if (-not (Test-Path (Join-Path $Src 'NPU翻译.exe'))) {
    throw "找不到 dist\NPU翻译\NPU翻译.exe，请先运行 .\build.ps1"
}

# 停掉正在运行的实例，避免文件占用
Get-Process 'NPU翻译', 'NPU翻译-服务' -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }

$work = Join-Path $Root 'build\native'
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Force -Path $work | Out-Null

# ---------------------------------------------------------------- 1. 编译外壳
$stub = Join-Path $work 'stub.exe'
Write-Host "编译安装器外壳…" -ForegroundColor Cyan
& $Csc /nologo /target:winexe /optimize+ /codepage:65001 `
    "/out:$stub" `
    /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll `
    /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll `
    (Join-Path $Root 'installer_native.cs')
if ($LASTEXITCODE -ne 0) { throw "csc 编译失败 (exit $LASTEXITCODE)" }
Write-Host ("  外壳: {0:N0} KB" -f ((Get-Item $stub).Length / 1KB))

# ---------------------------------------------------------------- 2. 打包 zip
$zip = Join-Path $work 'payload.zip'
$size = (Get-ChildItem $Src -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("压缩数据 {0:N0} MB（可能需要几分钟）…" -f ($size / 1MB)) -ForegroundColor Cyan
Add-Type -AssemblyName System.IO.Compression.FileSystem
if (Test-Path $zip) { Remove-Item $zip -Force }
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $Src, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $false)
Write-Host ("  zip: {0:N0} MB" -f ((Get-Item $zip).Length / 1MB))

# ---------------------------------------------------------------- 3. 拼接
$out = Join-Path $Root "dist\NPU安装程序-$Ver.exe"
if (Test-Path $out) { Remove-Item $out -Force }

$fs = [System.IO.File]::Create($out)
try {
    # 3.1 外壳
    $stubBytes = [System.IO.File]::ReadAllBytes($stub)
    $fs.Write($stubBytes, 0, $stubBytes.Length)
    $offset = $fs.Position

    # 3.2 zip 数据
    $zipStream = [System.IO.File]::OpenRead($zip)
    try { $zipStream.CopyTo($fs, 1MB) } finally { $zipStream.Close() }
    $length = $fs.Position - $offset

    # 3.3 尾部：offset(8) + length(8) + magic(8)
    $fs.Write([BitConverter]::GetBytes([int64]$offset), 0, 8)
    $fs.Write([BitConverter]::GetBytes([int64]$length), 0, 8)
    $fs.Write([System.Text.Encoding]::ASCII.GetBytes('NPUSETUP'), 0, 8)
} finally {
    $fs.Close()
}

Write-Host ""
Write-Host ("完成: {0}" -f $out) -ForegroundColor Green
Write-Host ("大小: {0:N0} MB" -f ((Get-Item $out).Length / 1MB))
Write-Host ("  外壳 {0:N0} KB + 数据 {1:N0} MB" -f ((Get-Item $stub).Length / 1KB), ($length / 1MB))
