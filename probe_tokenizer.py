#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""确认 MarianTokenizer 特殊 token id 与 padding 行为。"""
import os
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
from transformers import AutoTokenizer

MD = os.path.join(os.path.dirname(__file__), "models", "opus-mt-zh-en-ov")
tok = AutoTokenizer.from_pretrained(MD)
print("pad_token_id      =", tok.pad_token_id, repr(tok.pad_token))
print("eos_token_id      =", tok.eos_token_id, repr(tok.eos_token))
print("bos_token_id      =", tok.bos_token_id, repr(tok.bos_token))
print("unk_token_id      =", tok.unk_token_id, repr(tok.unk_token))
print("model_max_length  =", tok.model_max_length)
print("vocab_size        =", tok.vocab_size)

text = "今天天气真好，我们一起去公园散步吧。"
ids = tok(text)["input_ids"]
print("\n原始 ids (%d):" % len(ids), ids)
print("decode 原文      :", tok.decode(ids))
print("decode skip_special:", tok.decode(ids, skip_special_tokens=True))
# 反查几个关键 id 的 token 串
for tid in (0, 1, 65000, 58100):
    try:
        print(f"  id {tid} -> {tok.convert_ids_to_tokens(tid)!r}")
    except Exception as e:
        print(f"  id {tid} -> ERR {e}")
