#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大模型对比实测：Marian 77M（NPU） vs m2m100 418M（CPU） vs NLLB 600M（CPU）"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json

CHOICE_ZH_EN = [
    "今天天气真好，我们一起去公园散步吧。",
    "这个项目的进度比预期慢了一些，主要是因为上游接口交付延迟，而且测试环境一直不稳定。",
    "这件事不能画蛇添足，按原方案做就行。",
    "我们把模型量化成 FP16 之后，在 NPU 上的推理延迟下降了一半。",
    "订单号 A2024-0917，金额 3,280.50 元，请在 10 月 25 日前确认。",
    "我喜欢上一个人。",
    "他没有按照约定提交报告，所以上周的评审会没有开成。",
]

CHOICE_EN_ZH = [
    "The project is running a bit behind schedule, mainly because the upstream API was delivered late.",
    "Let's not gild the lily; just follow the original plan.",
    "After we quantized the model to FP16, inference latency on the NPU dropped by half.",
    "Order A2024-0917, amount 3,280.50 CNY, please confirm before October 25.",
    "This issue has already been fixed, but the patch hasn't been released yet.",
]

ROOT = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(ROOT, "models")
BUILD = os.path.join(ROOT, "build", "models")


def setup_meta(path, meta):
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


class Runner:
    def __init__(self, name, path, device):
        import translate as T
        self.T = T
        self.name = name
        self.path = path
        self.device = device
        t0 = time.time()
        self.compiled, self.tok = T.load_static_multi(path, device)
        self.load_s = time.time() - t0

    def run(self, text, src=None, tgt=None):
        T = self.T
        tier = T._pick_tier(self.compiled, text, self.tok)
        enc_c, dec_c = self.compiled[tier]
        t0 = time.time()
        out, _ = T.translate_static(enc_c, dec_c, self.tok, text, model_dir=self.path,
                                    src_lang=src, tgt_lang=tgt, max_new_tokens=tier[1] if isinstance(tier, tuple) else 96)
        return out, time.time() - t0


def main():
    runners = []

    # Marian 77M：NPU
    p = os.path.join(MODELS, "opus-mt-zh-en")
    runners.append(("Marian 77M (NPU)", Runner("marian_zh", p, "NPU"), "zh", "en"))

    # m2m100 418M：CPU
    p = os.path.join(BUILD, "_m2m418")
    setup_meta(p, {"src": "*", "dst": "*", "arch": "m2m100", "device": "CPU",
                   "label": "m2m100-418M", "precision": "FP16"})
    runners.append(("m2m100 418M (CPU)", Runner("m2m", p, "CPU"), "zh", "en"))

    # NLLB 600M：CPU
    p = os.path.join(BUILD, "_nllb600")
    setup_meta(p, {"src": "*", "dst": "*", "arch": "nllb", "device": "CPU",
                   "label": "nllb-600M", "precision": "FP16"})
    runners.append(("NLLB 600M (CPU)", Runner("nllb", p, "CPU"), "zh", "en"))

    print("加载耗时:", ", ".join(f"{n} {r.load_s:.0f}s" for n, r, _, _ in runners))
    print()
    print("=" * 110)
    print("中文 → 英文")
    print("=" * 110)
    for s in CHOICE_ZH_EN:
        print(f"\n{s}")
        for name, r, src, tgt in runners:
            try:
                out, dt = r.run(s, src, tgt)
                print(f"  {name:20s} {dt*1000:6.0f} ms  {out}")
            except Exception as e:
                print(f"  {name:20s} ❌ {type(e).__name__}: {str(e)[:120]}")

    print()
    print("=" * 110)
    print("英文 → 中文")
    print("=" * 110)
    for s in CHOICE_EN_ZH:
        print(f"\n{s}")
        for name, r, src, tgt in runners:
            try:
                out, dt = r.run(s, tgt, src)   # 反向：en→zh
                print(f"  {name:20s} {dt*1000:6.0f} ms  {out}")
            except Exception as e:
                print(f"  {name:20s} ❌ {type(e).__name__}: {str(e)[:120]}")


if __name__ == "__main__":
    main()
