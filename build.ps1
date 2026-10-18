# NPU 翻译 —— 打包脚本（PyInstaller）
# 产物：dist\NPU翻译\  目录（含 GUI exe、服务 exe、模型、配置、文档）
#
# 用法：
#   .\build.ps1            全量构建（GUI + 服务）
#   .\build.ps1 -OnlyGui   只构 GUI（快，做界面迭代时用）
#   .\build.ps1 -OnlySvc   只构服务
param(
    [switch]$OnlyGui,
    [switch]$OnlySvc,
    [switch]$NoClean
)

$ErrorActionPreference = 'Stop'

$Root = $PSScriptRoot
$Py   = Join-Path $Root '.venv\Scripts\python.exe'
$Dist = Join-Path $Root 'dist'
$Work = Join-Path $Root 'build'
$Out  = Join-Path $Dist 'NPU翻译'
$Icon = Join-Path $Root 'app.ico'
# 暂存区：PyInstaller 各自输出到这里，最后由组装步骤搬进 $Out。
# 关键：绝不能让它直接输出到 $Out —— PyInstaller 的 COLLECT 会**清空**目标目录，
# 那样 -OnlyGui 会把已组装好的 服务\ 和 models\ 一起删掉。
$Stage     = Join-Path $Work 'stage'
$StageGui  = Join-Path $Stage 'gui'
$StageSvc  = Join-Path $Stage 'svc'

$env:HF_HOME = Join-Path $Root '.hf-cache'
if (-not $env:HOME) { $env:HOME = $env:USERPROFILE }
$env:OPENVINO_DISABLE_TELEMETRY = '1'

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }

if (-not (Test-Path $Py)) { throw "找不到 venv python: $Py" }

# 构建前必须停掉正在运行的实例：PyInstaller 会清空 dist\NPU翻译\，
# 若服务/界面仍在跑，dist 里的 service.log 等文件被占用，删除会 PermissionError。
Step "停止正在运行的实例"
$stopped = 0
foreach ($n in @('NPU翻译-服务', 'NPU翻译')) {
    Get-Process $n -ErrorAction SilentlyContinue | ForEach-Object {
        try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; $stopped++ } catch { }
    }
}
Get-CimInstance Win32_Process -Filter "Name LIKE 'powershell%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*tray.ps1*' } | ForEach-Object {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $stopped++ } catch { }
    }
if ($stopped -gt 0) {
    Write-Host "  已停止 $stopped 个进程，等待文件句柄释放..."
    Start-Sleep -Seconds 3
} else {
    Write-Host "  没有运行中的实例"
}

if (-not $NoClean) {
    Step "清理旧产物"
    Remove-Item (Join-Path $Root 'build') -Recurse -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path $Dist | Out-Null
New-Item -ItemType Directory -Force -Path $Out  | Out-Null
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

# ------------------------------------------------------------------ GUI
if (-not $OnlySvc) {
    Step "构建 GUI（NPU翻译.exe）"
    $guiArgs = @(
        '--noconfirm', '--clean', '--windowed',
        '--name', 'NPU翻译',
        '--distpath', $StageGui,
        '--workpath', (Join-Path $Work 'gui'),
        '--specpath', (Join-Path $Work 'spec'),
        '--collect-data', 'customtkinter',
        '--hidden-import', 'PIL._tkinter_finder',
        '--add-data', "$Icon;.",
        '--exclude-module', 'openvino',
        '--exclude-module', 'openvino_tokenizers',
        '--exclude-module', 'transformers',
        '--exclude-module', 'tokenizers',
        '--exclude-module', 'torch',
        '--exclude-module', 'onnx',
        '--exclude-module', 'scipy',
        '--exclude-module', 'pandas',
        '--exclude-module', 'matplotlib',
        # 注意：绝不能排除 PIL —— CustomTkinter 依赖 Pillow，排掉会直接启动崩溃
        '--exclude-module', 'tkinter.test',
        '--exclude-module', 'test',
        (Join-Path $Root 'gui.py')
    )
    if (Test-Path $Icon) { $guiArgs = @('--icon', $Icon) + $guiArgs }
    & $Py -m PyInstaller @guiArgs
    if ($LASTEXITCODE -ne 0) { throw "GUI 构建失败 (exit $LASTEXITCODE)" }
    Write-Host "  GUI 完成" -ForegroundColor Green
}

# ------------------------------------------------------------------ 服务
if (-not $OnlyGui) {
    Step "构建服务（NPU翻译-服务.exe）"
    $svcArgs = @(
        '--noconfirm', '--clean', '--console',
        '--name', 'NPU翻译-服务',
        '--distpath', $StageSvc,
        '--workpath', (Join-Path $Work 'svc'),
        '--specpath', (Join-Path $Work 'spec'),
        # openvino：必须带 DLL（NPU 插件）+ 元数据
        '--collect-binaries', 'openvino',
        '--collect-data', 'openvino',
        '--collect-submodules', 'openvino',
        # tokenizer 链路
        '--collect-all', 'tokenizers',
        '--collect-submodules', 'transformers',
        '--collect-data', 'transformers',
        '--hidden-import', 'sentencepiece',
        '--hidden-import', 'transformers.models.marian',
        '--hidden-import', 'transformers.models.marian.tokenization_marian',
        # 明确排除：不参与推理的大件
        '--exclude-module', 'torch',
        '--exclude-module', 'torchvision',
        '--exclude-module', 'torchaudio',
        '--exclude-module', 'onnx',
        '--exclude-module', 'onnxruntime',
        '--exclude-module', 'scipy',
        '--exclude-module', 'pandas',
        '--exclude-module', 'matplotlib',
        '--exclude-module', 'sklearn',
        '--exclude-module', 'tensorflow',
        '--exclude-module', 'jax',
        '--exclude-module', 'paddle',
        '--exclude-module', 'IPython',
        '--exclude-module', 'notebook',
        '--exclude-module', 'tkinter',
        '--exclude-module', 'customtkinter',
        '--exclude-module', 'PIL',
        (Join-Path $Root 'translate_service.py')
    )
    if (Test-Path $Icon) { $svcArgs = @('--icon', $Icon) + $svcArgs }
    & $Py -m PyInstaller @svcArgs
    if ($LASTEXITCODE -ne 0) { throw "服务构建失败 (exit $LASTEXITCODE)" }
    Write-Host "  服务完成" -ForegroundColor Green
}

# ------------------------------------------------------------------ 组装
# 每次都从暂存区重建交付目录：单构 -OnlyGui / -OnlySvc 时也能保持完整交付。
Step "组装运行目录"

# GUI：暂存区是 <stage>\gui\NPU翻译\ ，搬到 <out>\
$guiBuilt = Join-Path $StageGui 'NPU翻译'
if (Test-Path $guiBuilt) {
    # 保留 服务\ 与 models\（它们是装配产物，不在本次 GUI 构建范围内）
    foreach ($n in @('_internal')) {
        $p = Join-Path $Out $n
        if (Test-Path $p) { Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue }
    }
    Copy-Item (Join-Path $guiBuilt '*') $Out -Recurse -Force
    Write-Host "  界面文件已就位"
} elseif (Test-Path (Join-Path $Out 'NPU翻译.exe')) {
    Write-Host "  界面沿用已有产物（本次未构建 GUI）"
} else {
    Warn "没有界面产物，请先跑一次完整构建"
}

# 服务：暂存区 <stage>\svc\NPU翻译-服务\ → <out>\服务\
$svcBuilt = Join-Path $StageSvc 'NPU翻译-服务'
$svcDst   = Join-Path $Out '服务'
if (Test-Path $svcBuilt) {
    if (Test-Path $svcDst) { Remove-Item $svcDst -Recurse -Force -ErrorAction SilentlyContinue }
    Copy-Item $svcBuilt $svcDst -Recurse -Force
    Write-Host "  服务目录已就位: 服务\"
} elseif (Test-Path $svcDst) {
    Write-Host "  服务沿用已有产物（本次未构建服务）"
} else {
    Warn "没有服务产物，请先跑一次完整构建"
}

# 模型：安装包**不再内置模型**（否则体积翻倍）。
# 改为程序首次运行时在「模型」页一键下载（从 GitHub Release 附件取 FP16 IR 包）。
# 这里只建空目录 + 说明文件。
$dstModel = Join-Path $Out 'models'
# 先清空，避免上一次构建残留的模型被打进新安装包
if (Test-Path $dstModel) { Remove-Item $dstModel -Recurse -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path $dstModel | Out-Null
$readme = @'
# 模型目录

安装包不内置模型。首次使用请打开程序 →「模型」页 → 点「下载并安装」。

下载得到的是 OpenVINO IR（FP16），解压后放在本目录下，例如：

    models\opus-mt-zh-en\
        openvino_encoder_model.xml/.bin
        openvino_decoder_model.xml/.bin
        source.spm, target.spm, vocab.json, config.json
        meta.json          （语言方向，如 {"src":"zh","dst":"en"}）

也可以把别处已导出的模型目录整个拷进来，然后点「刷新」。
'@
[System.IO.File]::WriteAllText((Join-Path $dstModel 'README.md'), $readme,
    (New-Object System.Text.UTF8Encoding($false)))
Write-Host "  models\ 目录已创建（不含模型，由程序内下载）"

# 配置与文档
Copy-Item $Icon $Out -Force -ErrorAction SilentlyContinue
foreach ($doc in @('README.md', 'SERVICE.md', 'KISS.md', 'BUILD.md')) {
    $p = Join-Path $Root $doc
    if (Test-Path $p) { Copy-Item $p $Out -Force }
}
Copy-Item (Join-Path $Root 'tray.ps1') $Out -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $Root '启动服务.bat') $Out -Force -ErrorAction SilentlyContinue

# Windows PowerShell 5.1 读 UTF-8 无 BOM 的 .ps1 会按 GBK 解析，中文字符串直接语法错误。
# 交付前统一补 BOM。
foreach ($ps1 in (Get-ChildItem $Out -Filter '*.ps1' -File -ErrorAction SilentlyContinue)) {
    $b = [System.IO.File]::ReadAllBytes($ps1.FullName)
    $bom = ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)
    if (-not $bom) {
        $ms = New-Object System.IO.MemoryStream
        $ms.Write([byte[]](0xEF, 0xBB, 0xBF), 0, 3)
        $ms.Write($b, 0, $b.Length)
        [System.IO.File]::WriteAllBytes($ps1.FullName, $ms.ToArray())
        Write-Host "  已补 BOM: $($ps1.Name)"
    }
}

# config：模型路径写成相对（程序目录可为任意位置），并把默认值补齐
$cfgPath = Join-Path $Out 'config.json'
if (Test-Path $cfgPath) {
    $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
} else {
    $cfg = [pscustomobject]@{}
}
$defaults = [ordered]@{
    device            = 'NPU'
    host              = '127.0.0.1'
    port              = 8715
    http_port         = 8716
    model_dir         = 'models\opus-mt-zh-en'
    enc_seq           = 64
    dec_seq           = 64
    max_new_tokens    = 96
    autostart         = $false
    background_service = $true
    minimize_to_tray  = $true
    autostart_service = $true
    log_level         = 'INFO'
}
foreach ($k in $defaults.Keys) {
    $existing = $cfg.PSObject.Properties[$k]
    if ($null -eq $existing) {
        $cfg | Add-Member -NotePropertyName $k -NotePropertyValue $defaults[$k] -Force
    }
}
# model_dir 强制相对路径：跟着程序目录走，换机器也不怕
$cfg.model_dir = 'models\opus-mt-zh-en'
($cfg | ConvertTo-Json -Depth 6) | Set-Content $cfgPath -Encoding utf8
Write-Host "  已写入 config.json"

# ------------------------------------------------------------------ 裁剪
# PyInstaller 的 --collect-all 会把整个 openvino / transformers 打进来，
# 含大量运行时用不到的东西。这里做无损裁剪（不删任何实际用到的文件）。
Step "裁剪无用依赖"

$svcInt = Join-Path $Out '服务\_internal'
if (Test-Path $svcInt) {
    $before = (Get-ChildItem $svcInt -Recurse -File -EA SilentlyContinue |
               Measure-Object Length -Sum).Sum

    # 1) OpenVINO：只保留 NPU / CPU / IR 前端
    #    删掉 GPU 插件、各语言前端（ONNX/TF/PyTorch/Paddle/GGUF/TFLite/JAX）、
    #    auto/hetero/batch 调度插件 —— 我们固定用 NPU 或 CPU 读 IR 模型。
    $ovLibs = Join-Path $svcInt 'openvino\libs'
    if (Test-Path $ovLibs) {
        $drop = @(
            'openvino_intel_gpu_plugin.dll',
            'openvino_onnx_frontend.dll',
            'openvino_tensorflow_frontend.dll',
            'openvino_tensorflow_lite_frontend.dll',
            'openvino_pytorch_frontend.dll',
            'openvino_paddle_frontend.dll',
            'openvino_gguf_frontend.dll',
            'openvino_jax_frontend.dll',
            'openvino_auto_plugin.dll',
            'openvino_auto_batch_plugin.dll',
            'openvino_hetero_plugin.dll',
            'openvino_c.dll'
        )
        foreach ($f in $drop) {
            $p = Join-Path $ovLibs $f
            if (Test-Path $p) { Remove-Item $p -Force -EA SilentlyContinue }
        }
        # 头文件是给开发用的，运行时不需要
        foreach ($d in @('include', 'cmake', 'tools')) {
            $p = Join-Path (Join-Path $svcInt 'openvino') $d
            if (Test-Path $p) { Remove-Item $p -Recurse -Force -EA SilentlyContinue }
        }
    }

    # 2) transformers：只保留 auto + marian（我们只用 MarianMT 的 tokenizer）
    $tfModels = Join-Path $svcInt 'transformers\models'
    if (Test-Path $tfModels) {
        foreach ($d in (Get-ChildItem $tfModels -Directory -EA SilentlyContinue)) {
            if ($d.Name -notin @('auto', 'marian')) {
                Remove-Item $d.FullName -Recurse -Force -EA SilentlyContinue
            }
        }
        foreach ($f in (Get-ChildItem $tfModels -File -EA SilentlyContinue)) {
            if ($f.Name -ne '__init__.py') { Remove-Item $f.FullName -Force -EA SilentlyContinue }
        }
    }

    # 3) 清掉所有 __pycache__
    Get-ChildItem $svcInt -Recurse -Directory -Filter '__pycache__' -EA SilentlyContinue |
        Remove-Item -Recurse -Force -EA SilentlyContinue

    $after = (Get-ChildItem $svcInt -Recurse -File -EA SilentlyContinue |
              Measure-Object Length -Sum).Sum
    Write-Host ("  服务依赖 {0:N0} MB → {1:N0} MB（省 {2:N0} MB）" -f `
        ($before / 1MB), ($after / 1MB), (($before - $after) / 1MB))
} else {
    Write-Host "  跳过（本次未构建服务，保持已组装内容）"
}

# 界面侧同样清 __pycache__
$guiInt = Join-Path $Out '_internal'
if (Test-Path $guiInt) {
    Get-ChildItem $guiInt -Recurse -Directory -Filter '__pycache__' -EA SilentlyContinue |
        Remove-Item -Recurse -Force -EA SilentlyContinue
}

# ------------------------------------------------------------------ 汇总
Step "完成"
Write-Host "输出目录: $Out" -ForegroundColor Green
if (Test-Path $Out) {
    Get-ChildItem $Out -File | Select-Object Name, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } } |
        Format-Table -AutoSize | Out-String | Write-Host
}

