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

# 内置可选模型：都来自 HuggingFace 的 MarianMT，体积小、质量够用
PRESETS = [
    {
        "id": "Helsinki-NLP/opus-mt-zh-en",
        "dir": "opus-mt-zh-en-ov",
        "label": "中 → 英（opus-mt-zh-en）",
        "src": "zh", "dst": "en",
        "note": "默认模型，约 445 MB",
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
