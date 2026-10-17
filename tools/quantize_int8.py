#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用 NNCF 把 OpenVINO IR 量化成 INT8（体积约为 FP32 的 1/4）。

需要 nncf（只在生成发行包时用，运行时不依赖）。
校准数据：中文短句经 tokenizer 编码后喂给 encoder，再把 encoder 输出作为
decoder 的 encoder_hidden_states，这样 decoder 的量化范围是真实的。

用法：python quantize_int8.py <FP32模型目录> <输出目录> [seq_len]
"""
import json
import os
import sys

import numpy as np
import openvino as ov

CALIB_SENTENCES = [
    "今天天气真好，我们一起去公园散步吧。",
    "请帮我把这份文件翻译成英文。",
    "会议室在三楼，电梯出来左转。",
    "我的电脑昨天突然开不了机了。",
    "请问最近的地铁站在哪里？",
    "今天的会议改到下午三点。",
    "我想订一张下周一去上海的机票。",
    "这个项目的进度比预期慢了一些。",
    "晚饭我们吃火锅怎么样？",
    "外面下雨了，记得带伞。",
    "谢谢你帮我修好了打印机。",
    "明天早上八点我来接你。",
    "这份报告需要老板签字。",
    "咖啡机在茶水间，自己拿杯子。",
    "密码又忘了，只能重置了。",
    "仓库的库存昨天盘点完了。",
    "周末一起去爬山吗？",
    "空调好像坏了，一点也不凉。",
    "请在周五之前提交代码。",
    "他的中文说得很流利。",
    "这件事我需要再考虑一下。",
    "系统正在升级，请稍后再试。",
    "他昨天加班到很晚才回家。",
    "这家餐厅的味道不错，价格也合理。",
    "麻烦你把窗户关一下，有点冷。",
    "我们下个月准备搬新办公室。",
    "这个功能还在测试阶段，可能有 bug。",
    "请确保所有文件都已经备份。",
    "客户希望本周内拿到方案。",
    "他花了一整天时间才修好这个问题。",
]


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    import nncf
    from transformers import MarianTokenizer

    src, dst = sys.argv[1], sys.argv[2]
    seq = int(sys.argv[3]) if len(sys.argv) > 3 else 64
    os.makedirs(dst, exist_ok=True)

    tok = MarianTokenizer(
        vocab=os.path.join(src, "vocab.json"),
        source_spm=os.path.join(src, "source.spm"),
        target_spm=os.path.join(src, "target.spm"),
    )

    core = ov.Core()
    enc = core.read_model(os.path.join(src, "openvino_encoder_model.xml"))
    dec = core.read_model(os.path.join(src, "openvino_decoder_model.xml"))
    enc.reshape({"input_ids": ov.PartialShape([1, seq]),
                 "attention_mask": ov.PartialShape([1, seq])})
    dec.reshape({"encoder_attention_mask": ov.PartialShape([1, seq]),
                 "input_ids": ov.PartialShape([1, seq]),
                 "encoder_hidden_states": ov.PartialShape([1, seq, 512])})

    enc_cpu = core.compile_model(enc, "CPU")

    enc_samples, dec_samples = [], []
    for s in CALIB_SENTENCES:
        ids = tok(s)["input_ids"][:seq]
        arr = np.zeros((1, seq), dtype=np.int64)
        mask = np.zeros((1, seq), dtype=np.int64)
        arr[0, :len(ids)] = ids
        mask[0, :len(ids)] = 1
        hidden = enc_cpu({"input_ids": arr, "attention_mask": mask})["last_hidden_state"]
        enc_samples.append({"input_ids": arr, "attention_mask": mask})
        dec_samples.append({"encoder_attention_mask": mask,
                            "input_ids": arr,
                            "encoder_hidden_states": np.asarray(hidden)})

    print("校准样本: %d（seq=%d）" % (len(enc_samples), seq))

    def tf_enc(d):
        return {"input_ids": d["input_ids"], "attention_mask": d["attention_mask"]}

    def tf_dec(d):
        return {"encoder_attention_mask": d["encoder_attention_mask"],
                "input_ids": d["input_ids"],
                "encoder_hidden_states": d["encoder_hidden_states"]}

    print("量化 encoder …", flush=True)
    q_enc = nncf.quantize(enc, nncf.Dataset(enc_samples, tf_enc),
                          subset_size=len(enc_samples), preset=nncf.QuantizationPreset.MIXED)
    ov.save_model(q_enc, os.path.join(dst, "openvino_encoder_model.xml"),
                  compress_to_fp16=False)

    print("量化 decoder …", flush=True)
    q_dec = nncf.quantize(dec, nncf.Dataset(dec_samples, tf_dec),
                          subset_size=len(dec_samples), preset=nncf.QuantizationPreset.MIXED)
    ov.save_model(q_dec, os.path.join(dst, "openvino_decoder_model.xml"),
                  compress_to_fp16=False)

    import shutil
    for f in os.listdir(src):
        if f.startswith("openvino_") or f.endswith(".zip"):
            continue
        p = os.path.join(src, f)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(dst, f))

    total = sum(os.path.getsize(os.path.join(dst, f))
                for f in os.listdir(dst) if os.path.isfile(os.path.join(dst, f)))
    print("INT8 完成，目录共 %.0f MB" % (total / (1024 * 1024)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
