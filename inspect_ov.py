#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快速查看导出的 OpenVINO IR 输入形状，帮助确定 NPU reshape 策略。"""
import os, sys
import openvino as ov

MODEL = os.path.join(os.path.dirname(__file__), "models", "opus-mt-zh-en")

def show(name, xml):
    p = os.path.join(MODEL, xml)
    if not os.path.exists(p):
        print(f"[{name}] 文件不存在: {xml}")
        return
    m = ov.Core().read_model(p)
    print(f"\n=== {name} ({xml}) ===")
    for i in m.inputs:
        print(f"  in  {i.get_any_name():30s} {i.get_partial_shape()}")
    for o in m.outputs:
        print(f"  out {o.get_any_name():30s} {o.get_partial_shape()}")

show("encoder", "openvino_encoder_model.xml")
show("decoder", "openvino_decoder_model.xml")
show("decoder_with_past", "openvino_decoder_with_past_model.xml")

