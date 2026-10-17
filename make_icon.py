#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成应用图标 app.ico（多尺寸）。用 Pillow 绘制，无需外部素材。"""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app.ico")

# 画布放大再缩小，获得抗锯齿
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 圆角方形 + 对角渐变
r = int(S * 0.22)
grad = Image.new("RGB", (S, S))
gd = ImageDraw.Draw(grad)
c1 = (32, 92, 190)    # 深蓝
c2 = (86, 60, 200)    # 紫
for y in range(S):
    t = y / (S - 1)
    gd.line([(0, y), (S, y)], fill=(
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    ))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=r, fill=255)
img.paste(grad, (0, 0), mask)

# 白色「译」字
font = None
for path in (
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
):
    if os.path.exists(path):
        try:
            font = ImageFont.truetype(path, int(S * 0.52))
            break
        except Exception:
            pass
if font is None:
    font = ImageFont.load_default()

text = "译"
bbox = d.textbbox((0, 0), text, font=font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
d.text(((S - tw) / 2 - bbox[0], (S - th) / 2 - bbox[1]), text,
       font=font, fill=(255, 255, 255, 235))

# 底部一条强调线，增加辨识度
d.rounded_rectangle([int(S * 0.30), int(S * 0.80), int(S * 0.70), int(S * 0.845)],
                    radius=int(S * 0.022), fill=(120, 220, 255, 220))

sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
img.resize((256, 256), Image.LANCZOS).save(OUT, format="ICO",
                                          sizes=[(w, h) for w, h in sizes])
print("saved:", OUT, os.path.getsize(OUT), "bytes")
