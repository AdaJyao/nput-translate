# NPU 翻译 —— 系统托盘控制（纯 PowerShell + WinForms，零编译）
# 用法：powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File tray.ps1
#
# 踩过的两个坑：
#  1) 启动 GUI **不能**加 -WindowStyle Hidden，否则主窗口会被一起隐藏。
#  2) PowerShell 函数 `return @($path)` 会被**展开成字符串**，调用方 `$cmd[0]`
#     于是取到首字符 'H'，Start-Process 报「系统找不到指定的文件」。
#     所以这里用 [pscustomobject] 返回 {Exe, Args}，绝不用数组。

$here = Split-Path -Parent $MyInvocation.MyCommand.Path

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$Port = 8716

function Test-HttpUp {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', $Port)
        $c.Close()
        return $true
    } catch { return $false }
}

function Find-Exe([string]$name) {
    $p = Join-Path $here $name
    if (Test-Path -LiteralPath $p) { return $p }
    return $null
}

function Get-GuiCommand {
    $exe = Find-Exe 'NPU翻译.exe'
    if ($exe) { return [pscustomobject]@{ Exe = $exe; Args = @() } }

    $pyw = Join-Path $here '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath $pyw)) {
        $pyw = Join-Path $here '.venv\Scripts\python.exe'
    }
    if (Test-Path -LiteralPath $pyw) {
        return [pscustomobject]@{ Exe = $pyw; Args = @((Join-Path $here 'gui.py')) }
    }
    return $null
}

function Get-ServiceCommand {
    $exe = Find-Exe 'NPU翻译-服务.exe'
    if (-not $exe) { $exe = Find-Exe '服务\NPU翻译-服务.exe' }
    if ($exe) { return [pscustomobject]@{ Exe = $exe; Args = @() } }

    $py = Join-Path $here '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $py) {
        return [pscustomobject]@{ Exe = $py; Args = @((Join-Path $here 'translate_service.py')) }
    }
    return $null
}

function Start-Gui {
    $cmd = Get-GuiCommand
    if (-not $cmd) {
        [System.Windows.Forms.MessageBox]::Show(
            "找不到界面程序，请确认 NPU翻译.exe 与本文件在同一目录。", "NPU 翻译") | Out-Null
        return
    }
    try {
        if ($cmd.Args.Count -gt 0) {
            Start-Process -FilePath $cmd.Exe -ArgumentList $cmd.Args -WorkingDirectory $here
        } else {
            Start-Process -FilePath $cmd.Exe -WorkingDirectory $here
        }
    } catch {
        [System.Windows.Forms.MessageBox]::Show(
            "启动界面失败：$($_.Exception.Message)", "NPU 翻译") | Out-Null
    }
}

function Start-ServiceProc {
    $cmd = Get-ServiceCommand
    if (-not $cmd) {
        [System.Windows.Forms.MessageBox]::Show(
            "找不到服务程序，请确认「服务」文件夹完整。", "NPU 翻译") | Out-Null
        return
    }
    try {
        if ($cmd.Args.Count -gt 0) {
            Start-Process -FilePath $cmd.Exe -ArgumentList $cmd.Args -WorkingDirectory $here -WindowStyle Hidden
        } else {
            Start-Process -FilePath $cmd.Exe -WorkingDirectory $here -WindowStyle Hidden
        }
    } catch { }
}

function Stop-ServiceProc {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$Port/shutdown" -Method POST -Body '{}' `
            -ContentType 'application/json' -UseBasicParsing -TimeoutSec 3 | Out-Null
    } catch { }
}

# ---------------------------------------------------------------- 托盘图标
$iconPath = Join-Path $here 'app.ico'
if (-not (Test-Path -LiteralPath $iconPath)) {
    $tmp = Join-Path $env:TEMP 'npu-translate-tray.ico'
    try { [System.Drawing.SystemIcons]::Application.Save($tmp); $iconPath = $tmp }
    catch { $iconPath = $null }
}

$ni = New-Object System.Windows.Forms.NotifyIcon
if ($iconPath) {
    try { $ni.Icon = New-Object System.Drawing.Icon($iconPath) }
    catch { $ni.Icon = [System.Drawing.SystemIcons]::Application }
} else {
    $ni.Icon = [System.Drawing.SystemIcons]::Application
}
$ni.Text = "NPU 翻译"
$ni.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip

$stItem = $menu.Items.Add("服务未运行")
$stItem.Enabled = $false
$null = $menu.Items.Add("-")

$openItem = $menu.Items.Add("打开界面(&O)")
$openItem.add_Click({ Start-Gui })

$startItem = $menu.Items.Add("启动服务(&S)")
$startItem.add_Click({ Start-ServiceProc })

$stopItem = $menu.Items.Add("停止服务(&P)")
$stopItem.add_Click({ Stop-ServiceProc })

$null = $menu.Items.Add("-")
$exitItem = $menu.Items.Add("退出托盘(&X)")
$exitItem.add_Click({
    $ni.Visible = $false
    $ni.Dispose()
    [System.Windows.Forms.Application]::Exit()
})

$ni.add_MouseClick({
    param($s, $e)
    if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Right) {
        if (Test-HttpUp) { $stItem.Text = "服务运行中" } else { $stItem.Text = "服务未运行" }
        $menu.Show([System.Windows.Forms.Cursor]::Position)
    } elseif ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
        # 左键单击直接打开界面，符合直觉
        Start-Gui
    }
})

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 4000
$timer.add_Tick({
    if (Test-HttpUp) { $stItem.Text = "服务运行中" } else { $stItem.Text = "服务未运行" }
})
$timer.Start()

[System.Windows.Forms.Application]::Run()
$timer.Stop()
$ni.Visible = $false
$ni.Dispose()
