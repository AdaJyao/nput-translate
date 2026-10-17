#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
NPU 翻译 —— 图形界面（CustomTkinter，现代化深/浅色自适应）

职责划分：
  GUI 只做界面与进程管理；模型与推理在后台服务进程里。
  这样 GUI 进程极轻（省电），改设置只需重启服务。
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

os.environ.setdefault("HF_HOME", os.path.join(HERE, ".hf-cache"))
if not os.environ.get("HOME"):
    os.environ["HOME"] = os.environ.get("USERPROFILE", HERE)
os.environ.setdefault("OPENVINO_DISABLE_TELEMETRY", "1")

import customtkinter as ctk

import nput_core as core

# ---------------------------------------------------------------- 外观
ctk.set_appearance_mode("auto")
ctk.set_default_color_theme("blue")

FONT = "Microsoft YaHei UI"
F_TITLE = (FONT, 20, "bold")
F_H = (FONT, 14, "bold")
F_BODY = (FONT, 13)
F_SMALL = (FONT, 11)
F_MONO = ("Consolas", 12)

PAD = 16

# 单实例唤起端口：第一个 GUI 实例监听它，再次启动 GUI 或点托盘「打开界面」时
# 通过它把已有窗口叫出来，而不是再开一个进程。
SHOW_PORT = 8717


class UiQueue:
    """线程安全 UI 更新队列：后台线程只入队，主线程定时 drain。"""

    def __init__(self):
        self.q = []
        self._lock = threading.Lock()

    def post(self, fn, *a, **kw):
        with self._lock:
            self.q.append((fn, a, kw))

    def pump(self):
        with self._lock:
            items, self.q = self.q, []
        for fn, a, kw in items:
            try:
                fn(*a, **kw)
            except Exception as e:
                print("[UI]", repr(e))


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.cfg = core.load_config()
        self.uiq = UiQueue()
        self._busy = False
        self._svc_up = False
        # _destroyed: 窗口已真正销毁（决定后台循环是否继续）
        # 注意不要用「隐藏」来表示它 —— 隐藏到托盘时唤起监听与状态轮询必须继续活着
        self._destroyed = False

        self.title("NPU 翻译")
        self.geometry("980x860")
        self.minsize(900, 680)

        try:
            icon = os.path.join(core.APP_DIR, "app.ico")
            if os.path.exists(icon):
                self.iconbitmap(icon)
        except Exception:
            pass

        self._build_header()
        self._build_tabs()
        self._pump_loop()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(300, self._boot)

    # ------------------------------------------------------------ 头部
    def _build_header(self):
        bar = ctk.CTkFrame(self, corner_radius=0)
        bar.pack(fill="x", side="top")

        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.pack(side="left", padx=PAD, pady=12)
        ctk.CTkLabel(left, text="NPU 翻译", font=F_TITLE).pack(anchor="w")
        ctk.CTkLabel(left, text="Intel AI Boost · 本机常驻 · 中→英",
                     font=F_SMALL, text_color="#8a8a8a").pack(anchor="w")
        # 状态直接放左侧栏内：浮动 pill 在 CTk 的 DPI 缩放 + place 下渲染不稳，
        # 放已确认能渲染的 pack 流里最可靠。
        self.pill = ctk.CTkLabel(left, text="● 检查中", font=F_SMALL,
                                 text_color="#8a8a8a")
        self.pill.pack(anchor="w", pady=(2, 0))

    # ------------------------------------------------------------ 页签
    def _build_tabs(self):
        # 用 CTkSegmentedButton 自绘导航：比 CTkTabview 更可控、更现代
        navwrap = ctk.CTkFrame(self, fg_color="transparent")
        navwrap.pack(fill="x", padx=PAD, pady=(0, 10))
        self.nav = ctk.CTkSegmentedButton(
            navwrap, values=["翻译", "服务", "设置", "模型", "插件", "关于"],
            font=F_H, height=38, command=self._switch_tab)
        self.nav.pack(anchor="w")
        self.nav.set("翻译")

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=PAD, pady=(0, PAD))
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        self.pages = {}
        for name in ("翻译", "服务", "设置", "模型", "插件", "关于"):
            fr = ctk.CTkFrame(self.body, fg_color="transparent")
            fr.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = fr

        self._build_translate(self.pages["翻译"])
        self._build_service(self.pages["服务"])
        self._build_settings(self.pages["设置"])
        self._build_models(self.pages["模型"])
        self._build_plugin(self.pages["插件"])
        self._build_about(self.pages["关于"])
        self._switch_tab("翻译")

    def _switch_tab(self, name):
        for k, fr in self.pages.items():
            if k == name:
                fr.grid()
                fr.tkraise()
            else:
                fr.grid_remove()

    # ------------------------------------------------------------ 翻译页
    def _build_translate(self, f):
        ctk.CTkLabel(f, text="原文（中文）", font=F_H).pack(anchor="w", pady=(8, 4))
        self.src = ctk.CTkTextbox(f, font=F_BODY, height=150, wrap="word")
        self.src.pack(fill="x")
        self.src.insert("1.0", "今天天气真好，我们一起去公园散步吧。")

        bar = ctk.CTkFrame(f, fg_color="transparent")
        bar.pack(fill="x", pady=10)
        self.btn_tr = ctk.CTkButton(bar, text="翻译", width=110, height=36,
                                    font=F_H, command=self._do_translate)
        self.btn_tr.pack(side="left")
        ctk.CTkButton(bar, text="清空", width=80, height=36, font=F_BODY,
                      fg_color="#555555", hover_color="#666666",
                      command=self._clear).pack(side="left", padx=8)
        ctk.CTkButton(bar, text="粘贴", width=80, height=36, font=F_BODY,
                      fg_color="#555555", hover_color="#666666",
                      command=self._paste).pack(side="left")
        ctk.CTkButton(bar, text="复制译文", width=100, height=36, font=F_BODY,
                      fg_color="#555555", hover_color="#666666",
                      command=self._copy_dst).pack(side="left", padx=8)
        self.lbl_ms = ctk.CTkLabel(bar, text="", font=F_SMALL, text_color="#8a8a8a")
        self.lbl_ms.pack(side="right")

        ctk.CTkLabel(f, text="译文（English）", font=F_H).pack(anchor="w", pady=(4, 4))
        self.dst = ctk.CTkTextbox(f, font=F_MONO, height=150, wrap="word")
        self.dst.pack(fill="both", expand=True)

    def _clear(self):
        self.src.delete("1.0", "end")
        self.dst.delete("1.0", "end")
        self.lbl_ms.configure(text="")

    def _paste(self):
        try:
            self.src.delete("1.0", "end")
            self.src.insert("1.0", self.clipboard_get())
        except Exception:
            pass

    def _copy_dst(self):
        t = self.dst.get("1.0", "end").strip()
        if t:
            self.clipboard_clear()
            self.clipboard_append(t)

    def _do_translate(self):
        text = self.src.get("1.0", "end").strip()
        if not text or self._busy:
            return
        if not self._svc_up:
            self.lbl_ms.configure(text="服务未运行，正在启动…")
            self._start_service(then_translate=text)
            return
        self._busy = True
        self.btn_tr.configure(state="disabled", text="翻译中")
        self.lbl_ms.configure(text="翻译中…")
        self.dst.delete("1.0", "end")

        def _run():
            try:
                r = core.translate(text)
                self.uiq.post(self._show_result, r.get("dst", ""),
                              r.get("ms", 0), r.get("tier", ""), r.get("error"))
            except Exception as e:
                self.uiq.post(self._show_result, "", 0, "",
                              f"{type(e).__name__}: {e}")
        threading.Thread(target=_run, daemon=True).start()

    def _show_result(self, dst, ms, tier, err):
        self._busy = False
        self.btn_tr.configure(state="normal", text="翻译")
        if err:
            self.lbl_ms.configure(text=f"失败：{err}")
            self.dst.insert("1.0", "翻译失败，请检查服务状态。")
        else:
            self.dst.insert("1.0", dst)
            self.lbl_ms.configure(text=f"{ms} ms  ·  {tier}")

    # ------------------------------------------------------------ 服务页
    def _build_service(self, f):
        # 用可滚动容器：窗口较小时（高 DPI / 小屏）内容不会被裁掉
        wrap = ctk.CTkScrollableFrame(f, fg_color="transparent")
        wrap.pack(fill="both", expand=True)

        card = ctk.CTkFrame(wrap, corner_radius=10)
        card.pack(fill="x", pady=(8, 12))

        self.svc_state = ctk.CTkLabel(card, text="● 未运行", font=F_TITLE,
                                      text_color="#e05a5a")
        self.svc_state.pack(anchor="w", padx=18, pady=(14, 6))

        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=18, pady=(0, 14))
        self.svc_rows = {}
        for i, key in enumerate(("设备", "可用档位", "运行时长", "累计请求", "监听端口")):
            ctk.CTkLabel(grid, text=key, font=F_SMALL, text_color="#8a8a8a",
                         width=90, anchor="w").grid(row=i, column=0, sticky="w", pady=2)
            v = ctk.CTkLabel(grid, text="—", font=F_BODY, anchor="w")
            v.grid(row=i, column=1, sticky="w", pady=2)
            self.svc_rows[key] = v

        bar = ctk.CTkFrame(wrap, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 10))
        ctk.CTkButton(bar, text="启动服务", width=110, height=36, font=F_BODY,
                      command=lambda: self._start_service()).pack(side="left")
        ctk.CTkButton(bar, text="停止服务", width=110, height=36, font=F_BODY,
                      fg_color="#a33a3a", hover_color="#b94444",
                      command=self._stop_service).pack(side="left", padx=8)
        ctk.CTkButton(bar, text="重启", width=80, height=36, font=F_BODY,
                      fg_color="#555555", hover_color="#666666",
                      command=self._restart_service).pack(side="left")
        ctk.CTkButton(bar, text="刷新", width=80, height=36, font=F_BODY,
                      fg_color="#555555", hover_color="#666666",
                      command=self._refresh_status).pack(side="left", padx=8)

        ctk.CTkLabel(wrap, text="服务日志（末尾）", font=F_H).pack(anchor="w", pady=(6, 4))
        self.logbox = ctk.CTkTextbox(wrap, font=("Consolas", 11), height=220)
        self.logbox.pack(fill="x", pady=(0, 4))
        self.logbox.configure(state="disabled")

    def _start_service(self, then_translate=None):
        self.svc_state.configure(text="● 启动中…", text_color="#d8a23a")

        def _run():
            ok, msg = core.start_service()
            if ok:
                ready = core.wait_service_ready()
                self.uiq.post(self._after_start, ready, msg, then_translate)
            else:
                self.uiq.post(self._after_start, False, msg, then_translate)
        threading.Thread(target=_run, daemon=True).start()

    def _after_start(self, ready, msg, then_translate):
        if ready:
            self._refresh_status()
            if then_translate:
                self._do_translate()
        else:
            self.svc_state.configure(text="● 启动失败", text_color="#e05a5a")
            self.svc_rows["监听端口"].configure(text=msg)

    def _stop_service(self):
        self.svc_state.configure(text="● 停止中…", text_color="#d8a23a")

        def _run():
            ok, msg = core.stop_service()
            self.uiq.post(self._refresh_status)
        threading.Thread(target=_run, daemon=True).start()

    def _restart_service(self):
        def _run():
            core.stop_service()
            core.wait_service_ready(timeout=10)
            ok, msg = core.start_service()
            ready = core.wait_service_ready() if ok else False
            self.uiq.post(self._refresh_status)
        threading.Thread(target=_run, daemon=True).start()

    def _refresh_status(self):
        def _run():
            st = core.http_get("/status")
            self.uiq.post(self._apply_status, st)
        threading.Thread(target=_run, daemon=True).start()

    def _apply_status(self, st):
        cfg = self.cfg
        port_txt = f"{cfg.get('port')} (TCP) · {cfg.get('http_port')} (HTTP)"
        if st and st.get("ok"):
            self._svc_up = True
            self.svc_state.configure(text="● 运行中", text_color="#3ba55d")
            self.pill.configure(text="● 服务运行中 · 设备 " + str(st.get("device", "")),
                                text_color="#3ba55d")
            self.svc_rows["设备"].configure(text=str(st.get("device", "—")))
            self.svc_rows["可用档位"].configure(text=", ".join(st.get("tiers", [])))
            self.svc_rows["运行时长"].configure(text=f"{st.get('uptime_s')} 秒")
            self.svc_rows["累计请求"].configure(text=str(st.get("requests", 0)))
            self.svc_rows["监听端口"].configure(text=port_txt)
        else:
            self._svc_up = False
            self.svc_state.configure(text="● 未运行", text_color="#e05a5a")
            self.pill.configure(text="● 服务已停止", text_color="#e05a5a")
            for k in ("设备", "可用档位", "运行时长", "累计请求"):
                self.svc_rows[k].configure(text="—")
            self.svc_rows["监听端口"].configure(text=port_txt)
        self._tail_log()

    def _tail_log(self):
        try:
            p = os.path.join(core.APP_DIR, "service.log")
            if not os.path.exists(p):
                return
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()[-40:]
            self.logbox.configure(state="normal")
            self.logbox.delete("1.0", "end")
            self.logbox.insert("1.0", "".join(lines))
            self.logbox.see("end")
            self.logbox.configure(state="disabled")
        except Exception:
            pass

    # ------------------------------------------------------------ 设置页
    def _build_settings(self, f):
        wrap = ctk.CTkScrollableFrame(f, fg_color="transparent")
        wrap.pack(fill="both", expand=True)

        def row(label, widget_factory, hint=None):
            r = ctk.CTkFrame(wrap, fg_color="transparent")
            r.pack(fill="x", pady=6)
            ctk.CTkLabel(r, text=label, font=F_BODY, width=130,
                         anchor="w").pack(side="left")
            w = widget_factory(r)
            w.pack(side="left")
            if hint:
                ctk.CTkLabel(r, text=hint, font=F_SMALL,
                             text_color="#8a8a8a").pack(side="left", padx=10)
            return w

        devs = core.fetch_devices()
        self.opt_dev = row("推理设备",
                           lambda p: ctk.CTkOptionMenu(
                               p, values=devs, width=260, font=F_BODY,
                               command=lambda _v: None),
                           "NPU 省电，CPU 更快但费电")
        cur = str(self.cfg.get("device", "NPU")).upper()
        self.opt_dev.set(cur if cur in devs else devs[0])

        seqvals = ["48", "64", "80", "96", "128", "160", "192", "256"]
        self.opt_enc = row("基准档位-源句",
                           lambda p: ctk.CTkOptionMenu(p, values=seqvals,
                                                       width=110, font=F_BODY))
        self.opt_dec = row("基准档位-解码",
                           lambda p: ctk.CTkOptionMenu(p, values=seqvals,
                                                       width=110, font=F_BODY))
        self.opt_enc.set(str(self.cfg.get("enc_seq", 64)))
        self.opt_dec.set(str(self.cfg.get("dec_seq", 64)))
        ctk.CTkLabel(wrap, text="档位越小越快、越省电；越大越不容易截断长句。",
                     font=F_SMALL, text_color="#8a8a8a").pack(anchor="w", padx=8)

        self.e_port = row("TCP 端口",
                          lambda p: ctk.CTkEntry(p, width=120, font=F_BODY),
                          "GUI / 客户端")
        self.e_port.insert(0, str(self.cfg.get("port", 8715)))
        self.e_http = row("HTTP 端口",
                          lambda p: ctk.CTkEntry(p, width=120, font=F_BODY),
                          "浏览器插件")
        self.e_http.insert(0, str(self.cfg.get("http_port", 8716)))

        ctk.CTkLabel(wrap, text="", font=F_SMALL).pack(pady=2)
        self.sw_auto = row("开机自启",
                           lambda p: ctk.CTkSwitch(p, text="", width=50))
        if self.cfg.get("autostart"):
            self.sw_auto.select()

        # 以后台服务运行：控制服务进程是否与界面生命周期解耦。
        # 开 = 关掉界面后服务继续常驻（可被插件/客户端继续调用）
        # 关 = 关掉界面时一并停止服务，不留后台进程
        self.sw_bg = row("后台服务",
                         lambda p: ctk.CTkSwitch(p, text="", width=50),
                         "关闭界面后服务继续常驻")
        if self.cfg.get("background_service", True):
            self.sw_bg.select()

        self.sw_tray = row("关闭最小化",
                           lambda p: ctk.CTkSwitch(p, text="", width=50),
                           "隐藏到托盘而不是退出")
        if self.cfg.get("minimize_to_tray", True):
            self.sw_tray.select()

        bar = ctk.CTkFrame(wrap, fg_color="transparent")
        bar.pack(fill="x", pady=16)
        ctk.CTkButton(bar, text="保存设置", width=120, height=38, font=F_H,
                      command=self._save_settings).pack(side="left")
        ctk.CTkButton(bar, text="恢复默认", width=110, height=38, font=F_BODY,
                      fg_color="#555555", hover_color="#666666",
                      command=self._reset_settings).pack(side="left", padx=10)
        self.lbl_set = ctk.CTkLabel(bar, text="", font=F_SMALL, text_color="#3ba55d")
        self.lbl_set.pack(side="left", padx=10)

        ctk.CTkLabel(wrap, text="修改设备/档位/端口后，需要重启服务才生效。",
                     font=F_SMALL, text_color="#8a8a8a").pack(anchor="w", pady=(4, 0))

    def _collect_settings(self):
        try:
            return {
                "device": self.opt_dev.get(),
                "enc_seq": int(self.opt_enc.get()),
                "dec_seq": int(self.opt_dec.get()),
                "port": int(self.e_port.get()),
                "http_port": int(self.e_http.get()),
                "autostart": bool(self.sw_auto.get()),
                "background_service": bool(self.sw_bg.get()),
                "minimize_to_tray": bool(self.sw_tray.get()),
            }
        except ValueError:
            return None

    def _save_settings(self):
        patch = self._collect_settings()
        if patch is None:
            self.lbl_set.configure(text="端口必须是数字", text_color="#e05a5a")
            return
        self.cfg = core.save_config(patch)
        self._apply_autostart(patch["autostart"])
        self._apply_background_mode(patch["background_service"])
        self.lbl_set.configure(text="已保存", text_color="#3ba55d")
        self.after(3000, lambda: self.lbl_set.configure(text=""))

    def _apply_background_mode(self, enable):
        """打开「后台服务」时立刻把服务拉起并保持常驻，符合开关语义。"""
        if not enable:
            return
        if core.is_service_running():
            return
        threading.Thread(target=lambda: (
            core.start_service(), core.wait_service_ready(),
            self.uiq.post(self._refresh_status)), daemon=True).start()

    def _reset_settings(self):
        self.cfg = core.save_config(core.DEFAULTS)
        d = core.DEFAULTS
        devs = core.fetch_devices()
        self.opt_dev.set(d["device"] if d["device"] in devs else devs[0])
        self.opt_enc.set(str(d["enc_seq"]))
        self.opt_dec.set(str(d["dec_seq"]))
        self.e_port.delete(0, "end"); self.e_port.insert(0, str(d["port"]))
        self.e_http.delete(0, "end"); self.e_http.insert(0, str(d["http_port"]))
        self.sw_auto.deselect()
        if d.get("background_service", True):
            self.sw_bg.select()
        else:
            self.sw_bg.deselect()
        self.sw_tray.select()
        self._apply_autostart(False)
        self.lbl_set.configure(text="已恢复默认", text_color="#3ba55d")

    # ------------------------------------------------------------ 自启
    def _apply_autostart(self, enable):
        try:
            startup = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                                   "Start Menu", "Programs", "Startup")
            lnk = os.path.join(startup, "NPU翻译.lnk")
            if not enable:
                if os.path.exists(lnk):
                    try:
                        os.remove(lnk)
                    except Exception:
                        core.run_hidden(["powershell", "-NoProfile", "-Command",
                                         f'Remove-Item -LiteralPath "{lnk}" -Force'],
                                        timeout=20)
                return
            target_cmd = core.service_cmd()
            if not target_cmd:
                print("[自启] 找不到服务程序，跳过")
                return
            target = target_cmd[0]
            args = " ".join(f'"{a}"' for a in target_cmd[1:])
            helper = os.path.join(core.APP_DIR, "_mk.ps1")
            with open(helper, "w", encoding="utf-8") as f:
                f.write("param($Lnk,$T,$A,$W)\n"
                        "$w=New-Object -ComObject WScript.Shell\n"
                        "$l=$w.CreateShortcut($Lnk)\n"
                        "$l.TargetPath=$T\n$l.Arguments=$A\n"
                        "$l.WorkingDirectory=$W\n$l.Save()\n")
            core.run_hidden(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                             "-File", helper, "-Lnk", lnk, "-T", target,
                             "-A", args, "-W", core.APP_DIR], timeout=30)
            try:
                os.remove(helper)
            except Exception:
                pass
        except Exception as e:
            print("[自启]", repr(e))

    # ------------------------------------------------------------ 模型页
    def _build_models(self, f):
        wrap = ctk.CTkScrollableFrame(f, fg_color="transparent")
        wrap.pack(fill="both", expand=True)

        ctk.CTkLabel(wrap, text="已安装模型", font=F_H).pack(anchor="w", pady=(8, 2))
        ctk.CTkLabel(wrap, text="选择模型后会重启服务生效。语言方向由模型决定。",
                     font=F_SMALL, text_color="#8a8a8a").pack(anchor="w", pady=(0, 8))

        # 列表区（每行一个模型）
        self.model_list = ctk.CTkFrame(wrap, corner_radius=10)
        self.model_list.pack(fill="x", pady=(0, 10))

        bar = ctk.CTkFrame(wrap, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 12))
        ctk.CTkButton(bar, text="刷新", width=90, height=34, font=F_BODY,
                      fg_color="#555555", hover_color="#666666",
                      command=self._refresh_models).pack(side="left")
        ctk.CTkButton(bar, text="打开模型目录", width=130, height=34, font=F_BODY,
                      fg_color="#555555", hover_color="#666666",
                      command=self._open_models_dir).pack(side="left", padx=8)
        self.lbl_model = ctk.CTkLabel(bar, text="", font=F_SMALL)
        self.lbl_model.pack(side="left", padx=8)

        ttk_sep = ctk.CTkLabel(wrap, text="", font=("", 2))
        ttk_sep.pack(pady=2)

        ctk.CTkLabel(wrap, text="下载模型", font=F_H).pack(anchor="w", pady=(6, 2))
        can = True
        try:
            import model_manager as mm
            can = mm.can_convert()
        except Exception:
            mm = None
        hint = ("下载后会自动转换为 OpenVINO 格式（每个约 445 MB，需联网，耗时几分钟）。"
                if can else
                "当前环境缺少转换工具（optimum-intel + torch），只能下载已导出的模型。")
        ctk.CTkLabel(wrap, text=hint, font=F_SMALL, text_color="#8a8a8a",
                     wraplength=640, justify="left").pack(anchor="w", pady=(0, 8))

        # 预设下拉 + 下载按钮
        self._mm = mm
        presets = [p["label"] for p in (mm.PRESETS if mm else [])]
        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        self.opt_preset = ctk.CTkOptionMenu(row, values=presets or ["（无）"],
                                            width=330, font=F_BODY)
        self.opt_preset.pack(side="left")
        self.btn_download = ctk.CTkButton(row, text="下载并转换", width=130, height=34,
                                          font=F_BODY, command=self._download_model)
        self.btn_download.pack(side="left", padx=10)
        self.lbl_dl = ctk.CTkLabel(row, text="", font=F_SMALL)
        self.lbl_dl.pack(side="left", padx=6)

        self.dl_log = ctk.CTkTextbox(wrap, font=("Consolas", 11), height=200)
        self.dl_log.pack(fill="x", pady=(4, 8))
        self.dl_log.configure(state="disabled")

        ctk.CTkLabel(wrap, text="提示：模型目录放在程序目录的 models\\ 下，"
                                "每个模型一个子目录，包含 openvino_*_model.xml/.bin 等文件。",
                     font=F_SMALL, text_color="#8a8a8a", wraplength=640,
                     justify="left").pack(anchor="w", pady=(4, 8))

        self._dl_stop = False
        self._refresh_models()

    def _open_models_dir(self):
        try:
            import model_manager as mm
            d = mm.MODELS_DIR
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception as e:
            print("[模型] 打开目录失败:", repr(e))

    def _refresh_models(self):
        for w in self.model_list.winfo_children():
            w.destroy()
        try:
            import model_manager as mm
            items = mm.list_installed()
        except Exception as e:
            ctk.CTkLabel(self.model_list, text=f"读取模型失败: {e}",
                         font=F_BODY, text_color="#e05a5a").pack(anchor="w", padx=14, pady=10)
            return
        if not items:
            ctk.CTkLabel(self.model_list, text="还没有安装任何模型。",
                         font=F_BODY, text_color="#8a8a8a").pack(anchor="w", padx=14, pady=12)
            return

        for it in items:
            row = ctk.CTkFrame(self.model_list, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=6)
            mark = "●" if it["active"] else "○"
            color = "#3ba55d" if it["active"] else "#8a8a8a"
            ctk.CTkLabel(row, text=mark, font=F_H, text_color=color,
                         width=22).pack(side="left")
            ctk.CTkLabel(row, text=it["label"], font=F_BODY,
                         width=250, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=f"{it['src']} → {it['dst']}", font=F_SMALL,
                         text_color="#8a8a8a", width=80,
                         anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=f"{it['size_mb']} MB", font=F_SMALL,
                         text_color="#8a8a8a", width=80,
                         anchor="w").pack(side="left")
            if it["active"]:
                ctk.CTkLabel(row, text="使用中", font=F_SMALL,
                             text_color="#3ba55d").pack(side="left")
            else:
                ctk.CTkButton(row, text="设为当前", width=90, height=30, font=F_SMALL,
                              command=lambda p=it["path"]: self._activate_model(p)
                              ).pack(side="left", padx=(6, 0))
                ctk.CTkButton(row, text="删除", width=60, height=30, font=F_SMALL,
                              fg_color="#7a3030", hover_color="#8d3a3a",
                              command=lambda p=it["path"], n=it["name"]:
                              self._delete_model(p, n)).pack(side="left", padx=6)

    def _activate_model(self, path):
        try:
            import model_manager as mm
            mm.set_active(path)
            self.cfg = core.load_config()
        except Exception as e:
            self.lbl_model.configure(text=f"切换失败: {e}", text_color="#e05a5a")
            return
        self.lbl_model.configure(text="已切换，正在重启服务…", text_color="#3ba55d")
        threading.Thread(target=self._activate_and_restart, daemon=True).start()

    def _activate_and_restart(self):
        core.stop_service()
        core.wait_service_ready(timeout=10)
        ok, msg = core.start_service()
        if ok:
            core.wait_service_ready()
        self.uiq.post(self._refresh_models)
        self.uiq.post(self._refresh_status)
        self.uiq.post(self.lbl_model.configure, {"text": "已切换并重启服务",
                                                 "text_color": "#3ba55d"})

    def _delete_model(self, path, name):
        if not messagebox.askyesno("删除模型", f"确定删除模型「{name}」？\n\n"
                                              f"{path}\n\n此操作不可撤销。"):
            return
        try:
            import model_manager as mm
            ok, msg = mm.delete_model(path)
        except Exception as e:
            ok, msg = False, str(e)
        self.lbl_model.configure(text=msg, text_color="#3ba55d" if ok else "#e05a5a")
        self._refresh_models()

    def _dl_log_write(self, line):
        self.dl_log.configure(state="normal")
        self.dl_log.insert("end", line + "\n")
        self.dl_log.see("end")
        self.dl_log.configure(state="disabled")

    def _download_model(self):
        if not self._mm:
            messagebox.showwarning("不可用", "模型管理模块未加载")
            return
        if not self._mm.can_convert():
            messagebox.showinfo("需要转换环境", self._mm.convert_hint())
            return
        label = self.opt_preset.get()
        preset = next((p for p in self._mm.PRESETS if p["label"] == label), None)
        if not preset:
            return
        if os.path.isdir(os.path.join(self._mm.MODELS_DIR, preset["dir"])) and \
                self._mm.is_valid_model_dir(os.path.join(self._mm.MODELS_DIR, preset["dir"])):
            if not messagebox.askyesno("已存在", "该模型目录已存在，重新下载会覆盖。继续？"):
                return

        self._dl_stop = False
        self.btn_download.configure(state="disabled", text="下载中…")
        self.lbl_dl.configure(text="开始…", text_color="#8a8a8a")
        self.dl_log.configure(state="normal")
        self.dl_log.delete("1.0", "end")
        self.dl_log.configure(state="disabled")

        def _run():
            def on_line(s):
                self.uiq.post(self._dl_log_write, s)
            ok, msg = self._mm.download_and_convert(
                preset, on_line=on_line, should_stop=lambda: self._dl_stop)
            self.uiq.post(self._dl_done, ok, msg)

        threading.Thread(target=_run, daemon=True).start()

    def _dl_done(self, ok, msg):
        self.btn_download.configure(state="normal", text="下载并转换")
        self.lbl_dl.configure(text=msg, text_color="#3ba55d" if ok else "#e05a5a")
        self._refresh_models()

    # ------------------------------------------------------------ 插件页
    def _build_plugin(self, f):
        cfg = self.cfg
        url = f"http://{cfg.get('host','127.0.0.1')}:{int(cfg.get('http_port',8716))}/translate"

        ctk.CTkLabel(f, text="kiss-translator 浏览器插件", font=F_H).pack(
            anchor="w", pady=(8, 2))
        ctk.CTkLabel(f, text="本服务已实现该插件的自定义接口规范，推理全部在本机 NPU 完成。",
                     font=F_SMALL, text_color="#8a8a8a").pack(anchor="w", pady=(0, 12))

        card = ctk.CTkFrame(f, corner_radius=10)
        card.pack(fill="x", pady=(0, 12))
        rows = [("URL", url), ("方法", "POST"), ("请求体", "留空"),
                ("Request Hook", "留空"), ("Response Hook", "留空")]
        for i, (k, v) in enumerate(rows):
            ctk.CTkLabel(card, text=k, font=F_SMALL, text_color="#8a8a8a",
                         width=110, anchor="w").grid(row=i, column=0, sticky="w",
                                                     padx=(16, 8), pady=4)
            e = ctk.CTkEntry(card, width=430, font=F_BODY)
            e.insert(0, v)
            e.configure(state="readonly")
            e.grid(row=i, column=1, sticky="w", pady=4, padx=(0, 16))

        bar = ctk.CTkFrame(f, fg_color="transparent")
        bar.pack(fill="x")
        ctk.CTkButton(bar, text="测试连接", width=110, height=36, font=F_BODY,
                      command=lambda: self._plugin_test(url)).pack(side="left")
        ctk.CTkButton(bar, text="复制 URL", width=100, height=36, font=F_BODY,
                      fg_color="#555555", hover_color="#666666",
                      command=lambda: self._copy(url)).pack(side="left", padx=8)
        self.lbl_pl = ctk.CTkLabel(bar, text="", font=F_SMALL)
        self.lbl_pl.pack(side="left", padx=10)

        ctk.CTkLabel(f, text="语言方向：中→英才真正翻译；其他方向原样返回，不报错。",
                     font=F_SMALL, text_color="#8a8a8a").pack(anchor="w", pady=(16, 0))

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    def _plugin_test(self, url):
        self.lbl_pl.configure(text="测试中…", text_color="#8a8a8a")

        def _run():
            import urllib.request
            try:
                body = json.dumps({"text": "今天天气真好", "from": "auto", "to": "en"},
                                  ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(url, data=body,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    obj = json.loads(r.read().decode("utf-8"))
                msg, col = f"OK · {obj.get('text','')[:40]}", "#3ba55d"
            except Exception as e:
                msg, col = f"失败：{type(e).__name__}", "#e05a5a"
            self.uiq.post(self.lbl_pl.configure, {"text": msg, "text_color": col})
        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------ 关于页
    def _build_about(self, f):
        txt = (
            "NPU 翻译\n"
            "基于 Intel AI Boost NPU 的本地中英翻译，省电、可常驻。\n\n"
            "实测性能（Core Ultra 9 275HX）\n"
            "  · 平均延迟 158 ms，P50 141 ms，最快 89 ms\n"
            "  · 吞吐 375 句/分钟\n"
            "  · 空闲 CPU 0%，常驻内存约 5 MB\n\n"
            "架构\n"
            "  界面进程只做交互；模型与推理在后台服务进程。\n"
            "  修改设备/档位/端口后需重启服务。\n\n"
            "协议\n"
            f"  · TCP  {self.cfg.get('port', 8715)}  换行分隔 JSON\n"
            f"  · HTTP {self.cfg.get('http_port', 8716)}  供浏览器插件\n\n"
            "文档：README.md / SERVICE.md / KISS.md"
        )
        box = ctk.CTkTextbox(f, font=F_BODY, wrap="word")
        box.pack(fill="both", expand=True, pady=8)
        box.insert("1.0", txt)
        box.configure(state="disabled")

    # ------------------------------------------------------------ 生命周期
    def _boot(self):
        """启动时：若配置要求且服务没起，自动拉起。"""
        self._refresh_status()
        if self.cfg.get("autostart_service", True) and not core.is_service_running():
            self._start_service()

    def _pump_loop(self):
        if self._destroyed:
            return
        self.uiq.pump()
        self.after(120, self._pump_loop)

    def _periodic(self):
        if self._destroyed:
            return
        self._refresh_status()
        self.after(5000, self._periodic)

    def _on_close(self):
        # 隐藏到托盘：窗口不可见，但进程、唤起监听、状态轮询都继续活着
        if self.cfg.get("minimize_to_tray", True):
            self.withdraw()
            self._ensure_tray()
            return

        # 真退出
        self._destroyed = True
        if not bool(self.cfg.get("background_service", True)):
            self._stop_service_on_exit()
        self.destroy()

    def _stop_service_on_exit(self):
        """退出前停服务（不阻塞界面关闭）。"""
        try:
            self.update_idletasks()
        except Exception:
            pass
        try:
            core.stop_service()
        except Exception as e:
            print("[退出] 停止服务失败:", repr(e))

    def _ensure_tray(self):
        """最小化到托盘：确保托盘进程在跑（tray.ps1 随程序分发）。"""
        try:
            if getattr(self, "_tray_started", False):
                return
            ps1 = os.path.join(core.APP_DIR, "tray.ps1")
            if not os.path.exists(ps1):
                print("[托盘] 找不到 tray.ps1")
                return
            # 已经在跑就别再起一个，否则托盘区会出现多个图标
            if core.tray_running():
                self._tray_started = True
                return
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                 "-ExecutionPolicy", "Bypass", "-File", ps1],
                cwd=core.APP_DIR, creationflags=0x08000000)  # CREATE_NO_WINDOW
            self._tray_started = True
        except Exception as e:
            print("[托盘]", repr(e))

    # ------------------------------------------------------------ 单实例
    def try_signal_existing(self, timeout=0.6):
        """如果已有实例在监听唤起端口，通知它显示窗口。返回 True 表示已存在。"""
        try:
            s = socket.create_connection(("127.0.0.1", SHOW_PORT), timeout=timeout)
            try:
                s.sendall(b"show\n")
            finally:
                s.close()
            return True
        except Exception:
            return False

    def start_show_listener(self):
        """监听唤起端口，收到 show 就把窗口叫出来。"""
        threading.Thread(target=self._show_listener, daemon=True).start()

    def _show_listener(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("127.0.0.1", SHOW_PORT))
            srv.listen(4)
            srv.settimeout(1.0)
        except Exception as e:
            print("[唤起] 监听失败:", repr(e))
            try:
                srv.close()
            except Exception:
                pass
            return
        while not self._destroyed:
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                data = conn.recv(64)
                if b"show" in data:
                    self.uiq.post(self.show_window)
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        try:
            srv.close()
        except Exception:
            pass

    def show_window(self):
        """把窗口从托盘/最小化状态叫回前台。"""
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
            self.attributes("-topmost", True)
            self.after(300, lambda: self.attributes("-topmost", False))
        except Exception as e:
            print("[唤起]", repr(e))


def main():
    # 单实例：若已有界面在跑（可能已隐藏到托盘），把它叫出来就退出，
    # 避免「托盘点打开界面」变成开第二个窗口。
    app = App()
    if app.try_signal_existing():
        app.destroy()
        return
    app.start_show_listener()
    app.after(1000, app._periodic)
    app.mainloop()


if __name__ == "__main__":
    main()
