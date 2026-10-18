#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""中英互译质量诊断：对比我们（FP16 IR + 贪心）与 HF 官方（beam=4）的输出差异。

输出每条句子的：
  原文 / 我们的译文 / HF beam=4 译文 / 标记（是否一致、是否被长度截断）
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CASES_ZH_EN = [
    # (类别, 中文)
    ("日常短句", "今天天气真好，我们一起去公园散步吧。"),
    ("日常短句", "会议室在三楼，电梯出来左转。"),
    ("长句", "这个项目的进度比预期慢了一些，主要是因为上游接口交付延迟，而且测试环境一直不稳定。"),
    ("超长句", "由于上游接口的交付时间比原计划晚了两周，加上测试环境频繁出现不稳定情况，"
              "整个项目的进度受到了比较大的影响，我们预计需要在下个迭代中补回这部分时间，"
              "同时也会和供应商重新确认后续的交付节奏。"),
    ("成语/习语", "这件事不能画蛇添足，按原方案做就行。"),
    ("技术术语", "我们把模型量化成 FP16 之后，在 NPU 上的推理延迟下降了一半。"),
    ("数字日期", "订单号 A2024-0917，金额 3,280.50 元，请在 10 月 25 日前确认。"),
    ("否定时态", "他没有按照约定提交报告，所以上周的评审会没有开成。"),
    ("专有名词", "请把这个文件发给张伟，抄送 Lina 和王工。"),
    ("口语/省略", "算了，先这样吧，回头再说。"),
    ("歧义", "我喜欢上一个人。"),
    ("被动语态", "这个问题已经被修复了，但补丁还没有发布。"),
]

CASES_EN_ZH = [
    ("日常短句", "The meeting room is on the third floor; turn left when you exit the elevator."),
    ("长句", "The project is running a bit behind schedule, mainly because the upstream API was "
             "delivered late and the test environment has been unstable."),
    ("习语", "Let's not gild the lily; just follow the original plan."),
    ("技术术语", "After we quantized the model to FP16, inference latency on the NPU dropped by half."),
    ("数字日期", "Order A2024-0917, amount 3,280.50 CNY, please confirm before October 25."),
    ("否定时态", "He didn't submit the report as agreed, so last week's review meeting didn't happen."),
    ("被动语态", "This issue has already been fixed, but the patch hasn't been released yet."),
]


def load_ours(models_dir, name):
    import translate as T
    p = os.path.join(models_dir, name)
    compiled, tok = T.load_static_multi(p, "NPU")
    return p, compiled, tok, T


def run_ours(T, compiled, tok, p, text):
    tier = T._pick_tier(compiled, text, tok)
    enc_c, dec_c = compiled[tier]
    out, ms = T.translate_static(enc_c, dec_c, tok, text, model_dir=p)
    return out, ms, tier


def load_hf(repo):
    import torch
    from transformers import MarianMTModel, MarianTokenizer
    mt = MarianMTModel.from_pretrained(repo).eval()
    tk = MarianTokenizer.from_pretrained(repo)
    return mt, tk


def run_hf(mt, tk, text, beams=4):
    import torch
    b = tk([text], return_tensors="pt", truncation=True, max_length=512, padding=True)
    with torch.no_grad():
        out = mt.generate(**b, num_beams=beams, max_new_tokens=128)
    return tk.batch_decode(out, skip_special_tokens=True)[0]


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    models = os.path.join(root, "models")

    print("=" * 100)
    print("中文 → 英文")
    print("=" * 100)
    p, compiled, tok, T = load_ours(models, "opus-mt-zh-en")
    mt, tk = load_hf("Helsinki-NLP/opus-mt-zh-en")
    for cat, s in CASES_ZH_EN:
        ours, ms, tier = run_ours(T, compiled, tok, p, s)
        ref = run_hf(mt, tk, s)
        flag = "一致" if ours.strip() == ref.strip() else "不同"
        trunc = " [可能截断]" if len(ours.split()) >= 60 else ""
        print(f"\n[{cat}] {s}")
        print(f"  我们  ({ms*1000:5.0f} ms, {tier}): {ours}{trunc}")
        print(f"  HF beam=4           : {ref}")
        print(f"  → {flag}")

    print()
    print("=" * 100)
    print("英文 → 中文")
    print("=" * 100)
    p2, compiled2, tok2, T2 = load_ours(models, "opus-mt-en-zh")
    mt2, tk2 = load_hf("Helsinki-NLP/opus-mt-en-zh")
    for cat, s in CASES_EN_ZH:
        ours, ms, tier = run_ours(T2, compiled2, tok2, p2, s)
        ref = run_hf(mt2, tk2, s)
        flag = "一致" if ours.strip() == ref.strip() else "不同"
        print(f"\n[{cat}] {s}")
        print(f"  我们  ({ms*1000:5.0f} ms, {tier}): {ours}")
        print(f"  HF beam=4           : {ref}")
        print(f"  → {flag}")


if __name__ == "__main__":
    main()
