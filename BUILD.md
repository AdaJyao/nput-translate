# 构建与打包

## 交付物

运行 `build.ps1` 后产出：

```
dist\NPU翻译\                     ← 整个目录拷走即用
├── NPU翻译.exe                   ← 界面（5.4 MB，onedir + _internal\）
├── _internal\                     ← 界面依赖（CustomTkinter / Pillow / tkinter）
├── 服务\
│   ├── NPU翻译-服务.exe            ← 后台服务（OpenVINO + NPU 插件）
│   └── _internal\                 ← 服务依赖（约 401 MB）
├── models\opus-mt-zh-en-ov\       ← OpenVINO IR（428 MB，已裁掉未用的 decoder_with_past）
├── config.json                    ← 设置（model_dir 为相对路径）
├── tray.ps1                       ← 系统托盘脚本
├── 启动服务.bat                    ← 开机自启用
├── app.ico
└── README.md / SERVICE.md / KISS.md
```

总计约 **916 MB**，其中服务依赖 401 MB、模型 428 MB。

## 构建命令

```powershell
$dir = 'H:\Code_project\deepseek harness\Project\npu-translate'

.\build.ps1              # 全量：GUI + 服务 + 组装
.\build.ps1 -OnlyGui     # 只构界面（迭代 UI 时用，很快）
.\build.ps1 -OnlySvc     # 只构服务
.\build.ps1 -NoClean     # 保留 build\ 中间产物，重复构建更快
```

构建日志分别写入 `build-gui.log` / `build-svc.log` / `build-all.log`。

## 依赖

| 用途 | 包 |
|---|---|
| 界面 | `customtkinter`、`pillow` |
| 推理 | `openvino`、`transformers`、`tokenizers`、`sentencepiece`、`numpy` |
| 打包 | `pyinstaller` |

用 uv 安装（venv 由 uv 创建，没有 pip）：

```powershell
$py = '.venv\Scripts\python.exe'
uv pip install --python $py customtkinter pillow pyinstaller
```

## 为什么是两个 exe

界面进程只做交互，不加载模型；模型与 NPU 上下文在服务进程里。
好处：界面启动快、内存小；改设置只重启服务；服务可开机常驻而不必开界面。

两者都用 PyInstaller **onedir**（而不是 onefile）：

- onefile 每次启动要把 400 MB 解压到临时目录，服务开机自启会明显变慢。
- onedir 各自带 `_internal`，不能混放在同一目录，所以服务放进 `服务\` 子目录。
  `nput_core.app_dir()` 会识别这种布局并上溯一级作为程序根目录。

## 打包踩坑（改 build.ps1 前必读）

### 坑 1：绝不能排除 PIL

CustomTkinter 依赖 Pillow。`--exclude-module PIL` 会让 exe 启动即崩（无任何提示）。
曾因此把 exe 从 5.4 MB 压到 2.9 MB「成功」构建，运行时静默退出。

### 坑 2：中文路径下 sentencepiece 打不开 .spm

sentencepiece 的 C++ `LoadFromFile` 在 Windows 用 ANSI 窄字符路径，
程序目录含中文（`NPU翻译`）时会误报：

```
RuntimeError: NOT_FOUND: "...\source.spm": No such file or directory Error #2
```

而文件明明存在。更坑的是 transformers 的 `from_pretrained` 会把这个异常吞掉
（`except import_protobuf_decode_error(): return False`），最终只看到
`TypeError: 'bool' object is not callable`。

**对策**：`translate.py` 的 `_patch_spm_loader()` 把
`transformers.models.marian.tokenization_marian.load_spm` 替换成
「Python 读字节 + `LoadFromSerializedProto`」，彻底绕开 C++ 文件 API。
并用直接构造 `MarianTokenizer(...)` 取代 `from_pretrained`，让真实异常可见。

### 坑 3：服务 exe 在子目录 → 程序根目录算错

服务 exe 位于 `服务\` 下，若直接用 `dirname(sys.executable)` 当程序目录，
`models\` 会被解析成 `服务\models\`（不存在）。
`nput_core.app_dir()` 已处理：子目录名为 `服务`/`service` 时上溯一级。

### 坑 4：PowerShell 5.1 读 UTF-8 无 BOM 的 .ps1 会按 GBK 解析

`tray.ps1` 含中文字符串，无 BOM 时直接语法错误（`MissingEndParenthesisInMethodCall`）。
构建脚本会在组装阶段给交付目录里所有 `.ps1` 补 BOM。

### 坑 5：Start-Process 传含空格路径

`-ArgumentList $path` 会被空格拆断（`H:\Code_project\deepseek` + `harness\...`）。
必须自己加引号：`-ArgumentList "`"$path`""`。

### 坑 6：子进程继承管道导致命令挂起

从命令行启动 GUI/服务（长驻进程）时，若不重定向输出，父命令会一直等管道 EOF。
测试时务必 `-RedirectStandardOutput` / `-RedirectStandardError`。

### 坑 7：服务日志中文乱码（编码不一致）

服务是控制台程序，stdout/stderr 被重定向到 `service.log`。中文 Windows 下
Python 默认按 **GBK** 写，而 GUI 按 **UTF-8** 读 → 日志里全是 `锛坘iss-translator锛`。

注意：**打包成 exe 后 `PYTHONIOENCODING` 环境变量不一定生效**，必须在服务进程内强制：

```python
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
```

### 坑 8：托盘进程检测会匹配到自己

用 PowerShell 查「tray.ps1 是否已在运行」时，查询语句自身的命令行里就含
`tray.ps1` 字样，`Where-Object { $_.CommandLine -like '*tray.ps1*' }` 会匹配到自己，
导致**永远返回「已在运行」→ 托盘永远不启动**。必须排除自身 PID：

```powershell
$me = $PID
... Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -like '*tray.ps1*' }
```

### 坑 9：Win11 把新托盘图标藏进溢出区

`NotifyIcon.Visible = true` 确实创建了图标，但 Windows 11 默认把它收进任务栏的
「^」展开区，用户会以为没启动。无法用 API 强制常显，只能在首次驻留时弹气泡提示，
引导用户去溢出区找并拖到任务栏。

### 坑 10：托盘「打开界面」会开出第二个窗口

界面隐藏到托盘后进程仍在，托盘再 `Start-Process` 就会开第二个实例。
解法是单实例：GUI 监听 `127.0.0.1:8717`，托盘先发 `show` 唤醒已有窗口，
连不上才启动新进程；GUI 启动时也先探测这个端口，已在运行就唤醒并退出。

## 打包后可用的诊断端点

服务起来后用浏览器或 curl 访问：

| 端点 | 用途 |
|---|---|
| `GET /status` | 设备、档位、运行时长、请求数、tokenizer 类型、模型目录 |
| `GET /selftest` | 完整自检：tokenizer 是否可用、推理是否成功、失败时的 traceback |
| `GET /devices` | 可用 OpenVINO 设备 |
| `GET /ping` | 存活探测 |
| `GET /translate?text=...&to=en` | GET 方式翻译（便于地址栏测） |
| `POST /shutdown` | 优雅停止服务 |

`/selftest` 是打包排障的首选入口。

## 已知限制

- 仅支持 **中→英**（opus-mt-zh-en）。其他方向原样返回，不报错。
- 模型文件必须与 exe 一起分发；不支持从 HuggingFace 在线拉取。
- 首次启动服务需约 5–8 秒编译 3 档 NPU 静态形状。
