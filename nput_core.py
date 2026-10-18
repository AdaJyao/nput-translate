#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""NPU 翻译配置与通用工具：被 GUI / 服务 / 客户端共用。
所有设置集中在 config.json，GUI 改完写盘，服务重启后生效。

打包说明：PyInstaller 冻结后 sys.frozen=True，程序目录取 exe 所在目录，
这样 models/、config.json 放在 exe 旁边即可被找到。
"""
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def app_dir():
    """程序根目录：打包后 = 主程序（GUI exe）所在目录；开发时 = 源码目录。

    坑：服务 exe 放在 <主目录>\\服务\\ 子目录（onedir 各自带 _internal），
    若直接用 dirname(sys.executable) 会把程序目录错认成「服务」，
    进而把 models\\ 解析到 服务\\models\\（不存在）。这里上溯一级。
    """
    if is_frozen():
        d = os.path.dirname(os.path.abspath(sys.executable))
        if os.path.basename(d) in ("服务", "service"):
            d = os.path.dirname(d)
        return d
    return HERE


APP_DIR = app_dir()
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
MODEL_DIR = os.path.join(APP_DIR, "models", "opus-mt-zh-en")

DEFAULTS = {
    "device": "NPU",                 # NPU | CPU
    "host": "127.0.0.1",
    "port": 8715,                    # TCP 端口（GUI / client.py）
    "http_port": 8716,               # HTTP 端口（kiss-translator 等浏览器插件）
    "model_dir": MODEL_DIR,
    "enc_seq": 64,                   # 基准档位（源句固定长度）
    "dec_seq": 64,                   # 基准档位（解码固定长度）
    "max_new_tokens": 96,
    "autostart": False,              # 开机自启
    "background_service": True,      # 后台服务：关界面后服务继续常驻
    # 按文本语言自动挑模型（缺直达时可用英文中转）
    "auto_route": True,
    # 同时驻留的模型数上限（超出按 LRU 淘汰，避免占满 NPU 内存）
    "auto_max_models": 2,
    "minimize_to_tray": True,        # 关闭窗口时最小化到托盘
    "autostart_service": True,       # 打开界面时若服务没起，自动拉起
    "log_level": "INFO",
}


def exe_name():
    return "NPU翻译.exe" if is_frozen() else None


def gui_cmd():
    """返回启动 GUI 的命令列表（供托盘/自启使用）。"""
    if is_frozen():
        return [sys.executable]
    return [sys.executable, os.path.join(HERE, "gui.py")]


def service_cmd():
    """返回启动后台服务的命令列表。
    打包后服务 exe 位于 <程序目录>\\服务\\ 子目录（onedir 各自带 _internal，不能混放）。
    若缺失返回 None，让调用方明确报错，绝不退回用 GUI exe（会把界面再开一份）。"""
    if is_frozen():
        for cand in (
            os.path.join(APP_DIR, "服务", "NPU翻译-服务.exe"),
            os.path.join(APP_DIR, "NPU翻译-服务.exe"),
        ):
            if os.path.exists(cand):
                return [cand]
        return None
    return [sys.executable, os.path.join(HERE, "translate_service.py")]


def service_exe():
    """打包模式下服务 exe 路径（不存在返回 None）。"""
    c = service_cmd()
    return c[0] if c else None


def tray_cmd():
    """托盘启动命令。
    注意：不要返回 [exe, "--tray"] —— GUI exe 不解析参数，会再开一个界面。
    托盘统一走随程序分发的 tray.ps1（PowerShell 一定有）。"""
    ps1 = os.path.join(APP_DIR, "tray.ps1")
    return ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
            "-ExecutionPolicy", "Bypass", "-File", ps1]


def load_config(path=CONFIG_PATH):
    """读配置；缺字段用默认值补齐（不写盘，避免并发）。"""
    cfg = dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[配置] 读取失败，用默认值: {type(e).__name__}: {e}")
    # model_dir 允许写相对路径（打包后随程序目录走），这里统一解析成绝对路径
    md = cfg.get("model_dir") or ""
    if md and not os.path.isabs(md):
        cfg["model_dir"] = os.path.normpath(os.path.join(APP_DIR, md))
    return cfg


def save_config(patch, path=CONFIG_PATH):
    """合并写入配置（保留未提及字段）。返回完整配置。"""
    cfg = load_config(path)
    cfg.update(patch)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return cfg


def is_service_up(host=None, port=None, timeout=1.5):
    """探测服务是否在监听。"""
    cfg = load_config()
    host = host or cfg.get("host", "127.0.0.1")
    port = int(port or cfg.get("port", 8715))
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def service_probe(host=None, port=None, timeout=1.5):
    """向服务要一份状态（设备/档位/运行时长）。失败返回 None。"""
    import client as _c
    cfg = load_config()
    host = host or cfg.get("host", "127.0.0.1")
    port = int(port or cfg.get("port", 8715))
    try:
        return _c.ask_json({"cmd": "status"}, host, port, timeout)
    except Exception:
        return None


def translate(text, to="en", host=None, port=None, timeout=180):
    """调服务翻译，返回 dict。服务不在时抛异常。

    to: 目标语言（en / zh-CN / de / fr / ru …）；开启自动路由时，
        服务会按原文语言自动挑选模型，必要时经英文中转（耗时可能翻倍，故默认 180s）。
    """
    import client as _c
    cfg = load_config()
    host = host or cfg.get("host", "127.0.0.1")
    port = int(port or cfg.get("port", 8715))
    return _c.ask_json({"text": text, "to": to or "en"}, host, port, timeout)


def openvino_devices():
    """列出本机可用 OpenVINO 设备（探测用，失败返回 []）。"""
    try:
        import openvino as ov
        return list(ov.Core().available_devices)
    except Exception:
        return []


def fmt_devices(devices=None):
    """把 ['CPU','GPU.0','NPU'] 格式化成中文标签。"""
    devices = devices if devices is not None else openvino_devices()
    label = {"NPU": "NPU（Intel AI Boost，省电）",
             "CPU": "CPU（24 核，快但费电）",
             "GPU.0": "GPU.0（核显）",
             "GPU.1": "GPU.1（独显）"}
    return [label.get(d, d) for d in devices]


# ============================================================ HTTP 侧
def _http_base(host=None, port=None):
    cfg = load_config()
    host = host or cfg.get("host", "127.0.0.1")
    port = int(port or cfg.get("http_port", 8716))
    return f"http://{host}:{port}"


def http_get(path, timeout=5):
    """GET 服务 HTTP 端点，返回 dict 或 None。"""
    import urllib.request
    try:
        with urllib.request.urlopen(_http_base() + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def http_post(path, obj, timeout=60):
    """POST 服务 HTTP 端点，返回 dict 或 None。"""
    import urllib.request
    try:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(_http_base() + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def http_alive(timeout=1.5):
    """HTTP 端口是否活着（比 TCP 更能确认服务完整就绪）。"""
    return http_get("/ping", timeout=timeout) is not None


def fetch_devices():
    """向服务要可用设备列表；服务没起则返回常见默认。"""
    r = http_get("/devices")
    if r and isinstance(r.get("devices"), list) and r["devices"]:
        return r["devices"]
    return ["NPU", "CPU"]


# ============================================================ 进程管理
# Windows 进程创建标志
CREATE_NO_WINDOW = 0x08000000          # 不创建控制台窗口（GUI 起控制台子进程必须带）
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008

# GUI 本身没有控制台；任何控制台子进程（powershell/python.exe）如果不带
# CREATE_NO_WINDOW，就会在桌面上闪出或留下一个黑色控制台窗口。
NO_WINDOW = CREATE_NO_WINDOW


def run_hidden(args, timeout=30, encoding="utf-8"):
    """静默执行子进程并捕获输出——不弹黑窗口。"""
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          encoding=encoding, errors="replace",
                          creationflags=NO_WINDOW)


def popen_hidden(args, cwd=None, env=None, stdout=None):
    """后台静默启动长驻子进程——不弹黑窗口。"""
    return subprocess.Popen(args, cwd=cwd, env=env,
                            stdout=stdout, stderr=subprocess.STDOUT,
                            creationflags=NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                            close_fds=True)


def _list_procs():
    """列出本机 python / NPU 相关进程（CIM，无需 psutil）。"""
    try:
        ps = ("Get-CimInstance Win32_Process | "
              "Where-Object { $_.Name -like 'python*' -or $_.Name -like '*NPU*' } | "
              "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress")
        out = run_hidden(["powershell", "-NoProfile", "-Command", ps],
                         timeout=25).stdout.strip()
        if not out:
            return []
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def service_pids():
    """找出后台服务进程 PID。"""
    pids = []
    for p in _list_procs():
        cmd = (p.get("CommandLine") or "")
        name = (p.get("Name") or "")
        hit = False
        if "translate_service" in cmd or "--service" in cmd:
            hit = True
        if name.lower().startswith("npu") and "服务" in name:
            hit = True
        if hit:
            try:
                pids.append(int(p["ProcessId"]))
            except Exception:
                pass
    return pids


def tray_running():
    """托盘进程是否已在运行（避免每次最小化都再起一个）。
    坑：查询语句自身的命令行里也含有 'tray.ps1' 字样，会被 Where-Object 匹配到，
    导致永远返回 True（于是托盘永远不启动）。必须排除自己的 PID。"""
    try:
        ps = ("$me = $PID; "
              "Get-CimInstance Win32_Process | "
              "Where-Object { $_.Name -eq 'powershell.exe' -and $_.ProcessId -ne $me "
              "-and $_.CommandLine -like '*tray.ps1*' } | "
              "Select-Object -First 1 -ExpandProperty ProcessId")
        out = run_hidden(["powershell", "-NoProfile", "-Command", ps],
                         timeout=20).stdout.strip()
        return bool(out)
    except Exception:
        return False


def is_service_running():
    """服务是否在运行（HTTP 优先，退回 TCP）。"""
    return http_alive() or is_service_up()


def start_service():
    """在后台拉起服务（脱离当前进程）。返回 (ok, message)。"""
    if is_service_running():
        return True, "服务已在运行"
    cmd = service_cmd()
    if not cmd:
        return False, ("找不到服务程序 NPU翻译-服务.exe，"
                       "请确认它与界面程序在同一目录。")
    try:
        # 只用 CREATE_NO_WINDOW，不加 DETACHED_PROCESS：
        # 两者互斥（DETACHED_PROCESS 会让 CREATE_NO_WINDOW 失效），
        # 而开发模式下服务是 python.exe（控制台程序），带 DETACHED 会留下一个黑色控制台窗口。
        # Windows 不会因父进程退出而杀掉子进程，所以服务仍能在界面关闭后常驻。
        env = dict(os.environ)
        env.setdefault("HF_HOME", os.path.join(APP_DIR, ".hf-cache"))
        env.setdefault("HOME", env.get("USERPROFILE", APP_DIR))
        env["OPENVINO_DISABLE_TELEMETRY"] = "1"
        # 关键：服务是控制台程序，输出被重定向到 service.log。
        # 中文 Windows 下 Python 默认按 GBK 写，GUI 按 UTF-8 读 → 日志全是乱码。
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env.pop("HF_ENDPOINT", None)
        logf = open(os.path.join(APP_DIR, "service.log"), "a", encoding="utf-8")
        popen_hidden(cmd, cwd=APP_DIR, env=env, stdout=logf)
        return True, "服务启动中"
    except Exception as e:
        return False, f"启动失败: {e}"


def stop_service():
    """停止服务：先请求优雅退出，不行再杀进程。"""
    http_post("/shutdown", {}, timeout=3)
    deadline = time.time() + 8
    while time.time() < deadline:
        if not is_service_running():
            return True, "服务已停止"
        time.sleep(0.4)
    pids = service_pids()
    for pid in pids:
        try:
            run_hidden(["taskkill", "/PID", str(pid), "/F", "/T"], timeout=10)
        except Exception:
            pass
    return True, f"已强制结束 {len(pids)} 个进程"


def wait_service_ready(timeout=120, on_tick=None):
    """等服务就绪。返回 True/False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if http_alive(timeout=1.0):
            return True
        if on_tick:
            on_tick()
        time.sleep(0.5)
    return False



