#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""NPU vs CPU 延迟/吞吐对比（不含功耗采样）。
实测功耗需拔电池，见 README；此处只量化「NPU 单句推理有多轻」。
用法: python bench.py --device NPU --seconds 30
"""
import argparse
import json
import os
import time
import traceback

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

ROOT = os.path.dirname(os.path.abspath(__file__))
LOGFILE = os.path.join(ROOT, "bench.log")
_lf = open(LOGFILE, "a", encoding="utf-8")


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    print(s, file=_lf, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="NPU", choices=["NPU", "CPU"])
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--sentences", default=os.path.join(ROOT, "bench_sentences.json"))
    args = ap.parse_args()

    try:
        from translate import load_static_multi, translate_static, _pick_tier
        with open(args.sentences, "r", encoding="utf-8") as f:
            sentences = json.load(f)

        log(f"\n===== 基准: {args.device} | 时长 {args.seconds}s | 句数 {len(sentences)} =====")
        compiled, tok = load_static_multi(os.path.join(ROOT, "models", "opus-mt-zh-en-ov"), args.device)
        tiers_used = {}

        # 预热（NPU 首次推理含驱动/JIT 开销，不计入）
        for key, (e, d) in compiled.items():
            translate_static(e, d, tok, "预热句子。")
        log("  预热完成，开始计时")

        lat, errs = [], []
        done = idx = 0
        t_start = time.time()
        while time.time() - t_start < args.seconds:
            text = sentences[idx % len(sentences)]
            try:
                tier = _pick_tier(compiled, text, tok)
                enc_c, dec_c = compiled[tier]
                tiers_used[tier] = tiers_used.get(tier, 0) + 1
                _, el = translate_static(enc_c, dec_c, tok, text)
                lat.append(el)
                done += 1
            except Exception as e:
                errs.append(f"{type(e).__name__}: {e}")
                log("  [翻译异常]", errs[-1])
            idx += 1
        total = time.time() - t_start

        srt = sorted(lat)
        n = len(srt)
        log(f"  完成     : {done} 句 / {total:.1f}s = {done/total*60:.1f} 句/分钟")
        log(f"  档位分布 : {tiers_used}")
        if n:
            log(f"  延迟均值 : {sum(srt)/n*1000:.0f} ms")
            log(f"  延迟 P50  : {srt[n//2]*1000:.0f} ms")
            log(f"  延迟最小 : {min(srt)*1000:.0f} ms | 最大 {max(srt)*1000:.0f} ms")
        if errs:
            log(f"  异常     : {len(errs)} 次，首条 {errs[0]}")
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
