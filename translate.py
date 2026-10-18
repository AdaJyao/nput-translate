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


def _read_meta(model_dir):
    """读模型目录里的 meta.json（多语种模型用它标记 arch / 语言代码映射）。"""
    try:
        with open(os.path.join(model_dir, "meta.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_tokenizer(model_dir):
    """加载分词器。

    - Marian 系（有 vocab.json + source.spm）：直接构造 MarianTokenizer
      （坑：PyInstaller 打包后 from_pretrained 会把真实异常吞成 False）
    - 多语种模型（m2m100 / NLLB，只有一个 sentencepiece.bpe.model）：用 AutoTokenizer
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

    # 0) 多语种模型：m2m100 / nllb 只有一个合并的 spm
    if os.path.exists(os.path.join(model_dir, "sentencepiece.bpe.model")) or \
       os.path.exists(os.path.join(model_dir, "tokenizer.json")):
        try:
            import transformers
            cand = transformers.AutoTokenizer.from_pretrained(model_dir)
            if _ok(cand):
                print(f"  [tokenizer] 多语种 AutoTokenizer -> {type(cand).__name__}", flush=True)
                return cand
        except Exception:
            diag.append("AutoTokenizer(多语种) 失败:\n" + traceback.format_exc()[-600:])

    # 1) 直接构造（推荐路径，绕过 from_pretrained 的异常吞噬）
    try:
        from transformers.models.marian.tokenization_marian import MarianTokenizer
        cand = MarianTokenizer(
            vocab=os.path.join(model_dir, "vocab.json"),
            source_spm=os.path.join(model_dir, "source.spm"),
            target_spm=os.path.join(model_dir, "target_spm"),
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


def _gen_constraints(cfg, model_dir=None):
    """读取生成约束（部分模型在 generation_config.json 里禁用某些 token）。

    典型例子：opus-mt-mul-en 的 generation_config 里 bad_words_ids=[[pad]]，
    不禁用的话贪心解码第一步就会选 pad，整句输出为空。
    返回 (禁止 token 集合, 强制 eos token)。
    """
    bad = set()
    forced_eos = None
    # generation_config.json 优先（HF 在 4.x 起把生成参数挪到这里）
    try:
        p = os.path.join(model_dir or MODEL_DIR, "generation_config.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                gc = json.load(f)
            for group in (gc.get("bad_words_ids") or []):
                for t in (group or []):
                    bad.add(int(t))
            if gc.get("forced_eos_token_id") is not None:
                forced_eos = int(gc["forced_eos_token_id"])
    except Exception:
        pass
    for group in (cfg.get("bad_words_ids") or []):
        for t in (group or []):
            bad.add(int(t))
    if cfg.get("forced_eos_token_id") is not None and forced_eos is None:
        forced_eos = int(cfg["forced_eos_token_id"])
    # pad 不该作为输出内容；但**不要**禁用 bos ——
    # Marian 系模型常常 bos_token_id == eos_token_id（都是 0），
    # 把 bos 禁掉会连带把 EOS 禁掉，解码就永远停不下来。
    if cfg.get("pad_token_id") is not None:
        bad.add(int(cfg["pad_token_id"]))
    # EOS 永远不能禁用
    for key in ("eos_token_id",):
        if cfg.get(key) is not None:
            bad.discard(int(cfg[key]))
    return bad, forced_eos


def _pick_next(logits_row, bad):
    """在禁用 token 之外取 argmax。"""
    if not bad:
        return int(np.argmax(logits_row))
    masked = np.array(logits_row, copy=True)
    for t in bad:
        if 0 <= t < masked.shape[0]:
            masked[t] = -np.inf
    return int(np.argmax(masked))


# ---------------------------------------------------------------- 多语种模型
# m2m100 用 ISO-639-1（两位）；NLLB 用 FLORES-200 代码
_M2M_CODES = {
    "zh": "zh", "en": "en", "ja": "ja", "ko": "ko", "ru": "ru",
    "de": "de", "fr": "fr", "es": "es", "it": "it", "pt": "pt", "nl": "nl",
}
_NLLB_CODES = {
    "zh": "zho_Hans", "en": "eng_Latn", "ja": "jpn_Jpan", "ko": "kor_Hang",
    "ru": "rus_Cyrl", "de": "deu_Latn", "fr": "fra_Latn", "es": "spa_Latn",
    "it": "ita_Latn", "pt": "por_Latn", "nl": "nld_Latn",
}


def _lang_pair(arch, src_lang, tgt_lang, meta):
    """把内部两位语言代码转成该模型要求的写法。"""
    table = _NLLB_CODES if arch == "nllb" else _M2M_CODES
    src = table.get((src_lang or "").lower(), src_lang or "")
    tgt = table.get((tgt_lang or "").lower(), tgt_lang or "")
    # 模型可自带覆盖表（meta.json 的 lang_map）
    override = meta.get("lang_map") or {}
    if src_lang and src_lang.lower() in override:
        src = override[src_lang.lower()]
    if tgt_lang and tgt_lang.lower() in override:
        tgt = override[tgt_lang.lower()]
    return src, tgt


def _lang_token_id(arch, tokenizer, code):
    """取目标语言 token 的 id（作为解码起点）。"""
    if not code:
        return None
    try:
        if arch == "m2m100" and hasattr(tokenizer, "get_lang_id"):
            return int(tokenizer.get_lang_id(code))
        if arch == "nllb" and hasattr(tokenizer, "convert_tokens_to_ids"):
            tid = tokenizer.convert_tokens_to_ids(code)
            if tid is not None and tid >= 0:
                return int(tid)
        if hasattr(tokenizer, "get_lang_id"):
            return int(tokenizer.get_lang_id(code))
    except Exception as e:
        print(f"  [多语种] 取语言 token 失败 {code}: {e}", flush=True)
    return None


def translate_static(enc_c, dec_c, tokenizer, text, max_new_tokens=MAX_NEW_TOKENS,
                     model_dir=None, src_lang=None, tgt_lang=None):
    cfg = _cfg(model_dir)
    pad_id = cfg.get("pad_token_id", 65000)
    dec_start = cfg.get("decoder_start_token_id", 65000)
    eos_id = cfg.get("eos_token_id", 0)
    bad, _forced_eos = _gen_constraints(cfg, model_dir)

    # 多语种模型（m2m100 / NLLB）：需要先设源语言，并把目标语言 token 作为解码起点
    forced_first = None
    meta = _read_meta(model_dir)
    arch = (meta.get("arch") or "").lower()
    if arch in ("m2m100", "nllb") and tokenizer is not None:
        src_code, tgt_code = _lang_pair(arch, src_lang, tgt_lang, meta)
        try:
            if src_code and hasattr(tokenizer, "src_lang"):
                tokenizer.src_lang = src_code
        except Exception:
            pass
        forced_first = _lang_token_id(arch, tokenizer, tgt_code)
        if tokenizer.pad_token_id is not None:
            pad_id = tokenizer.pad_token_id
        if tokenizer.eos_token_id is not None:
            eos_id = tokenizer.eos_token_id

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
        if forced_first is not None and not out_ids:
            # 多语种模型：第一个输出固定为目标语言 token（等价 HF 的 forced_bos_token_id）
            nxt = int(forced_first)
        else:
            nxt = _pick_next(logits[0, min(n, dec_len) - 1], bad)
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
    _dyn_bad, _ = _gen_constraints(cfg, model_dir)

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
        nxt = _pick_next(logits[0, -1], _dyn_bad)
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



