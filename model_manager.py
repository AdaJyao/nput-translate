#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
模型管理：列出已安装模型、切换当前模型、下载并转换为 OpenVINO IR。

目录约定：
    <程序目录>/models/<模型名>/
        openvino_encoder_model.xml/.bin
        openvino_decoder_model.xml/.bin
        source.spm / target.spm / vocab.json / config.json ...
        meta.json          ← 本文件写入：语言方向与显示名

下载与转换需要 optimum-intel + torch（约 500 MB 依赖），
因此只在**源码环境**（存在 .venv 且装了 optimum-cli）下可用；
打包版会给出明确提示，不会静默失败。
"""
import json
import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import nput_core as core

APP_DIR = core.APP_DIR
MODELS_DIR = os.path.join(APP_DIR, "models")

# 内置可下载模型清单。
# 安装包不再内置模型（否则体积翻倍），改为首次使用时从这里下载现成的 OpenVINO IR 包。
# url 指向 GitHub Release 上的附件（公开仓库，无需登录）。
_RELEASE_BASE = "https://github.com/AdaJyao/nput-translate/releases/download/v1.2.0"

MANIFEST = [
    {
        "id": "opus-mt-zh-en",
        "dir": "opus-mt-zh-en-ov",
        "label": "中 → 英（opus-mt-zh-en）",
        "src": "zh", "dst": "en",
        "note": "推荐，FP16 约 212 MB",
        "url": _RELEASE_BASE + "/opus-mt-zh-en-ov-fp16.zip",
        "size_mb": 197,
    },
]

# 兼容旧名字（源码环境下仍可用 optimum-cli 自行导出）
PRESETS = [
    {
        "id": "Helsinki-NLP/opus-mt-zh-en",
        "dir": "opus-mt-zh-en-ov",
        "label": "中 → 英（opus-mt-zh-en）",
        "src": "zh", "dst": "en",
        "note": "默认模型，约 445 MB（FP32）",
    },
    {
        "id": "Helsinki-NLP/opus-mt-en-zh",
        "dir": "opus-mt-en-zh-ov",
        "label": "英 → 中（opus-mt-en-zh）",
        "src": "en", "dst": "zh",
        "note": "反向翻译，约 445 MB",
    },
    {
        "id": "Helsinki-NLP/opus-mt-ja-zh",
        "dir": "opus-mt-ja-zh-ov",
        "label": "日 → 中（opus-mt-ja-zh）",
        "src": "ja", "dst": "zh",
        "note": "需要导出，约 445 MB",
    },
    {
        "id": "Helsinki-NLP/opus-mt-en-de",
        "dir": "opus-mt-en-de-ov",
        "label": "英 → 德（opus-mt-en-de）",
        "src": "en", "dst": "de",
        "note": "需要导出，约 445 MB",
    },
]

REQUIRED_FILES = (
    "openvino_encoder_model.xml", "openvino_encoder_model.bin",
    "openvino_decoder_model.xml", "openvino_decoder_model.bin",
)


# ---------------------------------------------------------------- 查询
def preset_by_dir(dirname):
    for p in PRESETS:
        if p["dir"] == dirname:
            return p
    return None


def read_meta(model_dir):
    """读模型目录里的 meta.json（语言方向）。缺失时按目录名猜。"""
    p = os.path.join(model_dir, "meta.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            m = json.load(f)
            if m.get("src") and m.get("dst"):
                return m
    except Exception:
        pass
    name = os.path.basename(model_dir.rstrip("\\/"))
    pre = preset_by_dir(name)
    if pre:
        return {"src": pre["src"], "dst": pre["dst"], "label": pre["label"]}
    return {"src": "zh", "dst": "en", "label": name}


def write_meta(model_dir, meta):
    try:
        with open(os.path.join(model_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[模型] 写 meta.json 失败:", repr(e))


def is_valid_model_dir(d):
    return all(os.path.exists(os.path.join(d, f)) for f in REQUIRED_FILES)


def dir_size(d):
    try:
        return sum(os.path.getsize(os.path.join(d, f))
                   for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))
    except Exception:
        return 0


def list_installed():
    """返回已安装模型列表：[{name, path, size_mb, label, src, dst, active}]"""
    cfg = core.load_config()
    active_path = os.path.normcase(os.path.normpath(cfg.get("model_dir", "")))
    out = []
    if not os.path.isdir(MODELS_DIR):
        return out
    for name in sorted(os.listdir(MODELS_DIR)):
        d = os.path.join(MODELS_DIR, name)
        if not os.path.isdir(d) or not is_valid_model_dir(d):
            continue
        meta = read_meta(d)
        out.append({
            "name": name,
            "path": d,
            "size_mb": round(dir_size(d) / (1024 * 1024), 1),
            "label": meta.get("label") or name,
            "src": meta.get("src", "zh"),
            "dst": meta.get("dst", "en"),
            "active": os.path.normcase(os.path.normpath(d)) == active_path,
        })
    return out


# ---------------------------------------------------------------- 环境探测
def export_tool():
    """返回可用的 optimum-cli 路径（源码环境才有）。"""
    for rel in (r".venv\Scripts\optimum-cli.exe",):
        p = os.path.join(APP_DIR, rel)
        if os.path.exists(p):
            return p
    return None


def export_python():
    p = os.path.join(APP_DIR, r".venv\Scripts\python.exe")
    return p if os.path.exists(p) else None


def can_convert():
    """能否在当前环境下载并转换模型。"""
    return export_tool() is not None and export_python() is not None


def convert_hint():
    return ("当前是打包版，未附带模型转换环境（需要 optimum-intel + torch，约 500 MB）。\n\n"
            "请在源码目录执行：\n"
            "    .\\setup.ps1\n"
            "    .\\export_model.ps1\n\n"
            "或直接把已导出的模型目录放进 models\\ 下，再回到这里「刷新」。")


# ---------------------------------------------------------------- 下载/转换
def download_and_convert(preset, on_line=None, should_stop=None):
    """下载指定预设并转换为 OpenVINO IR。

    on_line(str): 每行日志回调（在子线程里调用，调用方需自行切回 UI 线程）
    should_stop(): 返回 True 时中止
    返回 (ok: bool, message: str)
    """
    if not can_convert():
        return False, convert_hint()

    tool = export_tool()
    target = os.path.join(MODELS_DIR, preset["dir"])
    os.makedirs(MODELS_DIR, exist_ok=True)

    env = dict(os.environ)
    env.setdefault("HF_HOME", os.path.join(APP_DIR, ".hf-cache"))
    env.setdefault("HOME", env.get("USERPROFILE", APP_DIR))
    env["OPENVINO_DISABLE_TELEMETRY"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("HF_ENDPOINT", None)

    cmd = [tool, "export", "openvino",
           "--model", preset["id"],
           "--task", "text2text-generation",
           "--disable-stateful",
           target]

    if on_line:
        on_line(f"$ {' '.join(cmd)}")
        on_line(f"目标目录: {target}")

    try:
        # 用 CREATE_NO_WINDOW，避免转换时弹出黑色控制台窗口
        proc = subprocess.Popen(
            cmd, cwd=APP_DIR, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=core.NO_WINDOW)
    except Exception as e:
        return False, f"启动转换失败: {e}"

    try:
        for line in proc.stdout:
            if should_stop and should_stop():
                proc.kill()
                return False, "已取消"
            if on_line:
                on_line(line.rstrip())
        proc.wait(timeout=60)
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        return False, f"转换中断: {e}"

    if proc.returncode != 0:
        return False, f"转换失败（退出码 {proc.returncode}），请查看上方日志"

    if not is_valid_model_dir(target):
        return False, "转换完成但产物不完整，请检查日志"

    write_meta(target, {"src": preset["src"], "dst": preset["dst"],
                        "label": preset["label"]})

    # 不用的 decoder_with_past 删掉省 223 MB
    for suffix in (".xml", ".bin"):
        p = os.path.join(target, "openvino_decoder_with_past_model" + suffix)
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

    return True, f"完成：{preset['label']}"


def manifest_entry(dirname):
    for m in MANIFEST:
        if m["dir"] == dirname:
            return m
    return None


def download_zip_model(entry, on_line=None, on_progress=None, should_stop=None):
    """从 URL 下载现成的 OpenVINO IR 压缩包并解压到 models/<dir>。

    on_line(str)                      每行日志
    on_progress(downloaded, total)    下载进度（字节）
    should_stop()                     返回 True 时中止
    返回 (ok, message)
    """
    import tempfile
    import urllib.request
    import zipfile

    url = entry.get("url")
    if not url:
        return False, "该模型没有下载地址"

    target = os.path.join(MODELS_DIR, entry["dir"])
    os.makedirs(MODELS_DIR, exist_ok=True)

    if on_line:
        on_line("下载地址: " + url)

    tmpdir = tempfile.mkdtemp(prefix="nput-model-")
    zip_path = os.path.join(tmpdir, "model.zip")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NPUTranslate/1.1"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(zip_path, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            last_pct = -1
            while True:
                if should_stop and should_stop():
                    return False, "已取消"
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
                if total:
                    pct = done * 100 // total
                    if pct != last_pct and pct % 5 == 0:
                        last_pct = pct
                        if on_line:
                            on_line("已下载 %d%% (%.0f/%.0f MB)" %
                                    (pct, done / 1048576, total / 1048576))
        if on_line:
            on_line("下载完成，正在解压…")

        # 解压：兼容「zip 根目录就是文件」和「zip 里有一层同名目录」两种打包方式
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            root_prefix = ""
            top = {n.split("/")[0] for n in names if n.strip()}
            if len(top) == 1:
                only = next(iter(top))
                if any(n.startswith(only + "/") for n in names):
                    root_prefix = only + "/"
            os.makedirs(target, exist_ok=True)
            for n in names:
                if n.endswith("/"):
                    continue
                rel = n[len(root_prefix):] if root_prefix and n.startswith(root_prefix) else n
                if not rel:
                    continue
                dest = os.path.join(target, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(n) as src, open(dest, "wb") as dst:
                    shutil_copy(src, dst)
        if on_line:
            on_line("解压完成")
    except Exception as e:
        return False, "下载失败: %s: %s" % (type(e).__name__, e)
    finally:
        try:
            import shutil as _sh
            _sh.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

    if not is_valid_model_dir(target):
        return False, "下载包内容不完整（缺少 openvino_*_model.xml/.bin）"

    # 补 meta.json（语言方向）
    if not os.path.exists(os.path.join(target, "meta.json")):
        write_meta(target, {"src": entry["src"], "dst": entry["dst"],
                            "label": entry["label"]})
    if on_line:
        on_line("安装完成: " + target)
    return True, "完成：%s" % entry["label"]


def shutil_copy(src, dst):
    while True:
        buf = src.read(1024 * 1024)
        if not buf:
            break
        dst.write(buf)


def set_active(model_path):
    """把指定模型设为当前使用（写 config.model_dir）。"""
    cfg = core.load_config()
    try:
        rel = os.path.relpath(model_path, APP_DIR)
    except Exception:
        rel = model_path
    # 存相对路径，换机器也能用
    cfg = core.save_config({"model_dir": rel})
    return cfg


def delete_model(model_path):
    """删除模型目录（谨慎：调用方需确认）。"""
    active = core.load_config().get("model_dir") or ""
    if os.path.normcase(os.path.normpath(active)) == \
       os.path.normcase(os.path.normpath(model_path)):
        return False, "不能删除正在使用的模型，请先切换到其他模型"
    try:
        import shutil
        shutil.rmtree(model_path)
        return True, "已删除"
    except Exception as e:
        return False, f"删除失败: {e}"
