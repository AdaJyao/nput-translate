# 创建运行环境（首次使用跑一次）
#   .\setup.ps1              只装运行时依赖
#   .\setup.ps1 -WithBuild   额外装打包工具（pyinstaller）
param(
    [switch]$WithBuild
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Venv = Join-Path $Root '.venv'
$Py   = Join-Path $Venv 'Scripts\python.exe'

Write-Host "=== 创建虚拟环境 ===" -ForegroundColor Cyan
if (-not (Test-Path $Py)) {
    # 优先用 uv（快），没有就用标准 venv
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv venv $Venv --python 3.11
    } else {
        python -m venv $Venv
    }
}
if (-not (Test-Path $Py)) { throw "虚拟环境创建失败：找不到 $Py" }
& $Py --version

Write-Host "`n=== 安装运行时依赖 ===" -ForegroundColor Cyan
# openvino 必须 >= 2025.4（NPU 插件 + optimum-intel 1.27 的要求）
$pkgs = @(
    'openvino>=2025.4.0',
    'transformers==4.57.6',
    'tokenizers==0.22.2',
    'sentencepiece',
    'numpy',
    'customtkinter',
    'pillow'
)
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv pip install --python $Py @pkgs
} else {
    & $Py -m pip install @pkgs
}

if ($WithBuild) {
    Write-Host "`n=== 安装打包工具 ===" -ForegroundColor Cyan
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv pip install --python $Py pyinstaller
    } else {
        & $Py -m pip install pyinstaller
    }
}

Write-Host "`n=== 检查 ===" -ForegroundColor Cyan
& $Py -c "import openvino as ov; print('openvino', ov.__version__); print('devices', ov.Core().available_devices)"
& $Py -c "import transformers, sentencepiece, customtkinter; print('transformers', transformers.__version__); print('customtkinter', customtkinter.__version__)"

Write-Host "`n完成。下一步：" -ForegroundColor Green
Write-Host "  .\export_model.ps1     # 导出 OpenVINO 模型（约 445 MB）"
Write-Host "  .\start-service.ps1    # 起后台服务"
