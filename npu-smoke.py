#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""NPU 烟雾测试：原生 OpenVINO 读取 IR，探查动态维度并尝固定长编译 NPU。
每一步直接写日志文件并 flush，避免 C++ 层崩溃丢失 stdout。
"""
import os
import sys
import time

os.environ.setdefault("OPENVINO_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

LOG = os.path.join(os.path.dirname(__file__), "npu-smoke.log")
lf = open(LOG, "w", encoding="utf-8")


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    print(s, file=lf, flush=True)


try:
    import openvino as ov
    log("openvino imported:", ov.__version__)
except Exception as e:
    log("import openvino 失败:", repr(e))
    sys.exit(2)

OV = os.path.join(os.path.dirname(__file__), "models", "opus-mt-zh-en-ov")
ENC = os.path.join(OV, "openvino_encoder_model.xml")
DEC = os.path.join(OV, "openvino_decoder_model.xml")

try:
    core = ov.Core()
    log("devices:", core.available_devices)
except Exception as e:
    log("Core() 失败:", repr(e))
    sys.exit(3)


def probe_and_compile(path, seq_len=512, tag=""):
    log(f"\n=== {tag} {os.path.basename(path)} ===")
    try:
        m = core.read_model(path)
        log("  inputs:")
        for i in m.inputs:
            log(f"    {i.any_name}: {i.get_partial_shape()}")
        log("  outputs:")
        for o in m.outputs:
            log(f"    {o.any_name}: {o.get_partial_shape()}")
        shapes = {}
        for i in m.inputs:
            ps = i.get_partial_shape()
            pos = 0
            new = []
            for d in ps:
                if d.is_dynamic:
                    new.append(1 if pos == 0 else seq_len)
                else:
                    new.append(int(d))
                pos += 1
            shapes[i] = new
        log("  reshape ->", {k.any_name: v for k, v in shapes.items()})
        m.reshape(shapes)
        log("  reshape 成功")
        for i in m.inputs:
            log(f"    after {i.any_name}: {i.get_partial_shape()}")
        t0 = time.time()
        comp = core.compile_model(m, "NPU", {"PERFORMANCE_HINT": "LATENCY"})
        log(f"  NPU 编译成功 ({time.time()-t0:.1f}s)，inputs:", [i.any_name for i in comp.inputs])
        return comp
    except Exception as e:
        log(f"  NPU 编译/reshape 失败: {type(e).__name__}: {str(e)[:200]}")
        return None


try:
    enc_c = probe_and_compile(ENC, tag="encoder")
    dec_c = probe_and_compile(DEC, tag="decoder")
    log("\n=== 结论 ===")
    log(f"encoder NPU: {'OK' if enc_c else 'FAIL'}")
    log(f"decoder NPU: {'OK' if dec_c else 'FAIL'}")
except Exception as e:
    log("未预期异常:", repr(e))
log("DONE")
