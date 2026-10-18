#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把 OpenVINO IR 的权重压缩成 FP16（体积减半）。

为什么安全：Intel NPU 本来就按 FP16 计算，OpenVINO 在 NPU 上会以 FP16 执行；
FP32→FP16 只损失约 1e-3 相对精度，实测译文与 FP32 完全一致。

用法：
    python compress_model.py <模型目录>            # 原地压缩
    python compress_model.py <源目录> <目标目录>    # 输出到新目录
"""
import gc
import os
import shutil
import sys


def _size(path):
    try:
        return os.path.getsize(path) / (1024 * 1024)
    except OSError:
        return 0.0


def compress(src, dst=None):
    import openvino as ov

    dst = dst or src
    in_place = os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst))
    os.makedirs(dst, exist_ok=True)
    core = ov.Core()

    # 原地压缩必须先写临时目录：直接覆写正在读取的 .bin 会让 OpenVINO 报
    # "Can't open bin file"（读写同一文件冲突）。
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="nput-fp16-") if in_place else dst

    before = after = 0.0
    converted = []
    try:
        for name in ("openvino_encoder_model", "openvino_decoder_model"):
            xml = os.path.join(src, name + ".xml")
            if not os.path.exists(xml):
                continue
            model = core.read_model(xml)
            out_xml = os.path.join(tmpdir, name + ".xml")
            ov.save_model(model, out_xml, compress_to_fp16=True)
            before += _size(os.path.join(src, name + ".bin"))
            after += _size(os.path.join(tmpdir, name + ".bin"))
            converted.append(name)
            # 关键：OpenVINO 的 IR 模型会持有 .bin 的文件句柄，
            # 不释放就替换原文件会报 PermissionError。显式丢弃并回收。
            del model
            gc.collect()

        if in_place:
            gc.collect()
            # 用压缩结果替换原文件
            for f in os.listdir(tmpdir):
                shutil.move(os.path.join(tmpdir, f), os.path.join(src, f))
        else:
            # 其余文件（config / tokenizer / spm / vocab …）原样复制
            for f in os.listdir(src):
                if f.startswith("openvino_"):
                    continue
                p = os.path.join(src, f)
                if os.path.isfile(p):
                    shutil.copy2(p, os.path.join(dst, f))
    finally:
        if in_place:
            try:
                import shutil as _sh
                _sh.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass

    print("  已转换: %s" % (", ".join(converted) or "无"))
    print("  权重: %.0f MB -> %.0f MB（省 %.0f MB）" % (before, after, before - after))
    return before, after


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else None
    if not os.path.isdir(src):
        print("目录不存在: %s" % src)
        sys.exit(2)
    compress(src, dst)

