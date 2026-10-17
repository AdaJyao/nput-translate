# NPU 常驻翻译服务 —— 部署与自启

## 组件

| 文件 | 作用 |
|---|---|
| `gui.py` | 图形界面（tkinter）：翻译 / 设置 / 服务管理 / 关于 |
| `translate_service.py` | 后台服务，持有模型与 NPU，常驻内存 |
| `start-service.ps1` | 服务起停与状态查询（`-Start` / `-Stop` / `-Status`） |
| `tray.ps1` | 系统托盘常驻，右键菜单控制 |
| `启动服务.bat` | 开机自启入口（由 GUI 自动生成） |
| `client.py` | 命令行客户端 |
| `config.json` | 全部设置（GUI 保存，服务重启后生效） |

## 日常使用

```powershell
$root = 'H:\Code_project\deepseek harness\Project\npu-translate'
$py   = Join-Path $root '.venv\Scripts\python.exe'

# 打开图形界面
& $py (Join-Path $root 'gui.py')

# 只起后台服务
& (Join-Path $root 'start-service.ps1') -Start
& (Join-Path $root 'start-service.ps1') -Status
& (Join-Path $root 'start-service.ps1') -Stop

# 系统托盘常驻（右键菜单可开界面/起停服务）
powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File (Join-Path $root 'tray.ps1')

# 命令行调一次
& $py (Join-Path $root 'client.py') --text "外面下雨了，记得带伞。"
```

## 设置页开关说明

| 开关 | 作用 |
|---|---|
| **开机自启** | 开机时自动拉起服务（写启动文件夹 + 注册表双保险） |
| **后台服务** | **开**：关掉界面后服务继续常驻，插件/客户端仍可调用；**关**：关掉界面时一并停止服务，不留后台进程 |
| **关闭最小化** | 开：点 X 隐藏到托盘而不是退出；关：点 X 直接退出 |

「后台服务」与「关闭最小化」是两件事：
- 前者决定**关闭后服务还活着吗**（生命周期解耦）
- 后者决定**窗口是隐藏还是销毁**

常见组合：

| 想要的效果 | 后台服务 | 关闭最小化 |
|---|---|---|
| 常驻后端（推荐）：界面可随时关，服务一直在 | 开 | 关 |
| 完全驻留：界面收进托盘，服务也在 | 开 | 开 |
| 用完即走：关界面就停服务 | 关 | 关 |

## 开机自启

GUI「设置」页勾选**开机自动启动服务**即可，会自动做两件事（双保险）：

1. 生成 `启动服务.bat`，并在
   `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\` 放快捷方式；
2. 同时写注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\NPU-Translate`。

取消勾选则两个都清掉。

### 手动方式（GUI 写不进去时）

```powershell
$lnk = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\NPU-Translate.lnk"
$bat = 'H:\Code_project\deepseek harness\Project\npu-translate\启动服务.bat'
$w = New-Object -ComObject WScript.Shell
$l = $w.CreateShortcut($lnk)
$l.TargetPath = $bat
$l.WorkingDirectory = 'H:\Code_project\deepseek harness\Project\npu-translate'
$l.Save()
```

## 协议（TCP，换行分隔 JSON）

默认 `127.0.0.1:8715`，仅监听本机。

| 请求 | 响应 |
|---|---|
| `{"text": "中文"}` | `{"ok":true,"src":"...","dst":"...","ms":123.4,"tier":"64x64"}` |
| `{"cmd":"status"}` | `{"ok":true,"device":"NPU","tiers":[...],"uptime_s":12.3,"pid":123,"requests":0}` |
| `{"cmd":"ping"}` | `{"ok":true,"cmd":"pong"}` |
| 其他/空 | `{"ok":false,"error":"..."}` |

## 省电设计

- 模型常驻内存，空闲时主线程阻塞在 `socket.accept()`（1s 超时轮询），**实测 CPU 0%**。
- NPU 仅在推理瞬间上电，不推理时不占独显、不唤醒 dGPU。
- 全程 batch=1、无 KV 缓存、贪心解码 —— 单次调用算力极小。

## 常见问题

**Q：改了设置没生效？**
A：设备、档位、端口在服务启动时确定，改完需重启服务（「服务」页 → 停止 → 启动）。

**Q：开机自启勾选后提示未生效？**
A：写启动文件夹/注册表被安全软件或权限策略拦截。用上面的手动方式放快捷方式。

**Q：服务端口占用？**
A：`config.json` 里改 `port`，同时重启服务。

**Q：托盘图标没出现？**
A：确认是用 `powershell -File tray.ps1` 起的；Windows 10/11 需在「任务栏设置 → 其他系统托盘图标」里打开。
