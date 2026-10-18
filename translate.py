#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
极轻量翻译 POC：用 OpenVINO 在 Intel NPU / CPU 上跑 MarianMT (opus-mt-zh-en)。
设计目标：省电 / 可常驻（batch=1、无 KV 缓存、单句、低算力）。

Intel NPU (VPUX) 编译器的硬性约束（踩坑记录）：
  1. 所有动态维度必须有显式上界，否则报
     [NPU_VCL] Compiler returned msg: Got negative shape dim bound: '-1'
  2. 关键坑：PartialShape 上的 Dimension(1, 512) 这种「有界动态」的上界
     **不会传播到图内部节点**，内部仍是 int64-max (9223372036854775807)，
     NPU 依然拒绝。实测只有把输入 reshape 成**完全静态**的 [1, 512]
     才能编译通过（见 npu-smoke.py / npu-smoke.log）。
  3. decoder_with_past（带 KV 缓存）的 cache 维度同样是 int64-max，无法 NPU 编译，
     因此弃用缓存，走 decoder（非 past）每步重算路径。

实现策略（static 路径，NPU/CPU 通用）：
  - encoder 固定 [1, ENC_SEQ]，decoder 固定 [1, DEC_SEQ]，全静态编译一次。
  - 源句右侧 pad 到 ENC_SEQ，注意力 mask 屏蔽 pad。
  - 解码时把已生成序列右侧 pad 到 DEC_SEQ，取 logits[0, len-1] 作下一词预测
    （因果注意力保证右侧 padding 不影响该位置）。
  - 贪心解码，无 beam search —— 常驻省电优先，短句质量足够。

dynamic 路径（仅 CPU 对照基准）：用动态形状，避免 padding 浪费。
"""
import argparse
import json
import os
import time

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

MODELS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

# 模型目录名一律用原始精简名（如 opus-mt-zh-en），不再带 -ov 后缀
MODEL_DIR = os.path.join(MODELS_ROOT, "opus-mt-zh-en")


def resolve_model_dir(preferred=None):
    """解析实际可用的模型目录。

    优先级：显式指定 → 约定的默认名 → models\\ 下第一个含 encoder IR 的目录。
    这样只下载了 FP32 版（opus-mt-zh-en-fp32）也能直接跑起来。
    """
    cands = []
    if preferred:
        cands.append(preferred if os.path.isabs(preferred)
                     else os.path.join(os.path.dirname(os.path.abspath(__file__)), preferred))
    cands.append(MODEL_DIR)
    for c in cands:
        if os.path.exists(os.path.join(c, "openvino_encoder_model.xml")):
            return c
    try:
        for name in sorted(os.listdir(MODELS_ROOT)):
            d = os.path.join(MODELS_ROOT, name)
            if os.path.isdir(d) and os.path.exists(
                    os.path.join(d, "openvino_encoder_model.xml")):
                return d
    except OSError:
        pass
    return cands[0] if cands else MODEL_DIR


ENC_SEQ = 64       # 源句固定长度（中文短句通常 < 30 token；实测 64/64 比 128/128 快 38%）
DEC_SEQ = 64       # 解码固定长度（英文短句通常 < 40 token）
ENC_SEQ_MAX = 128  # 超长句回退档位（见 _seq_tiers）
DEC_SEQ_MAX = 128
HIDDEN = 512       # 默认 d_model（多数 opus-mt 是 512）；实际以模型 config.json 为准


def hidden_size(model_dir=None):
    """读模型真实的 d_model。不同 opus-mt 模型可能不同（512 / 1024），
    写死会在 reshape 时报 512 vs 1024 不匹配。"""
    try:
        return int(_cfg(model_dir).get("d_model") or HIDDEN)
    except Exception:
        return HIDDEN
MAX_NEW_TOKENS = 96


def _seq_tiers():
    """按句长分档，减少 padding 浪费（NPU 全静态形状的代价就在这）。
    实测 NPU 延迟：64/64 = 153 ms，96/96 = 190 ms，128/128 = 248 ms。"""
    return [(64, 64), (96, 96), (128, 128)]


def _cfg(model_dir=None):
    """读模型目录里的 config.json（Marian 的 d_model / 特殊 token id）。
    打包后模块级 MODEL_DIR 指向 _internal，可能不存在；
    调用方（服务）会显式传入模型目录，避免依赖 __file__。"""
    d = model_dir or MODEL_DIR
    with open(os.path.join(d, "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def _pad_to(arr, length, pad_id):
    """右侧 padding 到固定长度，返回 [1, length] int64。"""
    out = np.full((1, length), pad_id, dtype=np.int64)
    n = min(len(arr), length)
    out[0, :n] = arr[:n]
    return out, n


# ---------------------------------------------------------------- static 路径
def load_static(model_dir, device, enc_seq=ENC_SEQ, dec_seq=DEC_SEQ):
    """全静态形状编译 encoder/decoder（NPU 必需，CPU 亦可用）。"""
    import openvino as ov
    from transformers import AutoTokenizer

    device = device.upper()
    print(f"  [static] 加载并编译到 {device} (enc={enc_seq}, dec={dec_seq}) ...", flush=True)
    t0 = time.time()
    core = ov.Core()

    enc_m = core.read_model(os.path.join(model_dir, "openvino_encoder_model.xml"))
    dec_m = core.read_model(os.path.join(model_dir, "openvino_decoder_model.xml"))

    # 全静态：batch=1，序列固定，隐藏维度保持 512
    enc_m.reshape({
        "input_ids": ov.PartialShape([1, enc_seq]),
        "attention_mask": ov.PartialShape([1, enc_seq]),
    })
    dec_m.reshape({
        "encoder_attention_mask": ov.PartialShape([1, enc_seq]),
        "input_ids": ov.PartialShape([1, dec_seq]),
        "encoder_hidden_states": ov.PartialShape([1, enc_seq, hidden_size(model_dir)]),
    })

    cfg = {"PERFORMANCE_HINT": "LATENCY"} if device == "NPU" else {}
    enc_c = core.compile_model(enc_m, device, cfg)
    dec_c = core.compile_model(dec_m, device, cfg)
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    print(f"  [static] 编译完成，耗时 {time.time()-t0:.2f}s", flush=True)
    return enc_c, dec_c, tokenizer


def _patch_spm_loader():
    """修复 sentencepiece 在中文路径下的加载失败。

    坑：sentencepiece 的 C++ 侧 LoadFromFile 在 Windows 使用 ANSI 窄字符路径，
    程序目录含中文（如「NPU翻译」）时会误报
        NOT_FOUND: "...\\source.spm": No such file or directory
    即使文件确实存在。改用「Python 读字节 + LoadFromSerializedProto」绕开。
    """
    try:
        import sentencepiece
        import transformers.models.marian.tokenization_marian as tm
        if getattr(tm, "_nput_spm_patched", False):
            return
        _OrigProcessor = sentencepiece.SentencePieceProcessor

        def load_spm(path, sp_model_kwargs):
            sp = _OrigProcessor(**(sp_model_kwargs or {}))
            with open(path, "rb") as f:          # Python 的 open 支持 Unicode 路径
                data = f.read()
            sp.LoadFromSerializedProto(data)     # 不经过 C++ 的窄字符文件 API
            return sp

        tm.load_spm = load_spm
        tm._nput_spm_patched = True
        print("  [tokenizer] 已启用 UTF-8 路径安全的 SPM 加载器", flush=True)
    except Exception as e:
        print(f"  [tokenizer] SPM 加载器补丁失败: {e!r}", flush=True)


def _load_tokenizer(model_dir):
    """加载 Marian 分词器。

    坑（PyInstaller 打包后）：transformers 的 PreTrainedTokenizerBase.from_pretrained
    在构造 tokenizer 抛异常时会 `except import_protobuf_decode_error(): return False`，
    把真实异常吞掉，最终拿到一个 bool。因此这里**直接构造** MarianTokenizer，
    让真实异常暴露；再退回 from_pretrained 作为兜底。
    """
    import traceback
    diag = []
    try:
        import transformers
        diag.append(f"transformers={getattr(transformers, '__version__', '?')}")
    except Exception as e:
        diag.append(f"transformers import 失败: {e!r}")
    try:
        import sentencepiece
        diag.append(f"sentencepiece={getattr(sentencepiece, '__version__', '?')}")
    except Exception as e:
        diag.append(f"sentencepiece 失败: {e!r}")

    def _ok(tk):
        return tk is not None and callable(tk) and hasattr(tk, "decode") \
            and hasattr(tk, "pad_token_id")

    _patch_spm_loader()

    # 1) 直接构造（推荐路径，绕过 from_pretrained 的异常吞噬）
    try:
        from transformers.models.marian.tokenization_marian import MarianTokenizer
        cand = MarianTokenizer(
            vocab=os.path.join(model_dir, "vocab.json"),
            source_spm=os.path.join(model_dir, "source.spm"),
            target_spm=os.path.join(model_dir, "target.spm"),
        )
        if _ok(cand):
            print(f"  [tokenizer] 直接构造成功 -> {type(cand).__name__}", flush=True)
            return cand
        diag.append(f"直接构造返回 {type(cand).__name__}")
    except Exception:
        diag.append("直接构造失败:\n" + traceback.format_exc()[-900:])

    # 2) 退回 from_pretrained（如果上面失败且真实异常已被修好）
    for name in ("MarianTokenizer", "AutoTokenizer"):
        try:
            import transformers
            cls = getattr(transformers, name)
            cand = cls.from_pretrained(model_dir)
            diag.append(f"{name}.from_pretrained -> {type(cand).__name__}")
            if _ok(cand):
                print(f"  [tokenizer] {name}.from_pretrained 成功", flush=True)
                return cand
        except Exception:
            diag.append(f"{name}.from_pretrained 异常:\n" + traceback.format_exc()[-600:])

    msg = "tokenizer 加载失败 || " + " || ".join(diag)
    print("  [tokenizer] " + msg, flush=True)
    raise RuntimeError(msg)


def load_static_multi(model_dir, device, tiers=None):
    """预编译多档静态形状（NPU 全静态的代价是 padding 浪费，故按句长分档）。
    返回 {tier_key: (enc_compiled, dec_compiled)} 与 tokenizer。
    tier_key 形如 '64x64'。"""
    tiers = tiers or _seq_tiers()
    import openvino as ov

    device = device.upper()
    print(f"  [static-multi] 加载并编译 {len(tiers)} 档到 {device}: {tiers}", flush=True)
    t0 = time.time()
    core = ov.Core()
    cfg = {"PERFORMANCE_HINT": "LATENCY"} if device == "NPU" else {}
    tokenizer = _load_tokenizer(model_dir)
    print(f"  [tokenizer] {type(tokenizer).__name__}", flush=True)
    compiled = {}
    for enc_seq, dec_seq in tiers:
        enc_m = core.read_model(os.path.join(model_dir, "openvino_encoder_model.xml"))
        dec_m = core.read_model(os.path.join(model_dir, "openvino_decoder_model.xml"))
        enc_m.reshape({
            "input_ids": ov.PartialShape([1, enc_seq]),
            "attention_mask": ov.PartialShape([1, enc_seq]),
        })
        dec_m.reshape({
            "encoder_attention_mask": ov.PartialShape([1, enc_seq]),
            "input_ids": ov.PartialShape([1, dec_seq]),
            "encoder_hidden_states": ov.PartialShape([1, enc_seq, hidden_size(model_dir)]),
        })
        compiled[f"{enc_seq}x{dec_seq}"] = (
            core.compile_model(enc_m, device, cfg),
            core.compile_model(dec_m, device, cfg),
        )
        print(f"  [static-multi] {enc_seq}x{dec_seq} 编译完成 ({time.time()-t0:.1f}s)", flush=True)
    print(f"  [static-multi] 全部就绪，耗时 {time.time()-t0:.2f}s", flush=True)
    return compiled, tokenizer


def _pick_tier(compiled, text, tokenizer):
    """按源句 token 数选最短可用档；超出最大档则用最大档并截断。"""
    n = len(tokenizer(text)["input_ids"])
    best = None
    for key, (enc_c, _) in compiled.items():
        cap = enc_c.inputs[0].shape[1]
        if n <= cap:
            if best is None or cap < compiled[best][0].inputs[0].shape[1]:
                best = key
    return best or max(compiled, key=lambda k: compiled[k][0].inputs[0].shape[1])


def translate_static(enc_c, dec_c, tokenizer, text, max_new_tokens=MAX_NEW_TOKENS,
                     model_dir=None):
    cfg = _cfg(model_dir)
    pad_id = cfg.get("pad_token_id", 65000)
    dec_start = cfg.get("decoder_start_token_id", 65000)
    eos_id = cfg.get("eos_token_id", 0)

    enc = tokenizer(text, return_tensors="np", truncation=True, max_length=ENC_SEQ_MAX)
    ids = np.asarray(enc["input_ids"])[0]
    mask = np.asarray(enc["attention_mask"])[0]

    enc_len = enc_c.inputs[0].shape[1]
    dec_len = dec_c.inputs[1].shape[1]

    t0 = time.time()
    src_ids, src_len = _pad_to(ids, enc_len, pad_id)
    src_mask, _ = _pad_to(mask, enc_c.inputs[1].shape[1], 0)
    enc_hidden = enc_c({"input_ids": src_ids, "attention_mask": src_mask})["last_hidden_state"]

    seq = [int(dec_start)]
    out_ids = []
    for _ in range(max_new_tokens):
        dec_in, n = _pad_to(seq, dec_len, pad_id)
        logits = dec_c({
            "encoder_attention_mask": src_mask,
            "input_ids": dec_in,
            "encoder_hidden_states": enc_hidden,
        })["logits"]
        nxt = int(np.argmax(logits[0, min(n, dec_len) - 1]))
        if nxt == eos_id:
            break
        seq.append(nxt)
        out_ids.append(nxt)
        if len(seq) >= dec_len:
            print(f"  [警告] 达到解码上限 {dec_len}，截断。", flush=True)
            break

    elapsed = time.time() - t0
    return tokenizer.decode(out_ids, skip_special_tokens=True), elapsed


# ---------------------------------------------------------------- dynamic 路径（CPU 基准）
def load_dynamic(model_dir, device="CPU"):
    """动态形状（仅 CPU 对照用；NPU 会因 -1 上界缺失而编译失败）。"""
    import openvino as ov
    from transformers import AutoTokenizer
    core = ov.Core()
    enc_m = core.read_model(os.path.join(model_dir, "openvino_encoder_model.xml"))
    dec_m = core.read_model(os.path.join(model_dir, "openvino_decoder_model.xml"))
    dec_m.reshape({
        "encoder_attention_mask": ov.PartialShape([-1, -1]),
        "input_ids": ov.PartialShape([-1, -1]),
        "encoder_hidden_states": ov.PartialShape([-1, -1, hidden_size(model_dir)]),
    })
    enc_c = core.compile_model(enc_m, device)
    dec_c = core.compile_model(dec_m, device)
    return enc_c, dec_c, AutoTokenizer.from_pretrained(model_dir)


def translate_dynamic(enc_c, dec_c, tokenizer, text, max_new_tokens=MAX_NEW_TOKENS,
                      model_dir=None):
    cfg = _cfg(model_dir)
    dec_start = cfg.get("decoder_start_token_id", 65000)
    eos_id = cfg.get("eos_token_id", 0)

    enc = tokenizer(text, return_tensors="np", truncation=True, max_length=512)
    src_ids = np.asarray(enc["input_ids"], dtype=np.int64)
    src_mask = np.asarray(enc["attention_mask"], dtype=np.int64)

    t0 = time.time()
    enc_hidden = enc_c({"input_ids": src_ids, "attention_mask": src_mask})["last_hidden_state"]
    seq = [int(dec_start)]
    out_ids = []
    for _ in range(max_new_tokens):
        dec_in = np.array([seq], dtype=np.int64)
        logits = dec_c({
            "encoder_attention_mask": src_mask,
            "input_ids": dec_in,
            "encoder_hidden_states": enc_hidden,
        })["logits"]
        nxt = int(np.argmax(logits[0, -1]))
        if nxt == eos_id:
            break
        seq.append(nxt)
        out_ids.append(nxt)
    elapsed = time.time() - t0
    return tokenizer.decode(out_ids, skip_special_tokens=True), elapsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL_DIR)
    ap.add_argument("--device", default="NPU", choices=["NPU", "CPU"])
    ap.add_argument("--path", default="static", choices=["static", "dynamic"],
                    help="static=全静态形状（NPU 必需）；dynamic=动态形状（仅 CPU 基准）")
    ap.add_argument("--text", default="今天天气真好，我们一起去公园散步吧。")
    args = ap.parse_args()

    print(f"== 设备: {args.device} | 路径: {args.path} ==", flush=True)
    if args.path == "static":
        compiled, tok = load_static_multi(args.model, args.device)
        tier = _pick_tier(compiled, args.text, tok)
        enc_c, dec_c = compiled[tier]
        print(f"  选用档位 {tier}", flush=True)
        pred, el = translate_static(enc_c, dec_c, tok, args.text)
    else:
        enc_c, dec_c, tok = load_dynamic(args.model, args.device)
        pred, el = translate_dynamic(enc_c, dec_c, tok, args.text)
    print(f"  原文: {args.text}")
    print(f"  译文: {pred}")
    print(f"  耗时: {el:.3f}s")


if __name__ == "__main__":
    main()

