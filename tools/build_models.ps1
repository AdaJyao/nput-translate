# 批量导出 opus-mt 模型为 OpenVINO IR（FP16），产出 dist 下的模型下载包
#   每个模型：optimum-cli 导出 -> FP16 压缩 -> 写 meta.json -> 打 zip
#   产物：dist\<name>-fp16.zip
$ErrorActionPreference = 'Continue'
# 本脚本在 tools\ 下，项目根目录是它的上一级
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root '.venv\Scripts\python.exe'))) {
    # 兜底：如果被移动到项目根目录执行
    if (Test-Path (Join-Path $PSScriptRoot '.venv\Scripts\python.exe')) { $Root = $PSScriptRoot }
    else { throw "找不到 .venv（Root=$Root）" }
}
$Py    = Join-Path $Root '.venv\Scripts\python.exe'
$Opt   = Join-Path $Root '.venv\Scripts\optimum-cli.exe'
$Work  = Join-Path $Root 'build\models'
$Dist  = Join-Path $Root 'dist'

# 模型清单：hf 仓库 -> 精简名 / 语言方向
$Models = @(
    @{ Repo='Helsinki-NLP/opus-mt-en-zh'; Name='opus-mt-en-zh'; Src='en'; Dst='zh' },
    @{ Repo='Helsinki-NLP/opus-mt-ja-en'; Name='opus-mt-ja-en'; Src='ja'; Dst='en' },
    @{ Repo='Helsinki-NLP/opus-mt-en-de'; Name='opus-mt-en-de'; Src='en'; Dst='de' },
    @{ Repo='Helsinki-NLP/opus-mt-de-en'; Name='opus-mt-de-en'; Src='de'; Dst='en' },
    @{ Repo='Helsinki-NLP/opus-mt-en-fr'; Name='opus-mt-en-fr'; Src='en'; Dst='fr' },
    @{ Repo='Helsinki-NLP/opus-mt-en-ru'; Name='opus-mt-en-ru'; Src='en'; Dst='ru' }
)

$env:HF_HOME = Join-Path $Root '.hf-cache'
$env:HOME = $env:USERPROFILE
$env:OPENVINO_DISABLE_TELEMETRY = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
Remove-Item env:HF_ENDPOINT -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Force -Path $Work | Out-Null

foreach ($m in $Models) {
    $out = Join-Path $Work $m.Name
    $zip = Join-Path $Dist ($m.Name + '-fp16.zip')
    Write-Host ""
    Write-Host ("=== {0}  ({1} -> {2}) ===" -f $m.Name, $m.Src, $m.Dst) -ForegroundColor Cyan

    if (Test-Path $zip) { Write-Host "  已存在，跳过"; continue }

    if (Test-Path $out) { Remove-Item $out -Recurse -Force }
    # 注意：不要用 `| Select-Object -First N` 过滤子进程输出 —— PowerShell 会提前关闭管道，
    # 导致 optimum-cli 被判为"输出被切断"而中断导出（表现为导出静默失败）。
    # 先把输出收进变量，再筛选。
    $log = & $Opt export openvino --model $m.Repo --task text2text-generation --disable-stateful $out 2>&1
    $errs = $log | Where-Object { $_ -match 'Traceback|Error:|error:' }
    if ($errs) { Write-Host ("  错误: " + ($errs | Select-Object -First 2)) -ForegroundColor Red }
    if (-not (Test-Path (Join-Path $out 'openvino_encoder_model.xml'))) {
        Write-Host "  ❌ 导出失败，跳过" -ForegroundColor Red
        continue
    }

    # 删掉解码缓存版（我们用无缓存解码，省一半体积）
    Get-ChildItem $out -Filter 'openvino_decoder_with_past_model.*' -ErrorAction SilentlyContinue |
        Remove-Item -Force

    # FP16 压缩
    $clog = & $Py (Join-Path $Root 'tools\compress_model.py') $out 2>&1
    $clog | Where-Object { $_ -match '权重|MB' } | ForEach-Object { "  $_" }

    # meta.json（精简模型名 + 语言方向 + 精度）
    $meta = [ordered]@{ src=$m.Src; dst=$m.Dst; label=$m.Name; precision='FP16' }
    $json = $meta | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText((Join-Path $out 'meta.json'), $json,
        (New-Object System.Text.UTF8Encoding($false)))

    $size = (Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum
    Write-Host ("  目录 {0:N0} MB，压缩中…" -f ($size / 1MB))
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path (Join-Path $out '*') -DestinationPath $zip -CompressionLevel Optimal
    Write-Host ("  ✅ {0}  {1:N0} MB" -f (Split-Path $zip -Leaf), ((Get-Item $zip).Length / 1MB)) -ForegroundColor Green
}

Write-Host ""
Write-Host "=== 产物 ===" -ForegroundColor Green
Get-ChildItem $Dist -Filter '*-fp16.zip' | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB)}} |
    Format-Table -AutoSize | Out-String


