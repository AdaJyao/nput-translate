# 导出 OpenVINO 模型：Helsinki-NLP/opus-mt-zh-en → models\opus-mt-zh-en-ov
#
# 为什么单独一步：模型约 445 MB，不适合放进 git 仓库，需要本地导出。
# 需要先跑过 .\setup.ps1，并额外安装导出依赖：optimum-intel + torch
#
#   .\export_model.ps1
param(
    [string]$OutDir = (Join-Path $PSScriptRoot 'models\opus-mt-zh-en-ov'),
    [string]$Model  = 'Helsinki-NLP/opus-mt-zh-en'
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Py   = Join-Path $Root '.venv\Scripts\python.exe'
$OptimumCli = Join-Path $Root '.venv\Scripts\optimum-cli.exe'

# HuggingFace 环境：本机 $env:HOME 可能为空，会导致缓存目录 PermissionError
$env:HF_HOME = Join-Path $Root '.hf-cache'
if (-not $env:HOME) { $env:HOME = $env:USERPROFILE }
$env:OPENVINO_DISABLE_TELEMETRY = '1'
Remove-Item env:HF_ENDPOINT -ErrorAction SilentlyContinue   # 不要用镜像源

if (-not (Test-Path $Py)) { throw "先跑 .\setup.ps1 创建虚拟环境" }

Write-Host "=== 安装导出依赖（optimum-intel / torch）===" -ForegroundColor Cyan
$deps = @('optimum-intel>=1.27.0', 'optimum', 'torch', 'sacremoses')
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv pip install --python $Py @deps
} else {
    & $Py -m pip install @deps
}

if (-not (Test-Path $OptimumCli)) {
    throw "找不到 optimum-cli：$OptimumCli"
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Host "`n=== 导出到 $OutDir ===" -ForegroundColor Cyan
Write-Host "  注意：--disable-stateful 用于尽量减小带缓存模型，但 optimum 仍会生成"
Write-Host "  openvino_decoder_with_past_model.*；本项目不用它（NPU 无法编译其 int64-max 缓存维度），"
Write-Host "  导出完可以删掉以省 223 MB。"

# 输出目录是位置参数，必须放在最后
& $OptimumCli export openvino `
    --model $Model `
    --task text2text-generation `
    --disable-stateful `
    $OutDir

if ($LASTEXITCODE -ne 0) { throw "导出失败 (exit $LASTEXITCODE)" }

Write-Host "`n=== 校验 ===" -ForegroundColor Cyan
$need = @(
    'openvino_encoder_model.xml', 'openvino_encoder_model.bin',
    'openvino_decoder_model.xml', 'openvino_decoder_model.bin',
    'source.spm', 'target.spm', 'vocab.json', 'config.json'
)
$missing = @()
foreach ($f in $need) {
    if (-not (Test-Path (Join-Path $OutDir $f))) { $missing += $f }
}
if ($missing.Count -gt 0) {
    Write-Host "  缺少文件: $($missing -join ', ')" -ForegroundColor Yellow
    throw "导出结果不完整"
}

$sz = (Get-ChildItem $OutDir -File | Measure-Object Length -Sum).Sum
Write-Host ("  导出完整，共 {0:N0} MB" -f ($sz / 1MB)) -ForegroundColor Green

# 可选：删掉不用的 decoder_with_past（省 223 MB）
$dwp = Join-Path $OutDir 'openvino_decoder_with_past_model.xml'
if (Test-Path $dwp) {
    Write-Host "`n检测到 openvino_decoder_with_past_model.*（本项目不使用）"
    $ans = Read-Host "  删除它以节省约 223 MB？[y/N]"
    if ($ans -match '^[Yy]') {
        Remove-Item (Join-Path $OutDir 'openvino_decoder_with_past_model.*') -Force -EA SilentlyContinue
        Write-Host "  已删除" -ForegroundColor Green
    }
}

Write-Host "`n下一步：.\start-service.ps1 然后 .\client.py 测试" -ForegroundColor Green
