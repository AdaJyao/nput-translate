# NPU 常驻翻译服务 —— 后台起停管理
# 用法:
#   .\start-service.ps1            后台启动服务
#   .\start-service.ps1 -Status    查看运行状态
#   .\start-service.ps1 -Stop      停止服务
[CmdletBinding(DefaultParameterSetName = 'Start')]
param(
    [Parameter(ParameterSetName = 'Start')][switch]$Start,
    [Parameter(ParameterSetName = 'Status')][switch]$Status,
    [Parameter(ParameterSetName = 'Stop')][switch]$Stop,
    [int]$Port = 8715
)

$ErrorActionPreference = 'Stop'
$Root  = $PSScriptRoot
$Py    = Join-Path $Root '.venv\Scripts\python.exe'
$Svc   = Join-Path $Root 'translate_service.py'
$Log   = Join-Path $Root 'service-daemon.log'
$PidF  = Join-Path $Root 'service.pid'
$Name  = 'python*translate_service*'

function Get-SvcProcess {
    # 注意：.venv\Scripts\python.exe 是入口重定向，会 spawn 真实解释器，
    # 同一服务会呈现为两个 PID（父入口 + 子解释器），属正常现象。
    Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
        Where-Object { $_.CommandLine -like "*translate_service*" } |
        Sort-Object CreationDate
}

if ($Status) {
    $p = Get-SvcProcess
    if ($p) {
        # 取最早创建者为服务主进程（venv 入口重定向会产生成对 PID）
        $main = $p[0]
        "服务运行中: PID $($main.ProcessId)"
        "启动时间  : $($main.CreationDate)"
        if ($p.Count -gt 1) { "关联进程  : PID $($p[1..($p.Count-1)].ProcessId -join ', ')（venv 入口重定向）" }
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $c.Connect('127.0.0.1', $Port)
            "端口 $Port : OPEN"
            $c.Close()
        } catch {
            "端口 $Port : CLOSED（进程在但没监听？）"
        }
    } else {
        "服务未运行"
    }
    exit 0
}

if ($Stop) {
    $p = Get-SvcProcess
    if (-not $p) { "服务未运行"; exit 0 }
    foreach ($proc in $p) {
        Stop-Process -Id $proc.ProcessId -Force
        "已停止 PID $($proc.ProcessId)"
    }
    Remove-Item $PidF -ErrorAction SilentlyContinue
    exit 0
}

# Start
$p = Get-SvcProcess
if ($p) {
    "服务已在运行: PID $($p.ProcessId -join ', ')"
    exit 0
}
if (-not (Test-Path $Py))  { throw "找不到 python: $Py" }
if (-not (Test-Path $Svc)) { throw "找不到服务脚本: $Svc" }

# 环境变量：本机 $env:HOME 为空会导致 HuggingFace 报 PermissionError
# 必须在启动前设好，Start-Process 只继承此刻的环境块
$env:HF_HOME = Join-Path $Root '.hf-cache'
if (-not $env:HOME) { $env:HOME = $env:USERPROFILE }
$env:OPENVINO_DISABLE_TELEMETRY = '1'
Remove-Item env:HF_ENDPOINT -ErrorAction SilentlyContinue

$proc = Start-Process -FilePath $Py -ArgumentList "`"$Svc`"" -WorkingDirectory $Root `
    -WindowStyle Hidden -RedirectStandardOutput $Log -RedirectStandardError "$Log.err" -PassThru
$proc.Id | Set-Content $PidF
"服务已启动: PID $($proc.Id)，日志 $Log"
"等待模型加载与编译（约 15s）..."
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 1
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', $Port)
        $c.Close()
        $ok = $true
        break
    } catch { }
}
if ($ok) { "端口 $Port 已就绪，可以调用了。" }
else {
    "端口 $Port 40s 内未就绪，请查看日志:"
    if (Test-Path $Log)     { Get-Content $Log -Tail 20 }
    if (Test-Path "$Log.err") { Get-Content "$Log.err" -Tail 20 }
    exit 1
}
